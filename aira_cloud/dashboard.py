"""The organization dashboard, the coach workload view and the held-question queue.

One screen answers four questions: are we within the plan, how is the pipeline moving, which
candidates need attention, and what did the automation do. Every number carries a link to the
records behind it, and what a person sees depends on their role: an owner, admin or auditor sees
the organization, a billing contact sees usage only, a coach sees their own candidates, a
candidate sees their own workspace.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira_cloud.config import DAY
from aira_cloud.db import loads, many, one
from aira_cloud.errors import Forbidden
from aira_cloud.plans import month_key
from aira_cloud.rbac import AUDITOR, BILLING, CANDIDATE, COACH, OWNER, ADMIN, Principal
from aira_cloud.workspace import STAGES, _to_epoch

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.dashboard")


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


class Dashboard:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ entry point

    def for_principal(self, actor: Principal) -> dict[str, Any]:
        """The dashboard this person sees."""
        if actor.role == CANDIDATE:
            return self.candidate(actor)
        if actor.role == COACH:
            return self.coach(actor)
        if actor.role == BILLING:
            return {
                "kind": "usage",
                "plan_usage": self.plan_usage(actor.org_id),
                "banners": self.banners(actor),
            }
        self.app.authz.require(actor, "dashboard.read")
        return self.organization(actor)

    # ------------------------------------------------------------------ organization

    def organization(self, actor: Principal) -> dict[str, Any]:
        org_id = actor.org_id
        rows, snapshots = self._candidate_rows(org_id)
        return {
            "kind": "organization",
            "banners": self.banners(actor),
            "plan_usage": self.plan_usage(org_id),
            "pipeline": self.pipeline(org_id, rows),
            "automation_today": self.automation_today(org_id, snapshots),
            "needs_attention": self.app.tenancy.org_attention(
                org_id,
                [
                    (r, snapshots.get(r["member_id"], {}), bool(r["_coached"]))
                    for r in rows
                ],
            ),
            "coach_workload": self.coach_workload(org_id, snapshots),
            "outcomes": self.outcomes(snapshots),
            "audit": {
                "recent": self.app.audit.list(org_id, limit=50),
                "link": "#/org/audit",
            }
            if self.app.authz.can(actor, "audit.read")
            else None,
        }

    def _candidate_rows(
        self, org_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        with self.app.db.read() as connection:
            rows = many(
                connection,
                """SELECT m.member_id, m.email, m.name, w.stage, w.status AS workspace_status, w.paused_at, w.linkedin_port,
                (SELECT COUNT(*) FROM coach_assignments a WHERE a.org_id = m.org_id AND a.candidate_id = m.member_id) AS _coached
                FROM members m JOIN workspaces w ON w.candidate_id = m.member_id
                WHERE m.org_id = ? AND m.role = 'candidate' AND m.removed_at IS NULL AND w.status = 'active' ORDER BY m.created_at""",
                (org_id,),
            )
        snapshots: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                snapshots[row["member_id"]] = self.app.workspaces.refresh(
                    org_id, row["member_id"], max_age=60
                )
                row["stage"] = self.app.workspaces.row(org_id, row["member_id"])[
                    "stage"
                ]
            except Exception:  # noqa: BLE001 - one unreadable workspace must not blank the dashboard
                logger.warning(
                    "Snapshot failed for %s", row["member_id"], exc_info=True
                )
                snapshots[row["member_id"]] = {}
        return rows, snapshots

    # ------------------------------------------------------------------ banners

    def banners(self, actor: Principal) -> list[dict[str, Any]]:
        org_id = actor.org_id
        out: list[dict[str, Any]] = []
        org = self.app.tenancy.org(org_id)
        summary = self.app.billing.subscription(org_id)
        if org["paused_at"]:
            out.append(
                {
                    "kind": "paused",
                    "level": "warning",
                    "title": "Automation is paused for the organization",
                    "body": org["paused_reason"],
                    "link": "#/org/dashboard",
                }
            )
        status = summary["status"]
        if status == "trialing" and summary["trial_end"]:
            days = max(0, (summary["trial_end"] - self.now) // DAY)
            out.append(
                {
                    "kind": "trial",
                    "level": "info",
                    "title": f"Trial: {days} day(s) left",
                    "body": "Pick a plan to keep automation running.",
                    "link": "#/org/billing",
                }
            )
        elif status in {"past_due", "read_only", "cancelled"}:
            out.append(
                {
                    "kind": "billing",
                    "level": "danger",
                    "title": f"Subscription {status.replace('_', ' ')}",
                    "body": "Automation is stopped."
                    + (" Data is read-only." if status != "past_due" else ""),
                    "link": "#/org/billing",
                }
            )
        if summary["over_limits"]:
            out.append(
                {
                    "kind": "over_limits",
                    "level": "warning",
                    "title": "Over plan limits",
                    "body": ", ".join(summary["over_limits"]),
                    "link": "#/org/members",
                }
            )
        if org["closure_requested_at"]:
            out.append(
                {
                    "kind": "closure",
                    "level": "danger",
                    "title": "Closure requested",
                    "body": "This organization will be deleted after the retention window.",
                    "link": "#/org/data",
                }
            )
        with self.app.db.read() as connection:
            grants = many(
                connection,
                "SELECT access_id, expires_at, role FROM support_access WHERE org_id = ? AND status = 'approved' AND expires_at > ? AND ended_at IS NULL",
                (org_id, self.now),
            )
            plan = summary["plan_id"]
            notices = many(
                connection,
                """SELECT announcement_id, severity, title, body FROM announcements WHERE cancelled_at IS NULL AND starts_at <= ? AND (ends_at IS NULL OR ends_at > ?)
                AND (scope_type = 'all' OR (scope_type = 'plan' AND scope_id = ?) OR (scope_type = 'org' AND scope_id = ?))""",
                (self.now, self.now, plan, org_id),
            )
        for grant in grants:
            out.append(
                {
                    "kind": "support_access",
                    "level": "warning",
                    "title": "Aira support has access to this organization",
                    "body": f"Until {grant['expires_at']}.",
                    "link": "#/org/support",
                }
            )
        for notice in notices:
            out.append(
                {
                    "kind": "announcement",
                    "level": notice["severity"],
                    "title": notice["title"],
                    "body": notice["body"],
                    "link": "",
                }
            )
        return out

    # ------------------------------------------------------------------ panels

    def plan_usage(self, org_id: str) -> dict[str, Any]:
        entitlements = self.app.entitlements.resolve(org_id)
        usage = self.app.usage.snapshot(org_id)
        seats = self.app.tenancy.seat_usage(org_id)
        summary = self.app.billing.subscription(org_id)
        storage = self.app.storage.usage_bytes(org_id)
        return {
            "plan_id": entitlements.plan_id,
            "status": entitlements.status,
            "period_end": entitlements.period_end,
            "days_to_renewal": max(
                0, ((entitlements.period_end or self.now) - self.now) // DAY
            ),
            "cancel_at_period_end": bool(summary["cancel_at_period_end"]),
            "seats": {
                "candidates": {
                    "used": seats["candidates"],
                    "limit": entitlements.limit("candidate_seats"),
                },
                "staff": {
                    "used": seats["staff"],
                    "limit": entitlements.limit("staff_seats"),
                },
            },
            "applications": {
                "used": usage.get("applications", 0),
                "limit": entitlements.limit("apps_per_org_month"),
                "overage": usage.get("applications:overage", 0),
                "link": "#/org/candidates",
            },
            "searches": {"used": usage.get("searches", 0)},
            "pooled_searches": {
                "used": usage.get("pooled_searches", 0),
                "limit": entitlements.limit("pooled_searches_per_month"),
                "overage": usage.get("pooled_searches:overage", 0),
            },
            "leads": {
                "stored": self.app.workspaces.leads_total(org_id),
                "limit": entitlements.limit("leads_stored"),
                "enrichments": usage.get("lead_enrichments", 0),
                "enrichments_limit": entitlements.limit("lead_enrichments_per_month"),
                "emails": usage.get("lead_emails", 0),
                "emails_limit": entitlements.limit("lead_emails_per_month"),
                "email_daily_cap": entitlements.limit("lead_email_daily_cap"),
            },
            "api_searches": {
                "used": usage.get("api_searches", 0),
                "limit": entitlements.limit("api_searches_per_month"),
            },
            "cover_letters": {
                "used": usage.get("cover_letters", 0),
                "limit": entitlements.limit("cover_letters_per_month"),
            },
            "storage": {
                "used_bytes": storage,
                "limit_bytes": entitlements.limit("storage_bytes"),
            },
            "model_calls": usage.get("model_calls", 0),
            "browser_seconds": usage.get("browser_seconds", 0),
            "cost_per_application": self._cost_per_application(usage),
            "period": month_key(self.now),
            "history": self.app.usage.history(org_id, 6),
        }

    @staticmethod
    def _cost_per_application(usage: dict[str, int]) -> dict[str, Any]:
        applications = usage.get("applications", 0)
        return {
            "browser_seconds": round(usage.get("browser_seconds", 0) / applications, 1)
            if applications
            else None,
            "model_calls": round(usage.get("model_calls", 0) / applications, 2)
            if applications
            else None,
            "searches": round(usage.get("searches", 0) / applications, 2)
            if applications
            else None,
        }

    def pipeline(self, org_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {stage: 0 for stage in STAGES}
        for row in rows:
            counts[row["stage"]] = counts.get(row["stage"], 0) + 1
        since = self.now - 30 * DAY
        with self.app.db.read() as connection:
            log = many(
                connection,
                "SELECT candidate_id, stage, at FROM candidate_stage_log WHERE org_id = ? ORDER BY at, id",
                (org_id,),
            )
            departed = many(
                connection,
                "SELECT stage, snapshot FROM departed_candidates WHERE org_id = ?",
                (org_id,),
            )
        before: dict[str, str] = {}
        for entry in log:
            if entry["at"] <= since:
                before[entry["candidate_id"]] = entry["stage"]
        earlier = {stage: 0 for stage in STAGES}
        active = {row["member_id"] for row in rows}
        for candidate_id, stage in before.items():
            if candidate_id in active:
                earlier[stage] = earlier.get(stage, 0) + 1
        return {
            "stages": [
                {
                    "stage": stage,
                    "count": counts.get(stage, 0),
                    "change_30d": counts.get(stage, 0) - earlier.get(stage, 0),
                    "link": f"#/org/candidates?stage={stage}",
                }
                for stage in STAGES
            ],
            "total": len(rows),
            "departed": {
                "count": len(departed),
                "placed": sum(1 for d in departed if d["stage"] == "placed"),
            },
        }

    def automation_today(
        self, org_id: str, snapshots: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        by_source: dict[str, dict[str, int]] = {}
        totals = {"submitted": 0, "needs_review": 0, "ready": 0, "failed": 0}
        for snapshot in snapshots.values():
            for source, statuses in (snapshot.get("runs_today") or {}).items():
                entry = by_source.setdefault(
                    source, {"submitted": 0, "needs_review": 0, "ready": 0, "failed": 0}
                )
                for status, count in statuses.items():
                    if status in totals:
                        entry[status] += count
                        totals[status] += count
        with self.app.db.read() as connection:
            live = many(
                connection,
                "SELECT job_id, candidate_id, kind, status, started_at, result, run_at FROM queue_jobs WHERE org_id = ? AND status IN ('queued', 'leased') ORDER BY created_at LIMIT 50",
                (org_id,),
            )
        return {
            "submitted": totals["submitted"],
            "held": totals["needs_review"],
            "ready_for_approval": totals["ready"],
            "failed": totals["failed"],
            "by_source": [{"source": s, **v} for s, v in sorted(by_source.items())],
            "live": [
                {
                    "candidate_id": j["candidate_id"],
                    "kind": j["kind"],
                    "status": "running" if j["status"] == "leased" else "waiting",
                    "detail": j["result"] or "",
                }
                for j in live
            ],
            "link": "#/org/candidates?attention=1",
        }

    def coach_workload(
        self, org_id: str, snapshots: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        with self.app.db.read() as connection:
            coaches = many(
                connection,
                "SELECT member_id, name, email FROM members WHERE org_id = ? AND role = 'coach' AND removed_at IS NULL AND status = 'active'",
                (org_id,),
            )
            assignments = many(
                connection,
                "SELECT coach_id, candidate_id FROM coach_assignments WHERE org_id = ?",
                (org_id,),
            )
        by_coach: dict[str, list[str]] = {}
        for item in assignments:
            by_coach.setdefault(item["coach_id"], []).append(item["candidate_id"])
        since = self.now - 30 * DAY
        out = []
        for coach in coaches:
            candidates = by_coach.get(coach["member_id"], [])
            held = sum(int(snapshots.get(c, {}).get("held", 0)) for c in candidates)
            ready = sum(int(snapshots.get(c, {}).get("ready", 0)) for c in candidates)
            out.append(
                {
                    "coach_id": coach["member_id"],
                    "name": coach["name"] or coach["email"],
                    "candidates": len(candidates),
                    "open_items": held + ready,
                    "held": held,
                    "ready": ready,
                    "median_answer_seconds": self.app.tenancy.median_answer_seconds(
                        org_id, coach["member_id"], since
                    ),
                    "link": f"#/org/coaches?coach={coach['member_id']}",
                }
            )
        return out

    def outcomes(self, snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
        def merge(key: str) -> list[dict[str, Any]]:
            merged: dict[str, dict[str, int]] = {}
            for snapshot in snapshots.values():
                for name, values in (snapshot.get(key) or {}).items():
                    entry = merged.setdefault(
                        name, {"applications": 0, "interviews": 0, "offers": 0}
                    )
                    for metric in entry:
                        entry[metric] += int(values.get(metric, 0))
            return [
                {
                    "name": name,
                    **values,
                    "interviews_per_100": _ratio(
                        values["interviews"], values["applications"]
                    ),
                    "offers_per_100": _ratio(values["offers"], values["applications"]),
                }
                for name, values in sorted(
                    merged.items(), key=lambda item: -item[1]["applications"]
                )
            ]

        return {
            "by_source": merge("by_source"),
            "by_resume_version": merge("by_resume"),
            "link": "#/org/candidates",
        }

    # ------------------------------------------------------------------ coach and candidate views

    def coach(self, actor: Principal) -> dict[str, Any]:
        candidates = self.app.tenancy.list_candidates(actor)
        return {
            "kind": "coach",
            "banners": self.banners(actor),
            "candidates": candidates,
            "queue": self.held_queue(actor),
            "totals": {
                "candidates": len(candidates),
                "held": sum(c["held"] for c in candidates),
                "ready": sum(c["ready"] for c in candidates),
                "attention": sum(1 for c in candidates if c["needs_attention"]),
            },
        }

    def candidate(self, actor: Principal) -> dict[str, Any]:
        snapshot = self.app.workspaces.refresh(
            actor.org_id, actor.member_id, max_age=30
        )
        workspace = self.app.workspaces.row(actor.org_id, actor.member_id)
        onboarding = self.app.tenancy.onboarding(actor.org_id, actor.member_id)
        coaches = self.app.tenancy.list_assignments(actor)
        policy = self.app.tenancy.policy(actor.org_id)
        entitlements = self.app.entitlements.resolve(actor.org_id)
        return {
            "kind": "candidate",
            "banners": self.banners(actor),
            "stage": workspace["stage"],
            "paused": {
                "paused": bool(workspace["paused_at"]),
                "reason": workspace["paused_reason"],
            },
            "onboarding": onboarding,
            "coaches": coaches,
            "queue": self.held_queue(actor),
            "rules": {
                "daily_cap": min(
                    [
                        c
                        for c in (
                            policy.get("daily_cap_per_candidate"),
                            entitlements.limit("apps_per_candidate_day"),
                        )
                        if c
                    ]
                    or [0]
                )
                or None,
                "submission_mode": policy["submission_mode"]
                if entitlements.has("automation.submit")
                else "review",
                "allowed_sources": [
                    s
                    for s in policy["allowed_sources"]
                    if f"sources.{s}" in entitlements.features
                ],
                "linkedin_allowed": bool(
                    policy["linkedin_allowed"] and entitlements.has("sources.linkedin")
                ),
                "cover_letters": bool(
                    policy["cover_letters"] and entitlements.has("ai.cover_letters")
                ),
            },
            "today": {
                "applications": self.app.usage.used_today(
                    actor.org_id, actor.member_id, "applications"
                ),
                "held": snapshot.get("held", 0),
                "ready": snapshot.get("ready", 0),
            },
        }

    def held_queue(
        self, actor: Principal, candidate_id: str = ""
    ) -> list[dict[str, Any]]:
        """Held questions and applications waiting for approval, across the candidates this person may act for."""
        org_id = actor.org_id
        if actor.role == CANDIDATE:
            targets = [actor.member_id]
        elif actor.role == COACH:
            targets = self.app.authz.assigned_candidates(org_id, actor.member_id)
        elif self.app.authz.can(actor, "audit.read") or actor.role in (
            OWNER,
            ADMIN,
            AUDITOR,
        ):
            with self.app.db.read() as connection:
                targets = [
                    r["candidate_id"]
                    for r in many(
                        connection,
                        "SELECT candidate_id FROM workspaces WHERE org_id = ? AND status = 'active'",
                        (org_id,),
                    )
                ]
        else:
            raise Forbidden("You do not have permission to do that.")
        if candidate_id:
            targets = [t for t in targets if t == candidate_id]
        items: list[dict[str, Any]] = []
        for target in targets:
            try:
                workspace = self.app.workspaces.row(org_id, target)
                member = self.app.tenancy.member(org_id, target)
            except Exception:  # noqa: BLE001
                continue
            path = Path(workspace["data_path"])
            if not path.exists():
                continue
            scope = None
            if actor.role == COACH:
                scope = self.app.authz.assignment_scope(org_id, actor.member_id, target)
            try:
                connection = sqlite3.connect(
                    f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5
                )
                connection.row_factory = sqlite3.Row
            except sqlite3.Error:
                continue
            try:
                for row in connection.execute(
                    """SELECT r.run_id, r.status, r.unanswered, r.finished_at, r.created_at, r.error, j.title, j.company, j.job_id
                    FROM application_runs r LEFT JOIN jobs j ON j.job_id = r.job_id WHERE r.status IN ('needs_review', 'ready') ORDER BY r.created_at LIMIT 100"""
                ):
                    unanswered = loads(row["unanswered"], [])
                    items.append(
                        {
                            "candidate_id": target,
                            "candidate": member["name"] or member["email"],
                            "run_id": row["run_id"],
                            "status": row["status"],
                            "job_id": row["job_id"],
                            "title": row["title"],
                            "company": row["company"],
                            "questions": [
                                q.get("label") if isinstance(q, dict) else str(q)
                                for q in unanswered
                            ][:20],
                            "held_since": row["finished_at"] or row["created_at"],
                            "scope": scope,
                            "link": f"#/jobs/autopilot?candidate={target}",
                        }
                    )
            except sqlite3.Error:
                continue
            finally:
                connection.close()
        items.sort(key=lambda item: _to_epoch(item["held_since"]) or 0)
        return items


__all__ = ["Dashboard", "json", "one"]
