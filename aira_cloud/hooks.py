"""Installs the hosting hooks the shared domain code consults (see ``aira.hosting``).

Every hook reads the tenant bound to the current thread, so a request or a worker job can only ever
reach its own organization's documents, key, settings and limits. With no tenant bound a hook
raises instead of falling back to a default, which fails closed.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira.hosting import HOOKS
from aira_cloud import context, pooled
from aira_cloud.db import one
from aira_cloud.errors import SubscriptionInactive
from aira_cloud.kms import KeyProviderError
from aira_cloud.rbac import CANDIDATE, Principal
from aira_cloud.workspace import ControlWorkerRegistry

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.hooks")


def _iso(timestamp: int | None) -> str:
    return datetime.fromtimestamp(timestamp or 0, timezone.utc).isoformat(
        timespec="seconds"
    )


class ControlKeyIndex:
    """Indexes an organization's API keys in the control plane so ``/v1`` can find their owner."""

    def __init__(self, app: CloudApp, org_id: str) -> None:
        self.app = app
        self.org_id = org_id

    def add(self, key_hash: str) -> None:
        from aira.airaapi import store as airaapi_store

        with self.app.db.write() as connection:
            airaapi_store.index_key(connection, key_hash, self.org_id)

    def remove(self, key_hash: str) -> None:
        from aira.airaapi import store as airaapi_store

        with self.app.db.write() as connection:
            airaapi_store.unindex_key(connection, key_hash)


def principal_payload(principal: Principal | None) -> dict[str, Any]:
    if principal is None:
        return {}
    return {
        "member_id": principal.member_id,
        "operator_id": principal.operator_id,
        "org_id": principal.org_id,
        "role": principal.role,
        "email": principal.email,
        "kind": principal.kind,
    }


def principal_from(payload: Mapping[str, Any] | None) -> Principal | None:
    if not payload:
        return None
    return Principal(
        kind=str(payload.get("kind") or "member"),
        org_id=str(payload.get("org_id") or ""),
        member_id=str(payload.get("member_id") or ""),
        operator_id=str(payload.get("operator_id") or ""),
        role=str(payload.get("role") or ""),
        email=str(payload.get("email") or ""),
    )


