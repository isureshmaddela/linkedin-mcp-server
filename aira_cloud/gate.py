"""The gate in front of every automation run, and the limits it applies to the shared autopilot.

Plan limits, organization policy and the pause switches are checked when a run *starts*, not only
when it was scheduled, so a downgrade, a pause or a lapsed payment takes effect within a minute.
Automatic submission needs two switches: the organization policy (and the plan) allows it, and the
candidate (or an ``approve`` coach) turned it on in their own search plan. When either is off, the
run fills the form and waits for a person to submit.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira_cloud import context, pooled
from aira_cloud.db import many, one
from aira_cloud.errors import PlanRequired, SubscriptionInactive

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.gate")

BOARD_SOURCES = {"greenhouse", "lever", "ashby", "smartrecruiters"}
SOURCE_TO_POLICY = {
    "linkedin": "linkedin",
    "google_jobs": "google_jobs",
    "aira_jobs": "aira_jobs",
}


def policy_source(source: str) -> str:
    return SOURCE_TO_POLICY.get(
        source,
        "company_boards" if source in BOARD_SOURCES or source else "company_boards",
    )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str = ""
    message: str = ""
    can_submit: bool = True
    review_reason: str = ""


class Gate:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ blocks

    def automation_block(
        self, org_id: str, candidate_id: str, *, consent: bool = True
    ) -> tuple[str, str] | None:
        """``(code, message)`` when no automation may run for this candidate now, else None."""
        try:
            entitlements = self.app.entitlements.resolve(org_id)
            org = self.app.tenancy.org(org_id)
        except Exception:  # noqa: BLE001 - an organization that cannot be read cannot run automation
            return "org_unavailable", "The organization is not available."
        if entitlements.suspended:
            return "suspended", "This organization is suspended."
        if "automation" in entitlements.restrictions:
            return (
                "subscription",
                f"Automation is stopped because the subscription is {entitlements.status.replace('_', ' ')}.",
            )
        if entitlements.over_limits:
            return (
                "over_limits",
                "Automation is paused until the organization is within its plan limits: "
                + ", ".join(entitlements.over_limits)
                + ".",
            )
        limited_until = org.get("rate_limited_until")
        if (
            limited_until
            and limited_until > self.now
            and org.get("rate_limit_per_minute") == 0
        ):
            return (
                "rate_limited",
                "Automation is temporarily limited for this organization.",
            )
        if org["paused_at"]:
            return (
                "org_paused",
                f"Automation is paused by your organization: {org['paused_reason'] or 'no reason given'}.",
            )
        if candidate_id:
            with self.app.db.read() as connection:
                workspace = one(
                    connection,
                    "SELECT status, paused_at, paused_reason FROM workspaces WHERE candidate_id = ? AND org_id = ?",
                    (candidate_id, org_id),
                )
            if workspace is None or workspace["status"] != "active":
                return "workspace", "This candidate's workspace is not active."
            if workspace["paused_at"]:
                return (
                    "candidate_paused",
                    f"Automation is paused for this candidate: {workspace['paused_reason'] or 'no reason given'}.",
                )
            if (
                consent
                and not self.app.tenancy.consent_state(org_id, candidate_id)["complete"]
            ):
                return (
                    "consent",
                    "The candidate has not accepted how the organization runs applications on their behalf.",
                )
        return None

    OUTREACH_SWITCH = "leads_outreach"

    def outreach_block(
        self, org_id: str, candidate_id: str
    ) -> tuple[str, str, str] | None:
        """``(kind, code, message)`` when no outbound lead email may go out now, else None.

        ``kind`` is ``switch`` (an operator stopped all outbound lead email) or ``state`` (the organization
        is suspended, past due, read-only, paused or over its limits: the same gates as automation).
        """
        with self.app.db.read() as connection:
            blocked = one(
                connection,
                "SELECT reason FROM blocked_sources WHERE source = ?",
                (self.OUTREACH_SWITCH,),
            )
        if blocked:
            reason = f" ({blocked['reason']})" if blocked["reason"] else ""
            return (
                "switch",
                "outreach_blocked",
                f"Outbound lead email is switched off for every organization right now{reason}.",
            )
        block = self.automation_block(org_id, candidate_id, consent=False)
        if block:
            return "state", block[0], block[1]
        return None

    def source_block(self, org_id: str, source: str, candidate_id: str = "") -> str:
        """Why a source cannot be used for this organization right now ('' when it can)."""
        if not source:
            return ""
        name = policy_source(source)
        with self.app.db.read() as connection:
            blocked = one(
                connection,
                "SELECT reason FROM blocked_sources WHERE source IN (?, ?)",
                (source, name),
            )
        if blocked:
            return (
                f"{source.replace('_', ' ')} is switched off for every organization right now"
                + (f" ({blocked['reason']})." if blocked["reason"] else ".")
            )
        entitlements = self.app.entitlements.resolve(org_id)
        feature = f"sources.{name}"
        if feature not in entitlements.features:
            needed = self.app.entitlements.required_plan(feature)
            return f"{name.replace('_', ' ')} is not part of your plan" + (
                f" (it starts with {needed.title()})." if needed else "."
            )
        policy = self.app.tenancy.policy(org_id)
        if name not in policy["allowed_sources"]:
            return f"{name.replace('_', ' ')} is not allowed by your organization's policy."
        if name == "linkedin":
            if not policy["linkedin_allowed"]:
                return "LinkedIn is off in your organization's policy."
            if candidate_id:
                with self.app.db.read() as connection:
                    workspace = one(
                        connection,
                        "SELECT linkedin_opt_in_at FROM workspaces WHERE candidate_id = ?",
                        (candidate_id,),
                    )
                if not workspace or not workspace["linkedin_opt_in_at"]:
                    return "The candidate has not turned LinkedIn on for themselves."
        return ""

    def region_review(self, org_id: str, candidate_id: str) -> str:
        """The region (from the policy) that requires human review for this candidate, if any."""
        regions = [
            r.lower()
            for r in self.app.tenancy.policy(org_id).get("review_required_regions", [])
        ]
        if not regions or not candidate_id:
            return ""
        try:
            path = Path(self.app.workspaces.row(org_id, candidate_id)["data_path"])
            connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5
            )
            try:
                rows = connection.execute(
                    "SELECT value FROM settings WHERE key IN ('application_location', 'application_country', 'application_state')"
                ).fetchall()
            finally:
                connection.close()
        except (sqlite3.Error, OSError, KeyError):
            return ""
        location = " ".join(str(r[0]) for r in rows).lower()
        for region in regions:
            if region in location:
                return region
        return ""

    def decide(
        self, org_id: str, candidate_id: str, *, submit_requested: bool = True
    ) -> Decision:
        block = self.automation_block(org_id, candidate_id)
        if block:
            return Decision(False, block[0], block[1], can_submit=False)
        entitlements = self.app.entitlements.resolve(org_id)
        policy = self.app.tenancy.policy(org_id)
        review_reason = ""
        if not entitlements.has("automation.submit"):
            review_reason = "your plan allows review mode only"
        elif policy["submission_mode"] != "auto":
            review_reason = "your organization requires review before submitting"
        else:
            region = self.region_review(org_id, candidate_id)
            if region:
                review_reason = f"applications in {region} need human review"
        return Decision(True, can_submit=not review_reason, review_reason=review_reason)

    # ------------------------------------------------------------------ counting

    def daily_remaining(self, org_id: str, candidate_id: str) -> int | None:
        entitlements = self.app.entitlements.resolve(org_id)
        caps = [
            c
            for c in (
                entitlements.limit("apps_per_candidate_day"),
                self.app.tenancy.policy(org_id).get("daily_cap_per_candidate"),
            )
            if c
        ]
        remaining: list[int] = []
        if caps:
            remaining.append(
                max(
                    0,
                    min(caps)
                    - self.app.usage.used_today(org_id, candidate_id, "applications"),
                )
            )
        monthly = self.app.usage.remaining_month(
            org_id,
            "applications",
            entitlements.limit("apps_per_org_month"),
            overage=entitlements.overage_enabled,
        )
        if monthly is not None:
            remaining.append(monthly)
        return min(remaining) if remaining else None

    # ------------------------------------------------------------------ service wrapper

    def wrap_services(self, services: Any, user: Mapping[str, Any]) -> Any:
        ctx = context.current()
        if ctx is None or not ctx.org_id or not ctx.candidate_id:
            return services
        org_id, candidate_id = ctx.org_id, ctx.candidate_id
        base = services

        def paused() -> bool:
            return (
                bool(base.paused())
                or self.automation_block(org_id, candidate_id) is not None
            )

        def unavailable(source: str) -> str:
            blocked = self.source_block(org_id, source, candidate_id)
            if not blocked and source == "google_jobs":
                used_up = pooled.exhausted(self.app, org_id)
                blocked = used_up[:1].upper() + used_up[1:] + "." if used_up else ""
            return blocked or base.unavailable(source)

        def daily_remaining() -> int | None:
            local = base.daily_remaining()
            ours = self.daily_remaining(org_id, candidate_id)
            if local is None:
                return ours
            return local if ours is None else min(local, ours)

        def search(
            plan: Mapping[str, Any], source: str, identifier: str, position: str
        ) -> list[dict[str, Any]]:
            pooled.begin()
            found = base.search(plan, source, identifier, position)
            self.app.usage.record(org_id, "searches", 1, candidate_id=candidate_id)
            spent = (
                pooled.collect()
            )  # credits the search really cost: nothing for a cache hit
            if (
                spent
                and source == "google_jobs"
                and pooled.uses_pooled_key(self.app, org_id)
            ):
                pooled.add(self.app, org_id, spent, candidate_id=candidate_id)
            return found

        def apply(
            job: dict[str, Any],
            run: dict[str, Any],
            answers: Mapping[str, str],
            submit: bool,
            plan: Mapping[str, Any] | None,
        ) -> dict[str, Any]:
            started = time.monotonic()
            decision = self.decide(org_id, candidate_id)
            if not decision.allowed:
                raise SubscriptionInactive(decision.message, code_detail=decision.code)
            sources = job.get("sources") or []
            for source in sources:
                reason = self.source_block(org_id, source, candidate_id)
                if reason:
                    return {
                        "status": "unsupported",
                        "error": f"Not applied: {reason[0].upper() + reason[1:]}",
                        "route": "form",
                    }
            easy_apply = bool(plan and plan.get("linkedin_easy_apply"))
            if (
                easy_apply
                and "linkedin" in sources
                and not self._easy_apply_allowed(org_id, candidate_id)
            ):
                return {
                    "status": "unsupported",
                    "route": "linkedin",
                    "error": "LinkedIn Easy Apply is off for this candidate or organization. Apply from the job instead.",
                }
            effective_submit = bool(
                submit
            )  # automatic submission was already held back by ``submit_allowed``
            reservation = None
            if effective_submit:
                reservation = self.app.enforcer.reserve(
                    org_id,
                    "applications",
                    monthly_limit_name="apps_per_org_month",
                    daily_limit_name="apps_per_candidate_day",
                    candidate_id=candidate_id,
                    policy_daily_cap=int(
                        self.app.tenancy.policy(org_id).get("daily_cap_per_candidate")
                        or 0
                    )
                    or None,
                    audience="candidate",
                )
            try:
                outcome = base.apply(job, run, answers, effective_submit, plan)
            except BaseException:
                if reservation:
                    self.app.usage.release(reservation)
                raise
            status = str(outcome.get("status") or "failed")
            if reservation:
                if status == "submitted":
                    ctx.scratch["reserved"] = True
                else:
                    self.app.usage.release(reservation)
            duration_ms = int((time.monotonic() - started) * 1000)
            self.app.usage.record(
                org_id,
                "browser_seconds",
                max(1, duration_ms // 1000),
                candidate_id=candidate_id,
            )
            self.app.record_outcome(
                org_id,
                candidate_id,
                "apply",
                source=(sources[0] if sources else ""),
                ats=str(outcome.get("ats") or ""),
                status=status,
                duration_ms=duration_ms,
                error_class=("error" if status == "failed" else ""),
            )
            return outcome

        def submit_allowed() -> bool:
            decision = self.decide(org_id, candidate_id)
            return (
                bool(base.submit_allowed()) and decision.allowed and decision.can_submit
            )

        return replace(
            base,
            paused=paused,
            unavailable=unavailable,
            daily_remaining=daily_remaining,
            search=search,
            apply=apply,
            submit_allowed=submit_allowed,
        )

    def _easy_apply_allowed(self, org_id: str, candidate_id: str) -> bool:
        if self.app.flags.killed("linkedin.easy_apply"):
            return False
        if not self.app.tenancy.policy(org_id).get("easy_apply_allowed"):
            return False
        with self.app.db.read() as connection:
            row = one(
                connection,
                "SELECT easy_apply_opt_in_at FROM workspaces WHERE candidate_id = ?",
                (candidate_id,),
            )
        return bool(row and row["easy_apply_opt_in_at"])


__all__ = ["Gate", "Decision", "PlanRequired", "many"]
