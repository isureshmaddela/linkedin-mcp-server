"""Subscriptions: trial, checkout, upgrade, downgrade, cancellation, dunning and webhooks.

States: ``trialing``, ``active``, ``past_due``, ``read_only``, ``cancelled``, ``deleted``.
Every transition is written to the subscription event table and to the organization's audit
log with who or what caused it (a person, a webhook, the scheduler). Webhooks are verified,
processed once (``billing_events`` is keyed by the provider's event id) and take effect
synchronously, so an organization is active the moment its payment webhook is processed.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
import threading
import time as _time
from typing import TYPE_CHECKING, Any

from aira import apps as app_catalog
from monosage_cloud import crypto
from monosage_cloud.billing_providers import (
    BillingProvider,
    RazorpayProvider,
    StandInProvider,
    StripeProvider,
)
from monosage_cloud.config import DAY
from monosage_cloud.db import dumps, loads, many, new_id, one, platform_scope
from monosage_cloud.errors import (
    AppDisabled,
    Conflict,
    Forbidden,
    NotFound,
    PlanRequired,
    SubscriptionInactive,
    Unavailable,
    ValidationFailed,
)
from monosage_cloud.plans import (
    APP_PRICING,
    COACH_SCOPES,
    LIMIT_NAMES,
    TRIAL,
    is_free,
    with_later_rates,
)
from monosage_cloud.rbac import BILLING, OWNER, Principal

if TYPE_CHECKING:
    from monosage_cloud.app import CloudApp

logger = logging.getLogger("monosage_cloud.billing")

PERIOD_SECONDS = 30 * DAY
TRANSITIONS: dict[str, set[str]] = {
    "trialing": {"active", "cancelled", "deleted"},
    "active": {"past_due", "cancelled", "deleted"},
    "past_due": {"active", "read_only", "cancelled", "deleted"},
    "read_only": {"active", "cancelled", "deleted"},
    "cancelled": {"active", "trialing", "deleted"},
    "deleted": set(),
}
# The plans an owner buys through checkout are the catalog's self-serve ones (``PlanCatalog.self_serve``): the
# built-in Starter and Team, and any public, priced plan the superadmin creates.
SIGNUP_KEY = "signup_plan"  # cloud_kv: the plan a new sign-up starts on, set from the platform console

CUSTOM_LIMIT_NAMES = LIMIT_NAMES

# Metered overage: the usage metric, the plan's rate for it and how an invoice line names it.
OVERAGE_METRICS: dict[str, tuple[str, str]] = {
    "applications:overage": ("application_cents", "applications"),
    "searches:overage": ("search_cents", "searches"),
    "cover_letters:overage": ("cover_letter_cents", "AI cover letters"),
    "api_searches:overage": ("api_search_cents", "API searches"),
    "pooled_searches:overage": ("pooled_search_cents", "pooled SerpApi searches"),
}
OVERAGE_CLAIM_SECONDS = (
    3600  # a claim older than this was left by a tick that died: it may be taken again
)
OVERAGE_MAX_ATTEMPTS = 10
UPGRADE_PENDING_SECONDS = DAY  # Stripe drops an unpaid pending update after 23 hours
# Neutral events that change the subscription's state: one older than the newest applied is ignored.
ORDERED_EVENTS = frozenset(
    {
        "invoice.paid",
        "invoice.payment_failed",
        "subscription.updated",
        "subscription.deleted",
    }
)


class StandInRefused(RuntimeError):
    """The stand-in billing provider was asked to run where the public could reach it."""


def _day(timestamp: int | None) -> str:
    return _time.strftime("%Y-%m-%d", _time.gmtime(int(timestamp or 0)))


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
        config = app.config
        refusal = config.standin_refusal()
        if refusal and not config.allow_standin_billing:
            raise StandInRefused(refusal)
        if config.billing_provider == "standin":
            if refusal:
                logger.warning(
                    "!!! DEMO BILLING: the stand-in provider is on at %s (AIRA_CLOUD_ALLOW_STANDIN_BILLING). "
                    "Anyone who can reach this server can activate paid plans without paying. !!!",
                    config.base_url,
                )
            from monosage_cloud.routes.billing import register_standin_routes

            register_standin_routes()
        secret = self._standin_secret()
        # The stand-in is kept for the scheduler's own renewals and for tests; its webhook route and hosted pages
        # answer only when it is the configured provider.
        self.standin = StandInProvider(app.db, app.clock, config, secret)
        self.providers["standin"] = self.standin
        if config.stripe_secret_key or config.billing_provider == "stripe":
            self.providers["stripe"] = StripeProvider(config, app.clock, app.db)
        if config.razorpay_key_id or config.billing_provider == "razorpay":
            self.providers["razorpay"] = RazorpayProvider(config, app.clock, app.db)
        self._payout_lock = threading.Lock()
        self._payout_fernet: Any = None
        self._install_payout_key()

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

    # -- creators' payout details: encrypted with a key the KMS wraps ------------------------

    def _install_payout_key(self) -> None:
        """Creators' bank and UPI details live in the platform-wide Videos database; hosted, they are encrypted
        with a data key wrapped by the control plane's master key (the KMS), never stored in the clear."""
        from aira.suite.videos import store as videos_store

        videos_store.set_payout_key(
            self.app.config.data_root / "videos.sqlite3", self._payout_cipher
        )

    def _payout_cipher(self) -> Any:
        from cryptography.fernet import Fernet

        with self._payout_lock:
            if self._payout_fernet is not None:
                return self._payout_fernet
            provider = self.app.secrets.provider
            with self.db.write() as connection:
                row = one(
                    connection,
                    "SELECT value FROM cloud_kv WHERE key = 'payout_data_key'",
                )
                if row:
                    key = provider.unwrap(base64.b64decode(str(row["value"])))
                else:
                    key = Fernet.generate_key()
                    connection.execute(
                        "INSERT INTO cloud_kv (key, value) VALUES ('payout_data_key', ?)",
                        (base64.b64encode(provider.wrap(key)).decode("ascii"),),
                    )
            self._payout_fernet = Fernet(key)
            return self._payout_fernet

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

    def signup_default(self) -> dict[str, Any]:
        """The plan a new organization starts on and for how many trial days (0: no trial, the plan stays until
        someone changes it). The superadmin sets it; until then it is the configured default (All access)."""
        with self.db.read() as connection:
            row = one(
                connection, "SELECT value FROM cloud_kv WHERE key = ?", (SIGNUP_KEY,)
            )
        stored = loads(row["value"], {}) if row else {}
        plan_id = str(stored.get("plan_id") or self.app.config.signup_plan)
        if plan_id == TRIAL and not stored:
            days = int(self.app.config.trial_days)
        else:
            days = int(stored.get("trial_days", self.app.config.signup_trial_days) or 0)
        try:
            plan = self.app.catalog.get(plan_id)
        except NotFound:
            plan = None
        if plan is None or plan["archived"]:
            # the chosen plan is gone: fall back to the shipped default, then to the trial
            plan_id, days = (
                self.app.config.signup_plan,
                int(self.app.config.signup_trial_days),
            )
            try:
                plan = self.app.catalog.get(plan_id)
            except NotFound:
                plan_id, days = TRIAL, int(self.app.config.trial_days)
                plan = self.app.catalog.get(TRIAL)
        if plan_id == TRIAL and days < 1:
            days = int(self.app.config.trial_days)
        return {
            "plan_id": plan_id,
            "plan_name": plan["name"],
            "trial_days": days,
            "set_by": stored.get("set_by", ""),
            "set_at": stored.get("set_at"),
            "configured": bool(stored),
        }

    def set_signup_default(
        self, operator: Principal, plan_id: str, trial_days: Any = None
    ) -> dict[str, Any]:
        """The superadmin chooses what new sign-ups get. Existing organizations keep their plans.

        ``trial_days`` 0 starts the organization on the plan with no end (only a free plan: nobody pays for it);
        1 to 365 starts it as a trial of the plan that ends like any trial unless a plan is bought. Left out, it
        is the plan's own trial length (the trial: the configured 14 days).
        """
        self.app.authz.require(operator, "platform.plans.manage")
        plan_id = str(plan_id or "").strip()
        plan = self.app.catalog.get(plan_id)
        if plan["archived"]:
            raise ValidationFailed("That plan is archived. Restore it first.")
        if trial_days is None or trial_days == "":
            trial_days = (
                self.app.config.trial_days
                if plan_id == TRIAL
                else int(plan["pricing"].get("trial_days") or 0)
            )
        if (
            isinstance(trial_days, bool)
            or not isinstance(trial_days, int)
            or not 0 <= trial_days <= 365
        ):
            raise ValidationFailed("Trial days are a whole number from 0 to 365.")
        if trial_days == 0 and (plan_id == TRIAL or not is_free(plan["pricing"])):
            raise ValidationFailed(
                "A sign-up can start without a trial only on a free plan. Give this plan trial days, or choose a free plan (such as All access).",
                code="trial_required",
            )
        before = self.signup_default()
        value = {
            "plan_id": plan_id,
            "trial_days": trial_days,
            "set_by": operator.operator_id,
            "set_at": self.now,
        }
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SIGNUP_KEY, dumps(value)),
            )
        self.app.platform_audit.record(
            "plan.signup_default",
            operator=operator,
            target_type="plan",
            target_id=plan_id,
            detail={
                "from": {"plan": before["plan_id"], "trial_days": before["trial_days"]},
                "to": {"plan": plan_id, "trial_days": trial_days},
            },
        )
        return self.signup_default()

    def start_subscription(
        self, org_id: str, connection: sqlite3.Connection
    ) -> dict[str, Any]:
        """A new organization's subscription: the sign-up default, as a trial or (a free plan) with no end."""
        default = self.signup_default()
        plan = self.app.catalog.get(default["plan_id"])
        now = self.now
        days = int(default["trial_days"])
        seats = plan["entitlements"].get("candidate_seats") or 0
        if days > 0:
            status, trial_end = "trialing", now + days * DAY
            period_end: int = trial_end
        else:
            status, trial_end, period_end = "active", None, now + PERIOD_SECONDS
        connection.execute(
            """INSERT INTO subscriptions (org_id, plan_id, plan_version, status, candidate_seats, period_start, period_end, trial_end, updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                org_id,
                plan["plan_id"],
                plan["version"],
                status,
                seats,
                now,
                period_end,
                trial_end,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO subscription_events (org_id, from_status, to_status, reason, trigger_type, trigger_id, at) VALUES (?, '', ?, 'signup', 'person', '', ?)",
            (org_id, status, now),
        )
        return {
            "plan_id": plan["plan_id"],
            "status": status,
            "trial_days": days,
            "trial_end": trial_end,
        }

    def create_trial(self, org_id: str, connection: sqlite3.Connection) -> None:
        """Kept for callers that predate the sign-up default: starts whatever the default is."""
        self.start_subscription(org_id, connection)

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
        """The plans on the billing page, in the superadmin's order (archived and private plans are left out)."""
        out = []
        self_serve = set(self.app.catalog.self_serve())
        for plan in self.app.catalog.current().values():
            if not plan["public"]:
                continue
            ent, pricing = plan["entitlements"], plan["pricing"]
            out.append(
                {
                    "plan_id": plan["plan_id"],
                    "name": plan["name"],
                    "description": plan.get("description", ""),
                    "self_serve": plan["plan_id"] in self_serve,
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
        self_serve = self.app.catalog.self_serve()
        if plan_id not in self_serve:
            try:
                wanted = self.app.catalog.get(plan_id)
            except NotFound:
                wanted = None
            if (
                wanted
                and not wanted["archived"]
                and wanted["pricing"].get("contact_sales")
            ):
                raise ValidationFailed(
                    f"{wanted['name']} is arranged with our sales team; it is activated for you.",
                    code="contact_sales",
                )
            names = [self.app.catalog.name(p) for p in self_serve]
            raise ValidationFailed(
                "Choose "
                + (
                    " or ".join(names)
                    if len(names) <= 2
                    else ", ".join(names[:-1]) + " or " + names[-1]
                )
                + "."
                if names
                else "No plan can be bought here right now. Contact sales."
            )
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
                    for p in self_serve
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
                    f" Choose {self.app.catalog.name(larger)} for {seats}."
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
            else [
                app_catalog.current_id(a)
                for a in apps
                if app_catalog.current_id(a) in app_catalog.BY_ID
            ]
        )
        kept = [a for a in wanted if self.app.app_settings.state(plan_id, a) == "addon"]
        dropped = [
            a
            for a in wanted
            if self.app.app_settings.state(plan_id, a) == "unavailable"
        ]
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
        if subscription["status"] == "active" and not self.checkout_required(
            subscription
        ):
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
            checkout_id=new_id(),
        )
        # One open checkout per organization: this one replaces any other, which is closed at the provider so it
        # cannot be paid as well (a completion that still arrives for it is handled in apply_event).
        with self.db.write() as connection:
            replaced = many(
                connection,
                "SELECT session_id, provider FROM billing_checkouts WHERE org_id = ? AND status = 'open'",
                (actor.org_id,),
            )
            connection.execute(
                "UPDATE billing_checkouts SET status = 'superseded', closed_at = ? WHERE org_id = ? AND status = 'open'",
                (self.now, actor.org_id),
            )
            connection.execute(
                "INSERT INTO billing_checkouts (session_id, org_id, provider, plan_id, seats, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (
                    str(session["id"]),
                    actor.org_id,
                    provider.name,
                    plan_id,
                    int(quote["seats"]),
                    self.now,
                ),
            )
        self._expire_checkouts(replaced)
        self.app.audit.record(
            actor.org_id,
            "billing.checkout_start",
            actor=actor,
            detail={
                "plan": plan_id,
                "seats": quote["seats"],
                "replaced": len(replaced),
            },
        )
        return {
            "checkout_url": session["url"],
            "session_id": session["id"],
            "quote": quote,
        }

    def _expire_checkouts(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            provider = self.providers.get(str(row.get("provider") or ""))
            if provider is None:
                continue
            try:
                provider.expire_checkout(str(row["session_id"]))
            except Exception:  # noqa: BLE001 - already completed or expired there; a late completion is handled
                logger.warning(
                    "Could not expire checkout %s at %s",
                    row["session_id"],
                    provider.name,
                )

    def checkout_required(self, subscription: dict[str, Any]) -> bool:
        """A paid plan has to be bought through checkout first: in a trial, after cancellation, and on a free plan
        that no provider subscription stands behind (All access, a free plan the superadmin made)."""
        if subscription["status"] != "active":
            return True
        if subscription["billing_subscription_id"]:
            return False
        plan = self.app.catalog.get(
            subscription["plan_id"], subscription["plan_version"]
        )
        return is_free(plan["pricing"])

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
        if subscription["status"] != "active" or self.checkout_required(subscription):
            raise Conflict(
                "Choose a plan through checkout first.", code="not_subscribed"
            )
        target_seats = (
            int(seats) if seats is not None else subscription["candidate_seats"]
        )
        # A downgrade may go below current usage: it applies at period end and automation pauses until within limits.
        quote = self.quote(actor.org_id, plan_id, target_seats, enforce_usage=False)
        order = self.app.catalog.order(
            include_archived=True
        )  # the superadmin's order ranks the plans
        current_rank = (
            order.index(subscription["plan_id"])
            if subscription["plan_id"] in order
            else 0
        )
        target_rank = order.index(plan_id)
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
        charge = int(quote.get("prorated_charge_cents", 0))
        upgrade: dict[str, Any] = {
            "kind": "plan",
            "id": new_id(),
            "plan_id": plan_id,
            "plan_version": plan["version"],
            "seats": target_seats,
            "from_plan": subscription["plan_id"],
            "from_seats": subscription["candidate_seats"],
            "dropped": list(quote["dropped_apps"]),
            "charge_cents": charge,
            "currency": quote["currency"],
            "requested_at": self.now,
            "requested_by": actor.email,
            "invoice_row": new_id(),
            "invoice_id": "",
            "match": {},
            "provider": "",
        }
        lines = [
            {
                "description": f"Upgrade to {plan['name']} ({target_seats} seats), prorated",
                "amount_cents": charge,
            }
        ]
        if not subscription["billing_subscription_id"]:
            # no provider subscription to confirm a payment (a plan an operator set up, invoiced outside): the
            # upgrade applies now and its prorated charge waits on the next invoice, open
            self._record_upgrade_invoice(actor.org_id, upgrade, lines, subscription)
            self._apply_upgrade(actor.org_id, upgrade, actor=actor, paid=False)
            return {
                "scheduled": False,
                "applied": True,
                "pending_payment": False,
                "prorated_charge_cents": charge,
                "dropped_apps": quote["dropped_apps"],
                "quote": quote,
            }
        if subscription.get("pending_upgrade"):
            raise Conflict(
                "An upgrade is already waiting for its payment. It applies once the payment goes through.",
                code="upgrade_pending",
            )
        provider = self._provider_for(subscription)
        answer = provider.update_subscription(
            subscription["billing_subscription_id"],
            plan_id=plan_id,
            plan_name=plan["name"],
            seats=target_seats,
            seat_cents=int(plan["pricing"]["seat_cents_month"]),
            currency=plan["pricing"].get("currency", "usd"),
            prorate=True,
            addons=self._provider_addons(plan_id, target_seats, kept),
            confirm_payment=True,
        )
        upgrade.update(
            {
                "provider": provider.name,
                "invoice_id": str(answer.get("invoice_id") or ""),
                "match": dict(answer.get("match") or {}),
            }
        )
        if provider.name == "razorpay" and charge > 0:
            # Razorpay does not prorate: the difference for the rest of this period rides on the next charge
            try:
                upgrade["item_id"] = provider.add_invoice_item(
                    subscription["billing_customer_id"],
                    charge,
                    lines[0]["description"],
                    quote["currency"],
                    subscription_id=subscription["billing_subscription_id"],
                    idempotency_key=f"aira-upgrade-{upgrade['id']}",
                )
            except Exception:  # noqa: BLE001 - the plan change stands; reconcile reports the missing line
                logger.exception(
                    "Could not add the prorated upgrade charge for %s", actor.org_id
                )
        self._record_upgrade_invoice(actor.org_id, upgrade, lines, subscription)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET pending_upgrade = ?, updated_at = ? WHERE org_id = ?",
                (dumps(upgrade), self.now, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.upgrade_requested",
                actor=actor,
                detail={
                    "from": subscription["plan_id"],
                    "to": plan_id,
                    "seats": target_seats,
                    "prorated_cents": charge,
                    "provider": provider.name,
                },
                connection=connection,
            )
        if provider.name == "standin":
            # the stand-in's card always works: its invoice.paid confirms the upgrade, as a provider's would
            self._standin_event(
                "invoice.paid",
                {
                    "org_id": actor.org_id,
                    "subscription_id": subscription["billing_subscription_id"],
                    "customer_id": subscription["billing_customer_id"],
                    "invoice_id": upgrade["invoice_id"],
                    "amount_cents": charge,
                    "currency": quote["currency"],
                    "billing_reason": "subscription_update",
                },
            )
        applied = not self.subscription(actor.org_id)["pending_upgrade"]
        return {
            "scheduled": False,
            "applied": applied,
            "pending_payment": not applied,
            "prorated_charge_cents": charge,
            "dropped_apps": quote["dropped_apps"],
            "quote": quote,
        }

    def _standin_event(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        payload, signature = self.standin.make_event(event_type, data)
        return self.handle_webhook("standin", payload, signature)

    def _record_upgrade_invoice(
        self,
        org_id: str,
        upgrade: dict[str, Any],
        lines: list[dict[str, Any]],
        subscription: dict[str, Any],
    ) -> None:
        """The prorated charge of an upgrade, open until the provider says it was paid."""
        if not upgrade["charge_cents"]:
            return
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO invoices (invoice_id, org_id, provider_invoice_id, item_id, kind, amount_cents, currency, status, period_start, period_end, lines, created_at)
                VALUES (?, ?, ?, ?, 'proration', ?, ?, 'open', ?, ?, ?, ?)""",
                (
                    upgrade["invoice_row"],
                    org_id,
                    upgrade.get("invoice_id", ""),
                    upgrade.get("item_id", ""),
                    upgrade["charge_cents"],
                    upgrade["currency"],
                    self.now,
                    subscription["period_end"],
                    dumps(lines),
                    self.now,
                ),
            )

    def _apply_upgrade(
        self,
        org_id: str,
        upgrade: dict[str, Any],
        *,
        actor: Principal | None = None,
        paid: bool,
        trigger_id: str = "",
    ) -> bool:
        """Put the upgrade into effect, once: the plan, seats and limits change, apps the plan does not offer stop."""
        plan = self.app.catalog.get(upgrade["plan_id"])
        with self.db.write() as connection:
            current = one(
                connection,
                "SELECT pending_upgrade FROM subscriptions WHERE org_id = ?",
                (org_id,),
            )
            waiting = loads((current or {}).get("pending_upgrade"), None)
            if actor is None and (
                not waiting or waiting.get("id") != upgrade.get("id")
            ):
                return False  # already applied, or replaced by another change
            connection.execute(
                """UPDATE subscriptions SET plan_id = ?, plan_version = ?, candidate_seats = ?, pending_plan_id = '', pending_seats = NULL,
                pending_effective_at = NULL, pending_upgrade = '', updated_at = ? WHERE org_id = ?""",
                (
                    upgrade["plan_id"],
                    plan["version"],
                    int(upgrade["seats"]),
                    self.now,
                    org_id,
                ),
            )
            self._cancel_apps(
                connection,
                org_id,
                list(upgrade.get("dropped") or []),
                reason=f"not available on {plan['name']}",
                actor=actor,
            )
            if paid:
                connection.execute(
                    "UPDATE invoices SET status = 'paid', paid_at = ? WHERE org_id = ? AND invoice_id = ? AND status = 'open'",
                    (self.now, org_id, upgrade.get("invoice_row", "")),
                )
            self.app.audit.record(
                org_id,
                "billing.upgrade",
                actor=actor,
                actor_type="" if actor is not None else "webhook",
                actor_id="" if actor is not None else trigger_id,
                detail={
                    "from": upgrade.get("from_plan"),
                    "to": upgrade["plan_id"],
                    "seats": upgrade["seats"],
                    "prorated_cents": upgrade.get("charge_cents", 0),
                    "paid": paid,
                },
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        self.app.lifecycle.recompute_over_limits(org_id)
        if upgrade.get("dropped") and upgrade.get("provider") == "stripe":
            # a pending update cannot delete items: the dropped add-ons leave the provider's subscription now
            self._sync_provider(self.subscription(org_id))
        return True

    def _drop_upgrade(self, org_id: str, upgrade: dict[str, Any], reason: str) -> None:
        """The upgrade's payment failed or expired: the organization stays on its plan."""
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE subscriptions SET pending_upgrade = '', updated_at = ? WHERE org_id = ? AND pending_upgrade = ?",
                (self.now, org_id, dumps(upgrade)),
            ).rowcount
            if not changed:
                return
            connection.execute(
                "UPDATE invoices SET status = 'void' WHERE org_id = ? AND invoice_id = ? AND status = 'open'",
                (org_id, upgrade.get("invoice_row", "")),
            )
            self.app.audit.record(
                org_id,
                "billing.upgrade_failed",
                actor_type="system",
                detail={"to": upgrade.get("plan_id"), "reason": reason},
                connection=connection,
            )
        self.notice(
            org_id,
            "warning",
            "Upgrade not applied",
            f"The payment for the upgrade to {self.app.catalog.name(upgrade.get('plan_id', ''))} did not go through "
            f"({reason}). Nothing changed; update the payment method and try again.",
        )

    def _sync_provider(self, sub: dict[str, Any]) -> None:
        """Rewrite the provider's subscription to what we have, without proration (best effort)."""
        if not sub["billing_subscription_id"]:
            return
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
                    self.active_addons(sub["org_id"], sub["plan_id"]),
                ),
                at_cycle_end=False,
            )
        except Exception:  # noqa: BLE001 - keep our state moving; reconcile reports the drift
            logger.exception(
                "Could not sync the provider subscription for %s", sub["org_id"]
            )

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
            self._provider_for(subscription).cancel_subscription(
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
            "plan_free": is_free(plan["pricing"]),
            "checkout_required": self.checkout_required(subscription),
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
            # an upgrade waiting for the provider to confirm its payment
            "pending_upgrade": {
                key: value
                for key, value in loads(subscription.get("pending_upgrade"), {}).items()
                if key
                in {"plan_id", "seats", "charge_cents", "currency", "requested_at"}
            }
            or None,
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
        if any(row["app_id"] in app_catalog.LEGACY_IDS for row in rows):
            rows = self._move_legacy_apps(org_id, rows)
        return {row["app_id"]: row for row in rows}

    def _move_legacy_apps(
        self, org_id: str, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Subscriptions to apps that became sections of a bigger app (Shop and Bookings -> Commerce) move to the
        new app once; when two old apps meet, the best one is kept (paid, then granted, then cancelled)."""
        rank = {"disabled": 4, "active": 3, "granted": 2, "cancelled": 1}
        best: dict[str, dict[str, Any]] = {}
        for row in rows:
            new_id = app_catalog.current_id(row["app_id"])
            current = best.get(new_id)
            if current is None or rank.get(row["status"], 0) > rank.get(
                current["status"], 0
            ):
                best[new_id] = row
        with self.db.write() as connection:
            for new_id, row in best.items():
                olds = [
                    r["app_id"]
                    for r in rows
                    if app_catalog.current_id(r["app_id"]) == new_id
                ]
                if olds == [new_id]:
                    continue
                for old in olds:
                    connection.execute(
                        "DELETE FROM org_apps WHERE org_id = ? AND app_id = ?",
                        (org_id, old),
                    )
                connection.execute(
                    """INSERT INTO org_apps (org_id, app_id, status, seats_at_start, activated_at, cancelled_at, cancel_at_period_end, granted_by, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        org_id,
                        new_id,
                        row["status"],
                        row["seats_at_start"],
                        row["activated_at"],
                        row["cancelled_at"],
                        row["cancel_at_period_end"],
                        row["granted_by"],
                        row["note"],
                    ),
                )
        self.app.entitlements.invalidate(org_id)
        with self.db.read() as connection:
            return many(
                connection, "SELECT * FROM org_apps WHERE org_id = ?", (org_id,)
            )

    def active_addons(self, org_id: str, plan_id: str | None = None) -> list[str]:
        """App add-ons the organization pays for: bought rows that are add-ons on the plan (grants cost nothing)."""
        plan = plan_id or self.subscription(org_id)["plan_id"]
        rows = self._app_rows(org_id)
        return [
            app.id
            for app in app_catalog.APPS
            if rows.get(app.id, {}).get("status") == "active"
            and self.app.app_settings.state(plan, app.id) == "addon"
            and self.app.app_settings.enabled(
                app.id
            )  # switched off for everyone: not billed
        ]

    def _addon_lines(
        self, plan_id: str, seats: int, apps: list[str]
    ) -> list[dict[str, Any]]:
        lines = []
        for app_id in apps:
            cents = self.app.app_settings.price(plan_id, app_id) or 0
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
            state = self.app.app_settings.state(plan_id, app.id)
            row = rows.get(app.id)
            price = self.app.app_settings.price(plan_id, app.id)
            if not self.app.app_settings.enabled(app.id):
                status = "disabled"
            elif row and row["status"] == "disabled":
                status = "blocked"  # an operator switched it off for this organization
            elif row and row["status"] == "granted":
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
                    "category": app.category,
                    "industry": app.industry,
                    "highlights": list(app.highlights),
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
                    "required_plan": self.app.app_settings.plan_for(app.id)
                    if state == "unavailable"
                    else None,
                    "note": (row or {}).get("note", ""),
                }
            )
        return cards

    def apps_for_member(self, org_id: str, entitlements: Any) -> list[dict[str, Any]]:
        """What the app switcher needs: each app, whether this organization may open it, and why not."""
        out = []
        plan_name = self.app.catalog.name(entitlements.plan_id)
        blocked = {
            app_id
            for app_id, row in self._app_rows(org_id).items()
            if row["status"] == "disabled"
        }
        for app in app_catalog.APPS:
            enabled = entitlements.has_app(app.id)
            state = self.app.app_settings.state(entitlements.plan_id, app.id)
            reason = ""
            if not enabled:
                reason = (
                    "Switched off for now."
                    if not self.app.app_settings.enabled(app.id)
                    else "Switched off for your organization."
                    if app.id in blocked
                    else "Not turned on for your organization."
                    if state == "addon"
                    else f"Not available on the {plan_name} plan."
                )
            out.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "enabled": enabled,
                    "reason": reason,
                    "switched_off": not self.app.app_settings.enabled(app.id),
                }
            )
        return out

    def app_quote(self, org_id: str, app_id: str) -> dict[str, Any]:
        """What turning an app on costs now: the monthly line and the prorated charge for the rest of this period."""
        app_id = app_catalog.current_id(app_id)
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        subscription = self.subscription(org_id)
        plan_id = subscription["plan_id"]
        state = self.app.app_settings.state(plan_id, app_id)
        app = app_catalog.BY_ID[app_id]
        if not self.app.app_settings.enabled(app_id):
            raise AppDisabled(
                f"{app.name} is switched off for now and cannot be turned on.",
                app=app_id,
                name=app.name,
            )
        if self.app.entitlements.app_disabled_here(org_id, app_id):
            raise AppDisabled(
                f"{app.name} is switched off for your organization. Contact support to turn it back on.",
                app=app_id,
                name=app.name,
                scope="organization",
            )
        if state == "unavailable":
            needed = self.app.app_settings.plan_for(app_id)
            raise PlanRequired(
                f"{app.name} is not available on the {self.app.catalog.name(plan_id)} plan."
                + (
                    f" Upgrade to {self.app.catalog.name(needed)} to turn it on."
                    if needed
                    else ""
                ),
                feature=app.feature,
                required_plan=needed,
                current_plan=plan_id,
                app=app_id,
            )
        seats = int(subscription["candidate_seats"])
        price = self.app.app_settings.price(plan_id, app_id) or 0
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
        app_id = app_catalog.current_id(app_id)
        self.app.authz.require(actor, "billing.manage")
        subscription = self._require_active_for_apps(actor.org_id)
        quote = self.app_quote(actor.org_id, app_id)
        app = app_catalog.BY_ID[app_id]
        if quote["state"] == "included":
            raise Conflict(f"{app.name} is included in your plan.", code="included")
        if self.checkout_required(subscription):
            raise Conflict(
                "Choose a plan through checkout first; the trial includes every app."
                if subscription["plan_id"] == TRIAL
                else "Choose a plan through checkout first.",
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
                # the provider bills the prorated line with its next invoice: open until that invoice is paid
                connection.execute(
                    """INSERT INTO invoices (invoice_id, org_id, kind, amount_cents, currency, status, period_start, period_end, lines, created_at)
                    VALUES (?, ?, 'proration', ?, ?, 'open', ?, ?, ?, ?)""",
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

    def charge_ads_topup(self, actor: Principal, amount_cents: int) -> dict[str, Any]:
        """Put money on the organization's ad balance (the Ads app is prepaid).

        The amount is billed to the organization's own customer at the provider (Stripe: a one-off invoice paid at
        once; Razorpay: an add-on charged with the next cycle). The balance is credited only when the provider says
        the money arrived (``invoice.paid``), so campaigns and creators' earnings run on settled money only; a failed
        payment credits nothing and a refund takes the credit back. Aira never takes card details itself. A trial or
        an organization without a paid subscription has no customer at the provider to bill, so it is refused."""
        from aira.suite.ads import service as ads

        self.app.authz.require(actor, "billing.manage")
        self.app.entitlements.require_app(actor.org_id, "ads", audience="staff")
        try:
            amount = int(amount_cents)
        except (TypeError, ValueError):
            raise ValidationFailed("Send the amount in cents.") from None
        if not ads.MIN_TOPUP_CENTS <= amount <= ads.MAX_TOPUP_CENTS:
            raise ValidationFailed(
                f"A top-up is between ${ads.MIN_TOPUP_CENTS / 100:,.2f} and ${ads.MAX_TOPUP_CENTS / 100:,.2f}."
            )
        subscription = self._require_active_for_apps(actor.org_id)
        if (
            subscription["status"] != "active"
            or not subscription["billing_customer_id"]
        ):
            raise Conflict(
                "Choose a plan through checkout first: ad top-ups are billed with your subscription.",
                code="not_subscribed",
            )
        provider = self._provider_for(subscription)
        topup_id = new_id()
        charge = provider.charge_now(
            subscription["billing_customer_id"],
            amount,
            "Monosage Ads balance top-up",
            "usd",
            subscription_id=subscription["billing_subscription_id"],
            idempotency_key=f"aira-topup-{topup_id}",
            metadata={
                "org_id": actor.org_id,
                "kind": "ads_topup",
                "topup_id": topup_id,
            },
        )
        status = "scheduled" if charge.get("status") == "scheduled" else "open"
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO invoices (invoice_id, org_id, provider_invoice_id, item_id, kind, amount_cents, currency, status, lines, created_at)
                VALUES (?, ?, ?, ?, 'ads_topup', ?, 'usd', ?, ?, ?)""",
                (
                    topup_id,
                    actor.org_id,
                    str(charge.get("id") or ""),
                    str(charge.get("item_id") or ""),
                    amount,
                    status,
                    dumps(
                        [
                            {
                                "description": "Monosage Ads balance top-up",
                                "amount_cents": amount,
                                "app_id": "ads",
                            }
                        ]
                    ),
                    self.now,
                ),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.ads_topup",
                actor=actor,
                target_type="invoice_item",
                target_id=str(charge.get("item_id") or charge.get("id") or ""),
                detail={
                    "amount_cents": amount,
                    "provider": provider.name,
                    "status": status,
                },
                connection=connection,
            )
        if provider.name == "standin":
            # the stand-in's card always works: its invoice.paid credits the balance, as a provider's would
            self._standin_event(
                "invoice.paid",
                {
                    "org_id": actor.org_id,
                    "customer_id": subscription["billing_customer_id"],
                    "invoice_id": str(charge["id"]),
                    "amount_cents": amount,
                    "currency": "usd",
                    "kind": "ads_topup",
                },
            )
        elif charge.get("status") == "paid":
            # the provider's API answered that the invoice is paid: the money is settled (its webhook is a no-op)
            self._settle_topups(
                actor.org_id, {"invoice_id": str(charge["id"]), "payment_id": ""}
            )
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT status FROM invoices WHERE invoice_id = ?",
                (topup_id,),
            )
        from contextlib import closing

        from aira.suite.videos import db as videos_db

        with closing(videos_db.connect()) as sc:
            account = ads.account(sc, actor.org_id)
        settled = (row or {}).get("status") == "paid"
        return {
            "amount_cents": amount,
            "invoice_item": str(charge.get("item_id") or charge.get("id") or ""),
            "topup_id": topup_id,
            "status": (row or {}).get("status", status),
            "credited": settled,
            "balance_micros": int(account.get("balance_micros") or 0),
        }

    # -- ad top-ups: credited on settled money only ----------------------------------------

    def _topup_rows(
        self, org_id: str, where: str, params: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            return many(
                connection,
                f"SELECT * FROM invoices WHERE org_id = ? AND kind = 'ads_topup' AND {where} ORDER BY created_at",
                (org_id, *params),
            )

    def _settle_topups(self, org_id: str, neutral: dict[str, Any]) -> int:
        """``invoice.paid``: credit the ad balance for the top-ups that invoice paid. A top-up billed as a Razorpay
        add-on is paid by the subscription charge that carried it (the add-ons made before it)."""
        invoice_id = str(neutral.get("invoice_id") or "")
        rows = (
            self._topup_rows(
                # a failed attempt can be followed by a successful retry of the same invoice: it counts then
                org_id,
                "status IN ('open', 'scheduled', 'failed') AND provider_invoice_id = ?",
                (invoice_id,),
            )
            if invoice_id
            else []
        )
        addon_ids = [a for a in neutral.get("addon_ids") or [] if a]
        if addon_ids:
            rows += self._topup_rows(
                org_id,
                f"status = 'scheduled' AND item_id IN ({', '.join('?' * len(addon_ids))})",
                tuple(addon_ids),
            )
        elif neutral.get("cycle_charge"):
            paid_at = int(neutral.get("paid_at") or self.now)
            rows += self._topup_rows(
                org_id, "status = 'scheduled' AND created_at <= ?", (paid_at,)
            )
        settled = 0
        seen: set[str] = set()
        for row in rows:
            if row["invoice_id"] in seen:
                continue
            seen.add(row["invoice_id"])
            if self._credit_topup(org_id, row, str(neutral.get("payment_id") or "")):
                settled += 1
        return settled

    def _credit_topup(self, org_id: str, row: dict[str, Any], payment_id: str) -> bool:
        from contextlib import closing

        from aira.suite.ads import service as ads
        from aira.suite.ads.store import CENT
        from aira.suite.videos import db as videos_db

        # the ledger entry is keyed by the top-up, so a second delivery of the same payment credits nothing
        with closing(videos_db.connect()) as sc:
            ads.credit_once(
                sc,
                org_id,
                int(row["amount_cents"]) * CENT,
                kind="topup",
                reference=row["invoice_id"],
                note="Paid",
                by="billing",
            )
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE invoices SET status = 'paid', paid_at = ?, payment_id = ? WHERE invoice_id = ? AND status IN ('open', 'scheduled', 'failed')",
                (self.now, payment_id, row["invoice_id"]),
            ).rowcount
            if changed:
                self.app.audit.record(
                    org_id,
                    "billing.ads_topup_paid",
                    actor_type="webhook",
                    target_type="invoice",
                    target_id=row["invoice_id"],
                    detail={"amount_cents": int(row["amount_cents"])},
                    connection=connection,
                )
        return bool(changed)

    def _fail_topups(self, org_id: str, invoice_id: str, status: str) -> int:
        """A top-up whose payment failed (or was voided) is never credited; it stays on record as failed."""
        if not invoice_id:
            return 0
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE invoices SET status = ? WHERE org_id = ? AND kind = 'ads_topup' AND provider_invoice_id = ? AND status IN ('open', 'scheduled')",
                (status, org_id, invoice_id),
            ).rowcount
            if changed:
                self.app.audit.record(
                    org_id,
                    f"billing.ads_topup_{status}",
                    actor_type="webhook",
                    target_type="invoice",
                    target_id=invoice_id,
                    connection=connection,
                )
        if changed:
            self.notice(
                org_id,
                "warning",
                "Ad top-up not paid",
                "The payment for an ad balance top-up did not go through, so nothing was added to the balance.",
                "#/ads?tab=balance",
            )
        return int(changed)

    def _reverse_topups(self, org_id: str, neutral: dict[str, Any]) -> int:
        """A refund (or a dispute) of a payment that credited top-ups takes the credit back from the balance."""
        from contextlib import closing

        from aira.suite.ads import service as ads
        from aira.suite.ads.store import CENT
        from aira.suite.videos import db as videos_db

        payment_id = str(neutral.get("payment_id") or "")
        invoice_id = str(neutral.get("invoice_id") or "")
        clauses, params = [], []
        if payment_id:
            clauses.append("payment_id = ?")
            params.append(payment_id)
        if invoice_id:
            clauses.append("provider_invoice_id = ?")
            params.append(invoice_id)
        if not clauses:
            return 0
        rows = self._topup_rows(
            org_id, f"status = 'paid' AND ({' OR '.join(clauses)})", tuple(params)
        )
        # A refund in another currency than the top-ups (Razorpay refunds rupees for dollar top-ups) is taken as
        # the whole payment: every top-up it paid is reversed.
        currency = str(neutral.get("currency") or "").lower()
        same_units = not currency or all(
            currency == str(r["currency"]).lower() for r in rows
        )
        remaining = (int(neutral.get("amount_cents") or 0) if same_units else 0) or sum(
            int(r["amount_cents"]) for r in rows
        )
        reversed_rows = 0
        for row in rows:
            if remaining <= 0:
                break
            amount = min(int(row["amount_cents"]), remaining)
            # the ledger entry is keyed by the top-up, so the reversal happens once even if the event comes twice
            with closing(videos_db.connect()) as sc:
                ads.reverse_once(
                    sc,
                    org_id,
                    amount * CENT,
                    reference=row["invoice_id"],
                    note="Payment refunded",
                    by="billing",
                )
            with self.db.write() as connection:
                changed = connection.execute(
                    "UPDATE invoices SET status = 'refunded' WHERE invoice_id = ? AND status = 'paid'",
                    (row["invoice_id"],),
                ).rowcount
            if not changed:
                continue
            self.app.audit.record(
                org_id,
                "billing.ads_topup_refunded",
                actor_type="webhook",
                target_type="invoice",
                target_id=row["invoice_id"],
                detail={"amount_cents": amount},
            )
            remaining -= amount
            reversed_rows += 1
        return reversed_rows

    def unsubscribe_app(self, actor: Principal, app_id: str) -> dict[str, Any]:
        """Turn an app off at the period end; it keeps working until then."""
        app_id = app_catalog.current_id(app_id)
        self.app.authz.require(actor, "billing.manage")
        subscription = self._require_active_for_apps(actor.org_id)
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        app = app_catalog.BY_ID[app_id]
        row = self._app_rows(actor.org_id).get(app_id)
        if self.app.app_settings.state(subscription["plan_id"], app_id) == "included":
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
        app_id = app_catalog.current_id(app_id)
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

    def resync_app(self, app_id: str) -> dict[str, int]:
        """After an operator switched an app or changed its price: every organization that pays for it gets its
        provider subscription's add-on lines rewritten without proration, so the change bills from the next period."""
        ids = [
            app_id,
            *(old for old, new in app_catalog.LEGACY_IDS.items() if new == app_id),
        ]
        with platform_scope(), self.db.read() as connection:  # every tenant's rows
            org_ids = [
                r["org_id"]
                for r in many(
                    connection,
                    f"SELECT DISTINCT org_id FROM org_apps WHERE app_id IN ({', '.join('?' * len(ids))}) AND status = 'active'",
                    tuple(ids),
                )
            ]
        synced = failed = 0
        for org_id in org_ids:
            sub = self.subscription(org_id)
            if not sub["billing_subscription_id"]:
                continue
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
                synced += 1
            except Exception:  # noqa: BLE001 - one provider error must not stop the rest; reconcile reports it
                failed += 1
                logger.exception(
                    "Could not update the app add-on at the provider for %s", org_id
                )
        self.app.entitlements.invalidate()
        return {"organizations": len(org_ids), "synced": synced, "failed": failed}

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
        """An operator grants an app to a tenant without charge (a contract), or takes a grant back; or switches an
        app off for this one organization (``disabled``: closed to its members whatever the plan says, data kept)
        and back on (``enabled``: the plan, add-ons and grants decide again)."""
        app_id = app_catalog.current_id(app_id)
        self.app.authz.require(operator, "platform.billing.manage")
        self.app.tenancy.org(org_id)
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        if status not in {"granted", "revoked", "disabled", "enabled"}:
            raise ValidationFailed(
                "status must be granted, revoked, disabled or enabled."
            )
        row = self._app_rows(org_id).get(app_id)
        if status in {"disabled", "enabled"}:
            return self._set_app_disabled(operator, org_id, app_id, status, row, note)
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

    def _set_app_disabled(
        self,
        operator: Principal,
        org_id: str,
        app_id: str,
        status: str,
        row: dict[str, Any] | None,
        note: str,
    ) -> dict[str, Any]:
        app = app_catalog.BY_ID[app_id]
        if app_id not in APP_PRICING:  # Careers is each candidate's own workspace
            raise ValidationFailed(
                f"{app.name} is part of every plan; it cannot be switched off."
            )
        with self.db.write() as connection:
            if status == "disabled":
                if row and row["status"] == "disabled":
                    raise Conflict(
                        f"{app.name} is switched off already.", code="unchanged"
                    )
                if row and row["status"] == "active":
                    raise Conflict(
                        f"The organization pays for {app.name}; it turns it off under Apps, or reconcile billing first.",
                        code="already_subscribed",
                    )
                connection.execute(
                    """INSERT INTO org_apps (org_id, app_id, status, seats_at_start, activated_at, cancelled_at, cancel_at_period_end, granted_by, note)
                    VALUES (?, ?, 'disabled', 0, ?, NULL, 0, ?, ?)
                    ON CONFLICT(org_id, app_id) DO UPDATE SET status = 'disabled', cancelled_at = NULL,
                    cancel_at_period_end = 0, granted_by = excluded.granted_by, note = excluded.note""",
                    (org_id, app_id, self.now, operator.operator_id, note[:300]),
                )
            else:
                if not row or row["status"] != "disabled":
                    raise Conflict(
                        f"{app.name} is not switched off for this organization.",
                        code="unchanged",
                    )
                connection.execute(
                    "DELETE FROM org_apps WHERE org_id = ? AND app_id = ?",
                    (org_id, app_id),
                )
            action = (
                "billing.app_disabled"
                if status == "disabled"
                else "billing.app_enabled"
            )
            self.app.platform_audit.record(
                action,
                operator=operator,
                org_id=org_id,
                detail={"app": app_id, "note": note[:300]},
                connection=connection,
            )
            self.app.audit.record(
                org_id,
                action,
                actor=operator,
                target_type="app",
                target_id=app_id,
                detail={"note": note[:300]},
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        return {"org_id": org_id, "apps": self.apps_overview(org_id)}

    def operator_set_subscription(
        self,
        operator: Principal,
        org_id: str,
        *,
        plan_id: str,
        seats: int,
        months: int = 1,
        apps: dict[str, bool] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """The superadmin sets up a tenant's subscription directly: a customer who bought the plan outside the
        product (an invoice, a bank transfer, a contract). The plan, the seats and the paid-until date are set and
        the subscription is active; ``apps`` turns apps on (granted, no extra charge) or takes grants back. Apps the
        organization pays for itself are left alone. Any plan but the trial can be set, built-in or created by the
        superadmin, as long as it is not archived. Every change is recorded in both audit logs."""
        self.app.authz.require(operator, "platform.billing.manage")
        self.app.tenancy.org(org_id)
        choices = [p for p in self.app.catalog.order() if p != TRIAL]
        if plan_id not in choices:
            raise ValidationFailed(
                "Choose one of these plans: " + ", ".join(choices) + "."
            )
        plan = self.app.catalog.get(plan_id)
        minimum = int(plan["pricing"].get("min_seats") or 0)
        try:
            seats = int(seats)
            months = int(months)
        except (TypeError, ValueError):
            raise ValidationFailed("Seats and months must be whole numbers.") from None
        if seats < max(1, minimum):
            raise ValidationFailed(
                f"The {plan['name']} plan needs at least {max(1, minimum)} seats."
            )
        if not 1 <= months <= 36:
            raise ValidationFailed("Months must be between 1 and 36.")
        before = self.subscription(org_id)
        period_end = (
            max(self.now, int(before.get("period_end") or 0)) + months * 30 * DAY
        )
        self.transition(
            org_id,
            "active",
            reason=f"set by operator: {plan_id}, {seats} seats, {months} month(s)",
            trigger_type="operator",
            trigger_id=operator.operator_id,
            fields={
                "plan_id": plan_id,
                "plan_version": plan["version"],
                "candidate_seats": seats,
                "period_start": self.now,
                "period_end": period_end,
                "trial_end": None,
                "pending_plan_id": "",
                "pending_seats": None,
                "pending_effective_at": None,
                "cancel_at_period_end": 0,
                "cancelled_at": None,
                "past_due_since": None,
                "read_only_at": None,
                "deletion_scheduled_at": None,
            },
        )
        detail = {
            "from": {
                "plan": before["plan_id"],
                "status": before["status"],
                "seats": before["candidate_seats"],
            },
            "to": {"plan": plan_id, "seats": seats, "period_end": period_end},
            "note": note[:300],
        }
        self.app.platform_audit.record(
            "billing.subscription_set", operator=operator, org_id=org_id, detail=detail
        )
        self.app.audit.record(
            org_id,
            "billing.subscription_set",
            actor=operator,
            target_type="subscription",
            target_id=org_id,
            detail=detail,
        )
        self.app.entitlements.invalidate(org_id)
        for app_id, wanted in (apps or {}).items():
            app_id = app_catalog.current_id(app_id)
            if (
                app_id not in app_catalog.BY_ID
                or self.app.app_settings.state(plan_id, app_id) == "included"
            ):
                continue
            row = self._app_rows(org_id).get(app_id)
            status = row["status"] if row else ""
            if wanted and status not in ("active", "granted"):
                self.set_app_grant(operator, org_id, app_id, "granted", note)
            elif not wanted and status == "granted":
                self.set_app_grant(operator, org_id, app_id, "revoked", note)
        self.app.lifecycle.recompute_over_limits(org_id)
        return {
            "org_id": org_id,
            "subscription": self.summary(org_id),
            "apps": self.apps_overview(org_id),
        }

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
        self, provider_name: str, payload: bytes, signature: str, event_id: str = ""
    ) -> dict[str, Any]:
        """Verify, record once (by the provider's event id) and apply a provider event. ``event_id`` is the id a
        provider sends in a header instead of the body (Razorpay's ``X-Razorpay-Event-Id``)."""
        provider = self.providers.get(provider_name)
        if provider is None:
            raise NotFound("Unknown billing provider.")
        event = provider.verify_webhook(payload, signature)
        neutral = provider.normalize(event)
        event_id = str(event.get("id") or event_id or "")
        if not event_id and provider_name == "razorpay":
            event_id = "sha256:" + hashlib.sha256(payload).hexdigest()[:48]
        if not event_id:
            raise ValidationFailed("The event has no id.")
        if neutral is not None and not neutral.get("id"):
            neutral["id"] = event_id
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
                        str(event.get("type") or event.get("event") or "")[:80],
                        (neutral or {}).get("org_id", ""),
                        payload.decode("utf-8", "replace")[:20000],
                        self.now,
                    ),
                )
        if neutral is None:
            self._finish_event(event_id, "")
            return {"ignored": True}
        try:
            outcome = self.apply_event(neutral, provider_name)
        except Exception as error:  # noqa: BLE001 - recorded so the provider's retry can succeed later
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE billing_events SET error = ? WHERE event_id = ?",
                    (f"{type(error).__name__}: {error}"[:300], event_id),
                )
            raise
        self._finish_event(event_id, "")
        answer: dict[str, Any] = {"processed": True, "type": neutral["type"]}
        if outcome:
            answer["outcome"] = outcome
        return answer

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
                row = one(
                    connection,
                    "SELECT org_id FROM billing_checkouts WHERE session_id = ?",
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
            invoice_id = neutral.get("invoice_id") or ""
            if invoice_id:
                row = one(
                    connection,
                    "SELECT org_id FROM invoices WHERE provider_invoice_id = ?",
                    (invoice_id,),
                )
                if row:
                    return str(row["org_id"])
            payment_id = neutral.get("payment_id") or ""
            if payment_id:  # a refund names only the payment it returns
                row = one(
                    connection,
                    "SELECT org_id FROM invoices WHERE payment_id = ?",
                    (payment_id,),
                )
                if row:
                    return str(row["org_id"])
        raise NotFound("The event does not belong to a known organization.")

    def _stale(self, subscription: dict[str, Any], neutral: dict[str, Any]) -> bool:
        """An event older than the newest one applied to this subscription (providers do not promise order)."""
        if neutral["type"] not in ORDERED_EVENTS:
            return False
        created = int(neutral.get("created") or 0)
        newest = int(subscription.get("provider_event_at") or 0)
        return bool(created and newest and created < newest)

    def _seen_event(self, org_id: str, neutral: dict[str, Any]) -> None:
        created = int(neutral.get("created") or 0)
        if not created or neutral["type"] not in ORDERED_EVENTS:
            return
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET provider_event_at = ? WHERE org_id = ? AND (provider_event_at IS NULL OR provider_event_at < ?)",
                (created, org_id, created),
            )

    def apply_event(self, neutral: dict[str, Any], provider_name: str) -> str:
        org_id = self._org_for_event(neutral)
        subscription = self.subscription(org_id)
        if subscription["status"] == "deleted":
            return "deleted"
        stale = self._stale(subscription, neutral)
        outcome = self._apply(org_id, subscription, neutral, provider_name, stale)
        if not stale:
            self._seen_event(org_id, neutral)
        return "stale" if stale and not outcome else outcome

    def _apply(
        self,
        org_id: str,
        subscription: dict[str, Any],
        neutral: dict[str, Any],
        provider_name: str,
        stale: bool,
    ) -> str:
        kind = neutral["type"]
        trigger: dict[str, Any] = {
            "trigger_type": "webhook",
            "trigger_id": neutral.get("id", ""),
        }
        upgrade = loads(subscription.get("pending_upgrade"), None)
        invoice_id = str(neutral.get("invoice_id") or "")
        if kind == "checkout.completed":
            return self._checkout_completed(
                org_id, subscription, neutral, provider_name, trigger
            )
        if kind == "invoice.paid":
            # money for a top-up or an upgrade is not a renewal: it settles that and leaves the period alone
            if self._settle_topups(org_id, neutral) and self._is_one_off(neutral):
                self._record_invoice(org_id, neutral, "paid")
                return "topup_paid"
            if upgrade and (
                (upgrade.get("invoice_id") and invoice_id == upgrade["invoice_id"])
                or (
                    not upgrade.get("invoice_id")
                    and upgrade.get("provider") == "stripe"
                    and neutral.get("billing_reason") == "subscription_update"
                )
            ):
                self._apply_upgrade(
                    org_id, upgrade, paid=True, trigger_id=trigger["trigger_id"]
                )
                return "upgrade_applied"
            if self._is_one_off(neutral):
                self._record_invoice(org_id, neutral, "paid")
                return "one_off_paid"
            self._record_invoice(org_id, neutral, "paid")
            if provider_name == "razorpay" and not neutral.get("addon_ids"):
                # Razorpay charges add-ons with the next cycle: this charge paid the top-ups added before it
                self._settle_topups(org_id, {**neutral, "cycle_charge": True})
            if stale:
                return "recorded"
            fields: dict[str, Any] = {
                "past_due_since": None,
                "read_only_at": None,
                "deletion_scheduled_at": None,
                "deletion_notice_at": None,
            }
            fields.update(self._period_fields(org_id, subscription, neutral))
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
            return "paid"
        if kind == "invoice.payment_failed":
            if self._fail_topups(org_id, invoice_id, "failed"):
                return "topup_failed"
            if (
                upgrade
                and upgrade.get("invoice_id")
                and invoice_id == upgrade["invoice_id"]
            ):
                self._drop_upgrade(org_id, upgrade, "the card was declined")
                return "upgrade_failed"
            if self._is_one_off(neutral):
                return "one_off_failed"
            self._record_invoice(org_id, neutral, "failed")
            if stale:
                return "recorded"
            if subscription["status"] == "active":
                self.transition(
                    org_id,
                    "past_due",
                    reason="payment failed",
                    fields={"past_due_since": self.now},
                    **trigger,
                )
                self._notify_payment_failed(org_id)
            return "failed"
        if kind == "invoice.voided":
            if self._fail_topups(org_id, invoice_id, "void"):
                return "topup_void"
            if (
                upgrade
                and upgrade.get("invoice_id")
                and invoice_id == upgrade["invoice_id"]
            ):
                self._drop_upgrade(org_id, upgrade, "the payment expired")
                return "upgrade_failed"
            return "ignored"
        if kind == "payment.refunded":
            return (
                "topup_refunded" if self._reverse_topups(org_id, neutral) else "ignored"
            )
        if kind == "subscription.update_expired":
            if upgrade:
                self._drop_upgrade(org_id, upgrade, "the payment was not completed")
                return "upgrade_failed"
            return "ignored"
        if kind == "subscription.updated":
            if stale:
                return ""
            return self._subscription_updated(
                org_id, subscription, neutral, upgrade, trigger
            )
        if kind == "subscription.deleted":
            if stale:
                return ""
            if subscription["billing_subscription_id"] and neutral.get(
                "subscription_id"
            ) not in ("", None, subscription["billing_subscription_id"]):
                return "other_subscription"  # an old or duplicate subscription ended; ours goes on
            self._void_scheduled_topups(org_id)
            if subscription["status"] in {
                "active",
                "past_due",
                "read_only",
                "trialing",
            }:
                self._enter_cancelled(
                    org_id, reason="subscription ended at the provider", **trigger
                )
            return "cancelled"
        return ""

    @staticmethod
    def _is_one_off(neutral: dict[str, Any]) -> bool:
        return neutral.get("kind") in {"ads_topup", "proration"} or neutral.get(
            "billing_reason"
        ) in {"manual", "subscription_update"}

    def _void_scheduled_topups(self, org_id: str) -> None:
        """Add-ons on a subscription that ended are never charged: their top-ups will not be paid."""
        with self.db.write() as connection:
            connection.execute(
                "UPDATE invoices SET status = 'void' WHERE org_id = ? AND kind = 'ads_topup' AND status = 'scheduled'",
                (org_id,),
            )

    def _period_fields(
        self, org_id: str, subscription: dict[str, Any], neutral: dict[str, Any]
    ) -> dict[str, Any]:
        """The provider's billing period, when it is newer than ours. A period that moves on closes the one before
        it for overage billing (the provider's boundaries, not calendar months)."""
        start, end = neutral.get("period_start"), neutral.get("period_end")
        if not (start and end and int(end) > int(start)):
            return {}
        start, end = int(start), int(end)
        current_start = int(subscription["period_start"] or 0)
        if subscription["period_end"] and end <= current_start:
            return {}  # an older period than the one we are in
        if current_start and start > current_start + DAY:
            self._close_period(org_id, current_start, start)
        return {"period_start": start, "period_end": end}

    def _checkout_completed(
        self,
        org_id: str,
        subscription: dict[str, Any],
        neutral: dict[str, Any],
        provider_name: str,
        trigger: dict[str, Any],
    ) -> str:
        session_id = str(neutral.get("session_id") or "")
        subscription_id = str(neutral.get("subscription_id") or "")
        with self.db.read() as connection:
            checkout = (
                one(
                    connection,
                    "SELECT * FROM billing_checkouts WHERE session_id = ?",
                    (session_id,),
                )
                if session_id
                else None
            )
        live = subscription["status"] in {"active", "past_due", "read_only"}
        if (
            subscription_id
            and subscription_id == subscription["billing_subscription_id"]
            and live
        ):
            # the same provider subscription again: a repeated completion, or Razorpay re-activating after a retry
            self._close_checkout(session_id, "completed")
            if subscription["status"] in {"past_due", "read_only"}:
                fields: dict[str, Any] = {
                    "past_due_since": None,
                    "read_only_at": None,
                    "deletion_scheduled_at": None,
                    "deletion_notice_at": None,
                }
                fields.update(self._period_fields(org_id, subscription, neutral))
                self.transition(
                    org_id,
                    "active",
                    reason="payment recovered",
                    fields=fields,
                    **trigger,
                )
                return "recovered"
            return "already_applied"
        if checkout and checkout["status"] == "completed":
            return "already_applied"
        if (
            checkout
            and checkout["status"] != "open"
            and live
            and subscription["billing_subscription_id"]
        ):
            # An older checkout was paid after a newer one already started the plan: a second subscription would bill
            # twice, so it is cancelled at once and the payment flagged for a refund.
            provider = self.providers.get(checkout["provider"]) or self.providers.get(
                provider_name
            )
            if provider is not None and subscription_id:
                try:
                    provider.cancel_subscription(subscription_id, at_period_end=False)
                except Exception:  # noqa: BLE001 - reported below; an operator cancels it by hand
                    logger.exception(
                        "Could not cancel the duplicate subscription %s",
                        subscription_id,
                    )
            self._close_checkout(session_id, "duplicate")
            self.app.audit.record(
                org_id,
                "billing.checkout_duplicate",
                actor_type="webhook",
                actor_id=trigger["trigger_id"],
                target_type="checkout",
                target_id=session_id,
                detail={"subscription_id": subscription_id, "provider": provider_name},
            )
            self.notice(
                org_id,
                "warning",
                "A second payment was stopped",
                "An older checkout was completed after your plan had started. Its subscription was cancelled so you "
                "are not billed twice; any amount taken for it is refunded by support.",
            )
            return "duplicate"
        plan_id, seats = (
            neutral.get("plan_id") or subscription["plan_id"],
            int(neutral.get("seats") or subscription["candidate_seats"]),
        )
        plan = self.app.catalog.get(plan_id)
        start, end = neutral.get("period_start"), neutral.get("period_end")
        if not (start and end and int(end) > int(start)):
            start, end = self.now, self.now + PERIOD_SECONDS
        self.transition(
            org_id,
            "active",
            reason="checkout completed",
            fields={
                "plan_id": plan_id,
                "plan_version": plan["version"],
                "candidate_seats": seats,
                "period_start": int(start),
                "period_end": int(end),
                "trial_end": None,
                "cancel_at_period_end": 0,
                "billing_provider": provider_name,
                "billing_customer_id": neutral.get("customer_id")
                or subscription["billing_customer_id"],
                "billing_subscription_id": subscription_id
                or subscription["billing_subscription_id"],
                "past_due_since": None,
                "read_only_at": None,
                "deletion_scheduled_at": None,
                "export_until": None,
                "pending_plan_id": "",
                "pending_seats": None,
                "pending_effective_at": None,
                "pending_upgrade": "",
            },
            **trigger,
        )
        self._close_checkout(session_id, "completed")
        # this completion is the plan now: any other checkout still open is closed so it cannot be paid too
        with self.db.write() as connection:
            others = many(
                connection,
                "SELECT session_id, provider FROM billing_checkouts WHERE org_id = ? AND status = 'open'",
                (org_id,),
            )
            connection.execute(
                "UPDATE billing_checkouts SET status = 'superseded', closed_at = ? WHERE org_id = ? AND status = 'open'",
                (self.now, org_id),
            )
        self._expire_checkouts(others)
        self.app.lifecycle.recompute_over_limits(org_id)
        return "activated"

    def _close_checkout(self, session_id: str, status: str) -> None:
        if not session_id:
            return
        with self.db.write() as connection:
            connection.execute(
                "UPDATE billing_checkouts SET status = ?, closed_at = ? WHERE session_id = ? AND status != 'completed'",
                (status, self.now, session_id),
            )

    def _subscription_updated(
        self,
        org_id: str,
        subscription: dict[str, Any],
        neutral: dict[str, Any],
        upgrade: dict[str, Any] | None,
        trigger: dict[str, Any],
    ) -> str:
        """The provider changed the subscription: its period, cancellation at period end, and a plan or seat change
        made outside Aira (Stripe's customer portal). An upgrade waiting for payment is confirmed when the provider
        shows it applied."""
        plan_id = str(neutral.get("plan_id") or "")
        seats = int(neutral.get("seats") or 0)
        if upgrade and not neutral.get("pending_update"):
            match = upgrade.get("match") or {}
            confirmed = (
                match.get("provider_plan")
                and neutral.get("provider_plan") == match["provider_plan"]
                and seats == int(match.get("seats") or 0)
            ) or (
                not match.get("provider_plan")
                and plan_id == upgrade["plan_id"]
                and seats == int(upgrade["seats"])
                and upgrade.get("provider") != "standin"
            )
            if confirmed:
                self._apply_upgrade(
                    org_id, upgrade, paid=True, trigger_id=trigger["trigger_id"]
                )
                subscription = self.subscription(org_id)
        fields: dict[str, Any] = self._period_fields(org_id, subscription, neutral)
        if neutral.get("cancel_at_period_end") is not None:
            fields["cancel_at_period_end"] = int(bool(neutral["cancel_at_period_end"]))
        portal_change = False
        if (
            not neutral.get("pending_update")
            and not subscription.get("pending_upgrade")
            and plan_id
            and subscription["status"] in {"active", "past_due"}
        ):
            try:
                plan = self.app.catalog.get(plan_id)
            except NotFound:
                plan = None
                logger.warning("Provider update names an unknown plan %s", plan_id)
            if plan is not None and (
                plan_id != subscription["plan_id"]
                or (seats and seats != subscription["candidate_seats"])
            ):
                fields.update(
                    {
                        "plan_id": plan_id,
                        "plan_version": plan["version"],
                        "candidate_seats": seats or subscription["candidate_seats"],
                        "pending_plan_id": "",
                        "pending_seats": None,
                        "pending_effective_at": None,
                    }
                )
                portal_change = True
        self.transition(
            org_id,
            subscription["status"],
            reason="provider update",
            fields=fields,
            **trigger,
        )
        if portal_change:
            self.app.audit.record(
                org_id,
                "billing.provider_change",
                actor_type="webhook",
                actor_id=trigger["trigger_id"],
                target_type="subscription",
                target_id=org_id,
                detail={
                    "from": {
                        "plan": subscription["plan_id"],
                        "seats": subscription["candidate_seats"],
                    },
                    "to": {"plan": plan_id, "seats": fields["candidate_seats"]},
                },
            )
            self.app.lifecycle.recompute_over_limits(org_id)
            return "plan_changed"
        return "updated"

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
                # a paid invoice stays paid: a late failure for it (out of order) changes nothing
                connection.execute(
                    "UPDATE invoices SET status = ?, paid_at = ? WHERE org_id = ? AND provider_invoice_id = ? AND status NOT IN ('paid', 'refunded')",
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
                """INSERT INTO invoices (invoice_id, org_id, provider_invoice_id, payment_id, kind, amount_cents, currency, status, period_start, period_end, hosted_url, lines, created_at, paid_at)
                VALUES (?, ?, ?, ?, 'subscription', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    org_id,
                    invoice_id,
                    str(neutral.get("payment_id") or ""),
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
        was_trial = subscription["status"] == "trialing"
        window = (
            self.app.config.trial_retention_days
            if subscription["plan_id"] == TRIAL or subscription["status"] == "trialing"
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
        if was_trial:
            # the end of a trial is a clear step down: read-only, export only, until a plan is bought
            self.notice(
                org_id,
                "warning",
                "Your trial ended",
                f"The {self.app.catalog.name(subscription['plan_id'])} trial is over, so the organization is read-only "
                "and automation is off. Choose a plan to keep working; until then the data can be exported until "
                f"{time.strftime('%Y-%m-%d', time.gmtime(export_until))}.",
                "#/org/billing",
            )
            return
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
                waiting = loads(sub.get("pending_upgrade"), None)
                if (
                    waiting
                    and int(waiting.get("requested_at") or 0) + UPGRADE_PENDING_SECONDS
                    <= now
                ):
                    self._drop_upgrade(org_id, waiting, "it was not paid within a day")
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
        try:
            self.bill_due_overage()  # periods the provider's webhooks closed, and failed ones to retry
        except Exception:  # noqa: BLE001 - the next tick tries again
            logger.exception("Overage billing failed")
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
        provider = self.providers.get(sub["billing_provider"] or "")
        if not (
            provider is not None
            and provider.name != "standin"
            and sub["billing_subscription_id"]
        ):
            # Our period is the billing period (the stand-in, an operator's plan): it closes now. A provider's
            # period closes when the provider reports the next one (invoice.paid), at its own boundaries.
            self._close_period(org_id, sub["period_start"], sub["period_end"])
        self.bill_due_overage(org_id)
        self._apply_app_cancellations(sub)
        sub = self.subscription(org_id)
        plan = self.app.catalog.get(sub["plan_id"], sub["plan_version"])
        if (
            not sub["billing_subscription_id"]
            and is_free(plan["pricing"])
            and self._monthly_cents(sub) == 0
        ):
            # a free plan (All access): nothing to invoice, the period simply moves on
            self.transition(
                org_id,
                "active",
                reason="free plan renewed",
                trigger_type="scheduler",
                fields={
                    "period_start": sub["period_end"],
                    "period_end": sub["period_end"] + PERIOD_SECONDS,
                },
            )
            done["renewed"].append(org_id)
            return
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
        kept = [
            a for a in current if self.app.app_settings.state(plan_id, a) == "addon"
        ]
        dropped = [
            a
            for a in current
            if self.app.app_settings.state(plan_id, a) == "unavailable"
        ]
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
                    at_cycle_end=False,  # the period is over: the lower price applies from now
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

    # -- metered overage, per provider billing period ----------------------------------------

    def _close_period(self, org_id: str, start: int | None, end: int | None) -> bool:
        """A billing period is over: its overage can be billed once. Nothing is kept for a period with no overage."""
        if not start or not end or int(end) <= int(start):
            return False
        if not self.app.usage.overage_between(org_id, int(start), int(end)):
            return False
        with self.db.write() as connection:
            return bool(
                connection.execute(
                    "INSERT OR IGNORE INTO overage_periods (org_id, period_start, period_end, state, created_at) VALUES (?, ?, ?, NULL, ?)",
                    (org_id, int(start), int(end), self.now),
                ).rowcount
            )

    def _overage_lines(
        self, sub: dict[str, Any], start: int, end: int
    ) -> list[dict[str, Any]]:
        counters = self.app.usage.overage_between(sub["org_id"], start, end)
        plan = self.app.catalog.get(sub["plan_id"], sub["plan_version"])
        rates = with_later_rates(sub["plan_id"], plan["pricing"].get("overage", {}))
        span = f"{_day(start)} to {_day(end)}"
        lines = []
        for metric, (rate_name, label) in OVERAGE_METRICS.items():
            units = int(counters.get(metric, 0))
            rate = int(rates.get(rate_name, 0))
            if units and rate:
                lines.append(
                    {
                        "metric": metric,
                        "units": units,
                        "rate_cents": rate,
                        "description": f"{units} extra {label}, {span}",
                        "amount_cents": units * rate,
                    }
                )
        return lines

    def bill_due_overage(self, org_id: str | None = None) -> dict[str, int]:
        """Bill every closed period not billed yet (and retry failed ones). Safe to run from overlapping ticks."""
        now = self.now
        where = "(state IS NULL OR (state = 'failed' AND attempts < ?) OR (state = 'pending' AND claimed_at < ?))"
        params: list[Any] = [OVERAGE_MAX_ATTEMPTS, now - OVERAGE_CLAIM_SECONDS]
        if org_id:
            where += " AND org_id = ?"
            params.append(org_id)
        with platform_scope(), self.db.read() as connection:
            due = many(
                connection,
                f"SELECT org_id, period_start FROM overage_periods WHERE {where} ORDER BY period_start",
                tuple(params),
            )
        outcome: dict[str, int] = {}
        for row in due:
            try:
                result = self._bill_overage_period(
                    str(row["org_id"]), int(row["period_start"])
                )
            except Exception:  # noqa: BLE001 - one organization must not stop the rest
                logger.exception("Overage billing failed for %s", row["org_id"])
                result = "error"
            outcome[result] = outcome.get(result, 0) + 1
        return outcome

    def _bill_overage_period(self, org_id: str, period_start: int) -> str:
        """Claim the period (one conditional UPDATE: only one tick wins), call the provider for each line not billed
        yet with an idempotency key from the organization, the period and the line, then mark it billed, or put it
        back as failed for the next tick. A line the provider accepted keeps its item id, so a retry skips it."""
        now = self.now
        with self.db.write() as connection:
            claimed = connection.execute(
                """UPDATE overage_periods SET state = 'pending', claimed_at = ?, attempts = attempts + 1
                WHERE org_id = ? AND period_start = ?
                AND (state IS NULL OR (state = 'failed' AND attempts < ?) OR (state = 'pending' AND claimed_at < ?))""",
                (
                    now,
                    org_id,
                    period_start,
                    OVERAGE_MAX_ATTEMPTS,
                    now - OVERAGE_CLAIM_SECONDS,
                ),
            ).rowcount
            if not claimed:
                return "taken"
            period = one(
                connection,
                "SELECT * FROM overage_periods WHERE org_id = ? AND period_start = ?",
                (org_id, period_start),
            )
        assert period is not None
        start, end = int(period["period_start"]), int(period["period_end"])
        sub = self.subscription(org_id)
        lines = loads(period["lines"], []) or self._overage_lines(sub, start, end)
        if not lines:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE overage_periods SET state = 'none', billed_at = ? WHERE org_id = ? AND period_start = ? AND state = 'pending'",
                    (now, org_id, period_start),
                )
            return "none"
        provider = self.providers.get(sub["billing_provider"] or "")
        external = (
            provider is not None
            and provider.name != "standin"
            and bool(sub["billing_customer_id"])
        )
        error = ""
        if external:
            assert provider is not None
            for line in lines:
                if line.get("item_id"):
                    continue  # billed by an earlier attempt
                try:
                    line["item_id"] = provider.add_invoice_item(
                        sub["billing_customer_id"],
                        int(line["amount_cents"]),
                        f"Monosage overage: {line['description']}",
                        "usd",
                        subscription_id=sub["billing_subscription_id"],
                        idempotency_key=f"aira-overage-{org_id}-{start}-{end}-{line['metric']}",
                        metadata={
                            "org_id": org_id,
                            "kind": "overage",
                            "period_start": start,
                            "period_end": end,
                            "metric": line["metric"],
                        },
                    )
                except Exception as failure:  # noqa: BLE001 - retried by the next tick
                    error = f"{type(failure).__name__}: {failure}"[:300]
                    logger.warning(
                        "Overage for %s (%s to %s) not billed yet: %s",
                        org_id,
                        _day(start),
                        _day(end),
                        error,
                    )
                    break
        total = sum(int(line["amount_cents"]) for line in lines)
        with self.db.write() as connection:
            if error:
                connection.execute(
                    "UPDATE overage_periods SET state = 'failed', lines = ?, amount_cents = ?, error = ? WHERE org_id = ? AND period_start = ? AND state = 'pending'",
                    (dumps(lines), total, error, org_id, period_start),
                )
                return "failed"
            done = connection.execute(
                "UPDATE overage_periods SET state = 'billed', lines = ?, amount_cents = ?, error = '', billed_at = ? WHERE org_id = ? AND period_start = ? AND state = 'pending'",
                (dumps(lines), total, now, org_id, period_start),
            ).rowcount
            if not done:
                return "taken"
            connection.execute(
                """INSERT INTO invoices (invoice_id, org_id, item_id, kind, amount_cents, currency, status, period_start, period_end, lines, created_at)
                VALUES (?, ?, ?, 'overage', ?, 'usd', 'open', ?, ?, ?, ?)""",
                (
                    new_id(),
                    org_id,
                    ",".join(str(line.get("item_id") or "") for line in lines)[:500],
                    total,
                    start,
                    end,
                    dumps(
                        [
                            {
                                "description": line["description"],
                                "amount_cents": line["amount_cents"],
                            }
                            for line in lines
                        ]
                    ),
                    now,
                ),
            )
        return "billed"

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
        # a trial of any plan (the sign-up default can be a trial of a paid plan); a paid subscription has no trial end
        if subscription["status"] not in {"trialing", "cancelled"} or (
            subscription["plan_id"] != TRIAL and not subscription["trial_end"]
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
                    # Razorpay's states
                    "authenticated": "active",
                    "pending": "past_due",
                    "halted": "past_due",
                    "cancelled": "cancelled",
                    "completed": "cancelled",
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
