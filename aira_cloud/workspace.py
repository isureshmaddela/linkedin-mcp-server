"""Candidate workspaces: where they live, how a thread is bound to one, and what the dashboard reads from them.

A candidate's records (profile, jobs, plans, runs, applications, interviews, documents, recruiters,
inbox) are the existing per-account SQLite database, now placed under
``tenants/<org_id>/<candidate_id>/`` so one organization's data is a single directory tree that can
be exported or deleted as a unit. The domain modules run against it unchanged.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira_cloud import context, redaction
from aira_cloud.db import dumps, loads, many, one
from aira_cloud.errors import NotFound
from aira_cloud.rbac import Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.workspace")

STAGES = ("onboarding", "searching", "applying", "interviewing", "offers", "placed")
ORG_API_DIR = "_org"


def _local() -> Any:
    import auto_apply_assistant

    return auto_apply_assistant


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="minutes")


class WorkspaceManager:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    # -- paths ------------------------------------------------------------------------

    def org_dir(self, org_id: str) -> Path:
        return self.app.config.tenants_root / org_id

    def candidate_dir(self, org_id: str, candidate_id: str) -> Path:
        return self.org_dir(org_id) / candidate_id

    def data_path(self, org_id: str, candidate_id: str) -> Path:
        return self.candidate_dir(org_id, candidate_id) / "workspace.sqlite3"

    def org_api_path(self, org_id: str) -> Path:
        return self.org_dir(org_id) / ORG_API_DIR / "api.sqlite3"

    # -- lifecycle ----------------------------------------------------------------------

    def create(
        self,
        org_id: str,
        candidate_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Path:
        path = self.data_path(org_id, candidate_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = self.app.clock.now()

        def insert(db_connection: sqlite3.Connection) -> None:
            db_connection.execute(
                """INSERT OR IGNORE INTO workspaces (candidate_id, org_id, data_path, status, stage, stage_changed_at, created_at)
                VALUES (?, ?, ?, 'active', 'onboarding', ?, ?)""",
                (candidate_id, org_id, str(path), now, now),
            )
            db_connection.execute(
                "INSERT INTO candidate_stage_log (org_id, candidate_id, stage, at) VALUES (?, ?, 'onboarding', ?)",
                (org_id, candidate_id, now),
            )

        if connection is not None:
            insert(connection)
        else:
            with self.app.db.write() as write_connection:
                insert(write_connection)
        self._migrate(path)
        return path

    def _migrate(self, path: Path) -> None:
        from aira.schema import LATEST_VERSION, migrate, schema_version

        connection = sqlite3.connect(path, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
            if schema_version(connection) < LATEST_VERSION:
                migrate(connection)
            connection.commit()
        finally:
            connection.close()

    def row(self, org_id: str, candidate_id: str) -> dict[str, Any]:
        with self.app.db.read() as connection:
            workspace = one(
                connection,
                "SELECT * FROM workspaces WHERE candidate_id = ? AND org_id = ?",
                (candidate_id, org_id),
            )
        if workspace is None:
            raise NotFound("Candidate not found.")
        return workspace

    def stop_automation(self, org_id: str, candidate_id: str) -> None:
        """Disable every plan, schedule and monitor in the workspace, drop queued jobs and stop its LinkedIn worker."""
        try:
            workspace = self.row(org_id, candidate_id)
        except NotFound:
            return
        path = Path(workspace["data_path"])
        if path.exists():
            connection = sqlite3.connect(path, timeout=15)
            try:
                for statement in (
                    "UPDATE autopilot_plans SET enabled = 0",
                    "UPDATE schedules SET enabled = 0",
                    "UPDATE airaapi_monitors SET enabled = 0",
                ):
                    try:
                        connection.execute(statement)
                    except sqlite3.OperationalError:
                        continue
                connection.commit()
            finally:
                connection.close()
        self.app.queue.cancel_for(org_id, candidate_id)
        try:
            _local()._stop_linkedin_worker(
                {
                    "user_id": candidate_id,
                    "linkedin_port": workspace.get("linkedin_port"),
                }
            )
        except Exception:  # noqa: BLE001 - a worker that is already gone is fine
            logger.debug("No LinkedIn worker to stop for %s", candidate_id)
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET linkedin_port = NULL, login_pid = NULL, worker_pid = NULL WHERE candidate_id = ?",
                (candidate_id,),
            )

    def retention_ceiling(self, org_id: str) -> int:
        """Days an organization's documents may be kept: the lower of its plan limit and its policy (0: no ceiling)."""
        days = [
            int(d)
            for d in (
                self.app.entitlements.resolve(org_id).limit("retention_days"),
                self.app.tenancy.policy(org_id).get("retention_days"),
            )
            if d
        ]
        return max(30, min(days)) if days else 0

    def purge_expired_documents(self, org_id: str) -> int:
        """Apply document retention across the organization's workspaces; return how many documents were purged."""
        local = _local()
        with self.app.db.read() as connection:
            rows = many(
                connection,
                "SELECT candidate_id FROM workspaces WHERE org_id = ? AND status = 'active'",
                (org_id,),
            )
        ceiling = self.retention_ceiling(org_id)
        purged = 0
        for row in rows:
            try:
                with self.bind(
                    org_id, row["candidate_id"], kind="system", source="retention"
                ):
                    with local._connect() as connection:
                        if ceiling:
                            # a document is stamped with the retention in force when it was uploaded; a shorter
                            # policy or plan limit set since then applies to it as well
                            connection.execute(
                                "UPDATE documents SET retention_days = ? WHERE deleted_at IS NULL AND (retention_days IS NULL OR retention_days > ?)",
                                (ceiling, ceiling),
                            )
                            connection.commit()
                        purged += int(local._document_store().purge_expired(connection))
            except Exception:  # noqa: BLE001 - one workspace's failure does not stop retention elsewhere
                logger.warning(
                    "Document retention failed for %s",
                    row["candidate_id"],
                    exc_info=True,
                )
        return purged

    def delete(self, org_id: str, candidate_id: str) -> None:
        """Remove a workspace's database, documents and browser profile, and stop its LinkedIn session."""
        local = _local()
        try:
            workspace = self.row(org_id, candidate_id)
            local._stop_linkedin_worker(
                {
                    "user_id": candidate_id,
                    "linkedin_port": workspace.get("linkedin_port"),
                }
            )
        except Exception:  # noqa: BLE001 - deletion continues even if a worker is already gone
            logger.debug("No LinkedIn worker to stop for %s", candidate_id)
        shutil.rmtree(self.candidate_dir(org_id, candidate_id), ignore_errors=True)
        self.app.storage.delete_prefix(org_id, candidate_id)
        with self.app.db.write() as connection:
            connection.execute(
                "DELETE FROM workspaces WHERE candidate_id = ? AND org_id = ?",
                (candidate_id, org_id),
            )
            connection.execute(
                "DELETE FROM candidate_stage_log WHERE candidate_id = ? AND org_id = ?",
                (candidate_id, org_id),
            )

    def delete_org_tree(self, org_id: str) -> None:
        shutil.rmtree(self.org_dir(org_id), ignore_errors=True)
        self.app.storage.delete_prefix(org_id, "")

    # -- binding -------------------------------------------------------------------------

    def user_dict(self, org_id: str, candidate_id: str) -> dict[str, Any]:
        with self.app.db.read() as connection:
            row = one(
                connection,
                """SELECT w.*, m.email AS email FROM workspaces w JOIN members m ON m.member_id = w.candidate_id
                WHERE w.candidate_id = ? AND w.org_id = ?""",
                (candidate_id, org_id),
            )
        if row is None:
            raise NotFound("Candidate not found.")
        return {
            "user_id": candidate_id,
            "email": row["email"],
            "data_path": row["data_path"],
            "linkedin_port": row["linkedin_port"],
            "created_at": row["created_at"],
            "password_hash": "",
            "org_id": org_id,
        }

    @contextmanager
    def bind(
        self,
        org_id: str,
        candidate_id: str,
        *,
        principal: Principal | None = None,
        kind: str = "request",
        source: str = "user",
        correlation_id: str = "",
        audience: str = "staff",
        acting_label: str = "",
    ) -> Iterator[dict[str, Any]]:
        """Bind this thread to one candidate's workspace for the domain code, and unbind afterwards."""
        local = _local()
        user = self.user_dict(org_id, candidate_id)
        ctx = context.TenantContext(
            org_id=org_id,
            candidate_id=candidate_id,
            kind=kind,
            principal=principal,
            correlation_id=correlation_id,
            audience=audience,
        )
        with context.bound(ctx):
            local._bind_user(user)
            label = acting_label or (principal.label if principal else f"system:{kind}")
            local._request_context.actor = label[:120]
            local._request_context.source = source
            if correlation_id:
                local._request_context.correlation_id = correlation_id
            try:
                yield user
            finally:
                local._unbind_user()
                if correlation_id and hasattr(local._request_context, "correlation_id"):
                    del local._request_context.correlation_id

    @contextmanager
    def bind_org_api(
        self,
        org_id: str,
        *,
        principal: Principal | None = None,
        correlation_id: str = "",
    ) -> Iterator[dict[str, Any]]:
        """Bind to the organization's API workspace (history, monitors, keys shared across its candidates)."""
        local = _local()
        path = self.org_api_path(org_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        user = {
            "user_id": f"org-{org_id}",
            "email": principal.email if principal else "",
            "data_path": str(path),
            "linkedin_port": None,
            "created_at": 0,
            "password_hash": "",
            "org_id": org_id,
        }
        ctx = context.TenantContext(
            org_id=org_id,
            candidate_id="",
            kind="org_api",
            principal=principal,
            correlation_id=correlation_id,
        )
        with context.bound(ctx):
            local._bind_user(user)
            local._request_context.actor = (
                principal.label if principal else "system:org_api"
            )[:120]
            if correlation_id:
                local._request_context.correlation_id = correlation_id
            try:
                yield user
            finally:
                local._unbind_user()
                if correlation_id and hasattr(local._request_context, "correlation_id"):
                    del local._request_context.correlation_id

    def org_api_connection(self, org_id: str) -> sqlite3.Connection:
        """A connection to the organization's shared API workspace (search history and cache for all its candidates)."""
        local = _local()
        path = self.org_api_path(org_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=15, factory=local._ManagedConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        try:
            from aira.schema import LATEST_VERSION, migrate, schema_version

            if schema_version(connection) < LATEST_VERSION:
                with local._migration_lock:
                    migrate(connection)
        except BaseException:
            connection.rollback()
            connection.close()
            raise
        return connection

    # -- snapshots ------------------------------------------------------------------------

    def _read_connection(self, path: Path) -> sqlite3.Connection | None:
        if not Path(path).exists():
            return None
        connection = sqlite3.connect(
            f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def compute_snapshot(self, org_id: str, candidate_id: str) -> dict[str, Any]:
        """Read one candidate's workspace into the small summary the dashboard and rules use."""
        workspace = self.row(org_id, candidate_id)
        now = self.app.clock.now()
        snapshot: dict[str, Any] = {
            "computed_at": now,
            "profile_ready": False,
            "resume": False,
            "resume_file": False,
            "answers_confirmed": False,
            "jobs": 0,
            "plans": 0,
            "plans_enabled": 0,
            "plans_auto": 0,
            "monitors": 0,
            "submissions": 0,
            "submissions_30d": 0,
            "interviews": 0,
            "upcoming_interviews": 0,
            "offers": 0,
            "placed": 0,
            "held": 0,
            "oldest_held_at": None,
            "ready": 0,
            "failed_24h": 0,
            "runs_today": {},
            "documents_bytes": 0,
            "leads": 0,
            "last_activity_at": None,
            "by_source": {},
            "by_resume": {},
            "next_due_at": None,
        }
        connection = self._read_connection(Path(workspace["data_path"]))
        if connection is None:
            return snapshot
        try:
            settings = {
                r["key"]: r["value"]
                for r in connection.execute("SELECT key, value FROM settings")
            }
            snapshot["profile_ready"] = all(
                settings.get(f"application_{f}")
                for f in ("full_name", "email", "phone", "location")
            )
            snapshot["resume"] = bool(settings.get("resume"))

            def scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
                return connection.execute(sql, params).fetchone()[0]

            snapshot["resume_file"] = bool(
                scalar(
                    "SELECT COUNT(*) FROM documents WHERE kind = 'master_resume' AND deleted_at IS NULL"
                )
            )
            snapshot["documents_bytes"] = int(
                scalar(
                    "SELECT COALESCE(SUM(size), 0) FROM documents WHERE deleted_at IS NULL"
                )
            )
            snapshot["answers_confirmed"] = bool(
                scalar(
                    "SELECT COUNT(*) FROM standard_answers WHERE confirmed = 1 AND canonical_field IN ('work_authorization', 'sponsorship_required')"
                )
                >= 1
            )
            snapshot["jobs"] = int(
                scalar("SELECT COUNT(*) FROM jobs WHERE archived_at IS NULL")
            )
            snapshot["plans"] = int(scalar("SELECT COUNT(*) FROM autopilot_plans"))
            snapshot["plans_enabled"] = int(
                scalar("SELECT COUNT(*) FROM autopilot_plans WHERE enabled = 1")
            )
            snapshot["plans_auto"] = int(
                scalar(
                    "SELECT COUNT(*) FROM autopilot_plans WHERE enabled = 1 AND mode = 'apply'"
                )
            )
            snapshot["monitors"] = int(
                scalar("SELECT COUNT(*) FROM airaapi_monitors WHERE enabled = 1")
            )
            snapshot["submissions"] = int(
                scalar("SELECT COUNT(*) FROM application_submissions")
            )
            snapshot["submissions_30d"] = int(
                scalar(
                    "SELECT COUNT(*) FROM application_submissions WHERE submitted_at >= ?",
                    (_iso(now - 30 * 86400),),
                )
            )
            snapshot["interviews"] = int(
                scalar("SELECT COUNT(*) FROM interviews WHERE status != 'cancelled'")
            )
            snapshot["upcoming_interviews"] = int(
                scalar(
                    "SELECT COUNT(*) FROM interviews WHERE status != 'cancelled' AND start_at >= ?",
                    (_iso(now),),
                )
            )
            snapshot["offers"] = int(
                scalar("SELECT COUNT(*) FROM jobs WHERE stage = 'offer'")
            )
            snapshot["placed"] = int(
                scalar("SELECT COUNT(*) FROM jobs WHERE stage = 'accepted'")
            )
            held = connection.execute(
                "SELECT COUNT(*) AS n, MIN(COALESCE(finished_at, created_at)) AS oldest FROM application_runs WHERE status = 'needs_review'"
            ).fetchone()
            snapshot["held"] = int(held["n"])
            snapshot["oldest_held_at"] = held["oldest"]
            snapshot["ready"] = int(
                scalar("SELECT COUNT(*) FROM application_runs WHERE status = 'ready'")
            )
            snapshot["failed_24h"] = int(
                scalar(
                    "SELECT COUNT(*) FROM application_runs WHERE status = 'failed' AND COALESCE(finished_at, created_at) >= ?",
                    (_iso(now - 86400),),
                )
            )
            snapshot["next_due_at"] = connection.execute(
                "SELECT MIN(next_run_at) FROM autopilot_plans WHERE enabled = 1"
            ).fetchone()[0]
            day_start = datetime.fromtimestamp(now, timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            runs: dict[str, dict[str, int]] = {}
            for row in connection.execute(
                """SELECT COALESCE(j.source, 'unknown') AS source, r.status AS status, COUNT(*) AS n FROM application_runs r
                LEFT JOIN jobs j ON j.job_id = r.job_id WHERE r.created_at >= ? GROUP BY 1, 2""",
                (day_start.isoformat(timespec="seconds"),),
            ):
                runs.setdefault(row["source"], {})[row["status"]] = int(row["n"])
            snapshot["runs_today"] = runs
            by_source: dict[str, dict[str, int]] = {}
            by_resume: dict[str, dict[str, int]] = {}
            for row in connection.execute(
                """SELECT COALESCE(j.source, 'unknown') AS source, COALESCE(j.resume_hash, '') AS resume_hash,
                COUNT(s.submission_id) AS applications,
                SUM(CASE WHEN EXISTS (SELECT 1 FROM interviews i WHERE i.job_id = j.job_id AND i.status != 'cancelled') THEN 1 ELSE 0 END) AS interviews,
                SUM(CASE WHEN j.stage IN ('offer', 'accepted') THEN 1 ELSE 0 END) AS offers
                FROM jobs j JOIN application_submissions s ON s.job_id = j.job_id GROUP BY 1, 2"""
            ):
                for bucket, key in (
                    (by_source, row["source"]),
                    (by_resume, row["resume_hash"][:12] or "unknown"),
                ):
                    entry = bucket.setdefault(
                        key, {"applications": 0, "interviews": 0, "offers": 0}
                    )
                    entry["applications"] += int(row["applications"])
                    entry["interviews"] += int(row["interviews"] or 0)
                    entry["offers"] += int(row["offers"] or 0)
            snapshot["by_source"], snapshot["by_resume"] = by_source, by_resume
            try:  # a workspace made before Aira Leads has no leads table yet
                snapshot["leads"] = int(scalar("SELECT COUNT(*) FROM leads"))
            except sqlite3.Error:
                snapshot["leads"] = 0
            last = connection.execute("SELECT MAX(at) FROM activity_log").fetchone()[0]
            snapshot["last_activity_at"] = last
        except sqlite3.Error:
            logger.warning(
                "Could not read the workspace snapshot for %s",
                candidate_id,
                exc_info=True,
            )
        finally:
            connection.close()
        return snapshot

    def leads_total(self, org_id: str, *, refresh_candidate: str = "") -> int:
        """Saved leads across the organization, from workspace snapshots (the writing candidate's is refreshed)."""
        if refresh_candidate:
            self.refresh(org_id, refresh_candidate, max_age=0)
        with self.app.db.read() as connection:
            rows = connection.execute(
                "SELECT candidate_id FROM workspaces WHERE org_id = ? AND status != 'deleted'",
                (org_id,),
            ).fetchall()
        total = 0
        for row in rows:
            try:
                total += int(
                    self.refresh(org_id, row["candidate_id"], max_age=60).get(
                        "leads", 0
                    )
                )
            except Exception:  # noqa: BLE001 - a missing workspace does not count
                continue
        return total

    @staticmethod
    def stage_of(snapshot: dict[str, Any], override: str = "") -> str:
        if override in STAGES:
            return override
        if snapshot["placed"]:
            return "placed"
        if snapshot["offers"]:
            return "offers"
        if snapshot["interviews"]:
            return "interviewing"
        if snapshot["submissions"]:
            return "applying"
        if (
            snapshot["profile_ready"]
            and snapshot["resume"]
            and (snapshot["jobs"] or snapshot["plans"])
        ):
            return "searching"
        if (
            snapshot["plans_enabled"]
            and snapshot["profile_ready"]
            and snapshot["resume"]
        ):
            return "searching"
        return "onboarding"

    def refresh(
        self, org_id: str, candidate_id: str, *, max_age: int = 60
    ) -> dict[str, Any]:
        """The candidate's snapshot, recomputed when older than ``max_age`` seconds; keeps the stage in step."""
        workspace = self.row(org_id, candidate_id)
        now = self.app.clock.now()
        cached = loads(workspace["snapshot"], {})
        if (
            cached
            and workspace["snapshot_at"]
            and now - int(workspace["snapshot_at"]) < max_age
        ):
            return cached
        snapshot = self.compute_snapshot(org_id, candidate_id)
        stage = self.stage_of(snapshot, workspace["stage_override"])
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET snapshot = ?, snapshot_at = ?, last_activity_at = ?, next_due_at = ? WHERE candidate_id = ? AND org_id = ?",
                (
                    dumps(snapshot),
                    now,
                    snapshot["last_activity_at"]
                    and _to_epoch(snapshot["last_activity_at"]),
                    _to_epoch(snapshot["next_due_at"]),
                    candidate_id,
                    org_id,
                ),
            )
            if stage != workspace["stage"]:
                connection.execute(
                    "UPDATE workspaces SET stage = ?, stage_changed_at = ? WHERE candidate_id = ? AND org_id = ?",
                    (stage, now, candidate_id, org_id),
                )
                connection.execute(
                    "INSERT INTO candidate_stage_log (org_id, candidate_id, stage, at) VALUES (?, ?, ?, ?)",
                    (org_id, candidate_id, stage, now),
                )
        return snapshot

    def invalidate_snapshot(self, org_id: str, candidate_id: str) -> None:
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET snapshot_at = NULL WHERE candidate_id = ? AND org_id = ?",
                (candidate_id, org_id),
            )

    # -- export ---------------------------------------------------------------------------

    def export_zip(
        self,
        org_id: str,
        candidate_id: str,
        principal: Principal | None = None,
        *,
        full: bool = False,
    ) -> bytes:
        """The candidate's tables as JSON and every live document, decrypted, in one archive.

        Sensitive profile values are masked unless the export is the candidate's own (``full``, or no
        requesting principal) or the requester may see them.
        """
        local = _local()
        buffer = io.BytesIO()
        with self.bind(
            org_id, candidate_id, principal=principal, kind="system", source="export"
        ):
            data = local._export_account()
            if (
                not full
                and principal is not None
                and not self.app.authz.can_see_sensitive(principal, candidate_id)
            ):
                data = redaction.redact_export(data)
            store = local._document_store()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "workspace.json",
                    json.dumps(data, ensure_ascii=False, indent=2, default=str),
                )
                with local._connect() as connection:
                    documents = [
                        dict(r)
                        for r in connection.execute(
                            "SELECT document_id, filename FROM documents WHERE deleted_at IS NULL"
                        )
                    ]
                for document in documents:
                    try:
                        with local._connect() as connection:
                            _record, content = store.read(
                                connection,
                                owner_id=candidate_id,
                                document_id=document["document_id"],
                                actor="export",
                            )
                    except Exception:  # noqa: BLE001 - one unreadable document must not fail the export
                        logger.warning(
                            "Could not export document %s",
                            document["document_id"],
                            exc_info=True,
                        )
                        continue
                    safe = "".join(
                        ch if ch.isalnum() or ch in "._- " else "_"
                        for ch in document["filename"]
                    )[:120]
                    archive.writestr(
                        f"documents/{document['document_id'][:8]}-{safe}", content
                    )
        return buffer.getvalue()


def _to_epoch(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class ControlWorkerRegistry:
    """LinkedIn worker state for hosted candidates: profile directories on the worker volume, process ids in the control plane."""

    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def _org_of(self, candidate_id: str) -> str:
        with self.app.db.read() as connection:
            row = one(
                connection,
                "SELECT org_id FROM workspaces WHERE candidate_id = ?",
                (candidate_id,),
            )
        if row is None:
            raise NotFound("Candidate not found.")
        return str(row["org_id"])

    def profile_dir(self, user_id: str) -> Path:
        org_id = self._org_of(user_id)
        return (
            self.app.workspaces.candidate_dir(org_id, user_id) / "linkedin" / "profile"
        )

    def update(
        self,
        user_id: str,
        *,
        port: int | None,
        login_pid: int | None,
        worker_pid: int | None,
    ) -> None:
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET linkedin_port = ?, login_pid = ?, worker_pid = ? WHERE candidate_id = ?",
                (port, login_pid, worker_pid, user_id),
            )

    def read(self, user_id: str) -> tuple[int | None, int | None]:
        with self.app.db.read() as connection:
            row = one(
                connection,
                "SELECT worker_pid, linkedin_port FROM workspaces WHERE candidate_id = ?",
                (user_id,),
            )
        return (row["worker_pid"], row["linkedin_port"]) if row else (None, None)


__all__ = [
    "WorkspaceManager",
    "ControlWorkerRegistry",
    "STAGES",
    "many",
    "timedelta",
    "time",
]