def install(app: CloudApp) -> None:
    ctx_org = lambda: context.require().org_id  # noqa: E731

    HOOKS.worker_registry = ControlWorkerRegistry(app)
    HOOKS.documents = lambda: (
        app.storage.backend(ctx_org()),
        app.secrets.fernet(ctx_org()),
    )
    HOOKS.secret_key = lambda: app.secrets.key(ctx_org())
    HOOKS.model_json = app.models.complete_json
    HOOKS.wrap_services = app.gate.wrap_services

    def settings_overlay(merged: dict[str, str]) -> dict[str, str]:
        ctx = context.current()
        if ctx is None or not ctx.org_id:
            return merged
        org_id, candidate_id = ctx.org_id, ctx.candidate_id
        out = dict(merged)
        try:
            entitlements = app.entitlements.resolve(org_id)
            policy = app.tenancy.policy(org_id)
        except Exception:  # noqa: BLE001
            return out
        caps = [
            int(c)
            for c in (
                entitlements.limit("apps_per_candidate_day"),
                policy.get("daily_cap_per_candidate"),
            )
            if c
        ]
        try:
            own = int(out.get("daily_application_limit") or 0)
        except ValueError:
            own = 0
        if own > 0:
            caps.append(own)
        if caps:
            out["daily_application_limit"] = str(min(caps))
        retention = [
            int(r)
            for r in (
                entitlements.limit("retention_days"),
                policy.get("retention_days"),
            )
            if r
        ]
        try:
            retention.append(int(out.get("retention_days") or 0) or 3650)
        except ValueError:
            pass
        if retention:
            out["retention_days"] = str(max(30, min(retention)))
        linkedin = bool(policy.get("linkedin_allowed")) and entitlements.has(
            "sources.linkedin"
        )
        if not linkedin:
            out["auto_reply_paused"] = "1"
        out["linkedin_show_browser"] = "0"
        if candidate_id and app.gate.automation_block(
            org_id, candidate_id, consent=False
        ):
            out["auto_apply_paused"] = "1"
        if app.config.model_name and app.config.model_provider != "ollama":
            out["default_model"] = app.config.model_name
        return out

    HOOKS.settings_overlay = settings_overlay

    def serpapi_key() -> tuple[str | None, str]:
        ctx = context.require()
        org_id = ctx.org_id
        key = _org_serpapi_key(app, org_id)
        if key:
            return key, "app"
        policy = app.tenancy.policy(org_id)
        if (
            policy.get("use_pooled_serpapi")
            and app.entitlements.resolve(org_id).has("pooled_serpapi")
            and app.config.pooled_serpapi_key
        ):
            return app.config.pooled_serpapi_key, "pooled"
        return None, ""

    HOOKS.serpapi_key = serpapi_key

    def on_submission(
        connection: sqlite3.Connection, submission: Mapping[str, Any]
    ) -> None:
        ctx = context.current()
        if ctx is None or not ctx.org_id:
            return
        if ctx.scratch.pop("reserved", False):
            pass
        else:
            app.usage.record(
                ctx.org_id, "applications", 1, candidate_id=ctx.candidate_id
            )
        principal = ctx.principal
        app.audit.record(
            ctx.org_id,
            "application.submit",
            actor=principal,
            actor_type="" if principal else "system",
            actor_label="" if principal else f"system:{ctx.kind}",
            candidate_id=ctx.candidate_id,
            target_type="job",
            target_id=str(submission.get("job_id", "")),
            detail={
                "method": submission.get("method"),
                "confirmation_number": submission.get("confirmation_number"),
                "has_evidence": bool(submission.get("evidence_document_id")),
            },
            source=ctx.kind,
            correlation_id=ctx.correlation_id,
        )
        if ctx.candidate_id:
            app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)

    HOOKS.submission_listeners.append(on_submission)

    def start_background(
        user: Mapping[str, Any], label: str, spec: Mapping[str, Any]
    ) -> dict[str, Any]:
        ctx = context.require()
        block = app.gate.automation_block(ctx.org_id, ctx.candidate_id)
        if block:
            raise SubscriptionInactive(block[1], code_detail=block[0])
        if app.queue.active_for(ctx.org_id, ctx.candidate_id):
            raise ValueError(
                "Another automation (an inbox check or a run) is busy for your account. Try again in a minute."
            )
        job_id = app.queue.enqueue(
            ctx.org_id,
            "autopilot.exec",
            candidate_id=ctx.candidate_id,
            payload={
                "spec": dict(spec),
                "label": label,
                "principal": principal_payload(ctx.principal),
                "correlation_id": ctx.correlation_id,
            },
            priority=3,
            host=app.agents.target(ctx.org_id, ctx.candidate_id, assign=False),
        )
        return {
            "started": True,
            "queued": True,
            "label": label,
            "started_at": _iso(app.clock.now()),
            "job_id": job_id,
        }

    HOOKS.start_background = start_background

    def automation_status(user_id: str) -> dict[str, Any] | None:
        ctx = context.current()
        if ctx is None:
            return None
        job = app.queue.active_for(ctx.org_id, user_id)
        if job is None:
            return None
        running = job["status"] == "leased"
        return {
            "label": job["payload"].get("label") or "Automation",
            "started_at": _iso(job["started_at"] or job["created_at"]),
            "detail": (job["result"] or "Working")
            if running
            else "Waiting for a worker",
        }

    HOOKS.automation_status = automation_status

    def progress(label: str) -> None:
        ctx = context.current()
        job_id = ctx.scratch.get("job_id") if ctx else None
        if job_id:
            app.queue.heartbeat(job_id, progress=label)

    HOOKS.progress = progress

    def max_plans() -> int | None:
        ctx = context.current()
        if ctx is None or not ctx.org_id:
            return None
        limit = app.entitlements.resolve(ctx.org_id).limit("search_plans_per_candidate")
        return (
            int(limit) if limit else None
        )  # the gateway enforces the entitlement; this lifts the local cap of 10

    HOOKS.max_plans = max_plans
    HOOKS.browser_time_cap = lambda: app.config.browser_time_cap_seconds
    HOOKS.search_connect = lambda: app.workspaces.org_api_connection(ctx_org())
    HOOKS.search_credits = pooled.note
    HOOKS.key_index = lambda user_id: ControlKeyIndex(app, user_id.removeprefix("org-"))


def _org_serpapi_key(app: CloudApp, org_id: str) -> str | None:
    """The organization's own SerpApi key from its API workspace, decrypted with its data key."""
    from aira.airaapi import service as airaapi_service
    from aira.airaapi import store as airaapi_store

    path = app.workspaces.org_api_path(org_id)
    if not Path(path).exists():
        return None
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        store = airaapi_store.SecretStore(app.secrets.key(org_id))
        return store.get(connection, airaapi_service.APP_KEY_SECRET) or None
    except KeyProviderError:
        raise  # an unreachable KMS is an outage, not "no key"
    except Exception:  # noqa: BLE001 - a missing or unreadable key means "no key"
        return None
    finally:
        connection.close()


def uninstall() -> None:
    HOOKS.reset()


__all__ = [
    "install",
    "uninstall",
    "principal_from",
    "principal_payload",
    "CANDIDATE",
    "one",
]
