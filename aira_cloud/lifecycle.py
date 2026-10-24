"""Limits after plan changes, exports, organization closure and deletion, and the scheduler tick."""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira_cloud import crypto
from aira_cloud.config import DAY
from aira_cloud.db import (
    defer_foreign_keys,
    dumps,
    many,
    new_id,
    one,
    table_columns,
    table_names,
)
from aira_cloud.errors import Conflict, Forbidden, Gone, NotFound, ValidationFailed
from aira_cloud.rbac import Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.lifecycle")

# Tables that survive an organization's deletion: the billing record and the platform's own history.
RETAINED_TABLES = frozenset(
    {
        "invoices",
        "billing_events",
        "billing_credits",
        "subscription_events",
        "subscriptions",
        "organizations",
        "audit_stubs",
        "platform_audit_logs",
        "data_subject_requests",
        "cloud_kv",
        "cloud_migrations",
        "plans",
        "plan_versions",
        "plan_migrations",
        "feature_flags",
        "announcements",
        "operators",
        "blocked_sources",
        "worker_heartbeats",
    }
)
AUDIT_STUB_RETENTION_DAYS = 7 * 365


def _holds_unmasked_data(row: dict[str, Any]) -> bool:
    """Exports that carry a candidate's sensitive values in full: their own, a departure hand-over, a data-subject request."""
    created_by = str(row.get("created_by") or "")
    return bool(
        row.get("kind") == "candidate_departure"
        or created_by.startswith("dsr:")
        or (row.get("candidate_id") and created_by == row.get("candidate_id"))
    )


class Lifecycle:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ limits

    def max_plans_per_candidate(self, org_id: str) -> int:
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT candidate_id FROM workspaces WHERE org_id = ? AND status = 'active'",
                (org_id,),
            )
        best = 0
        for row in rows:
            try:
                snapshot = self.app.workspaces.refresh(
                    org_id, row["candidate_id"], max_age=300
                )
            except NotFound:
                continue
            best = max(best, int(snapshot.get("plans", 0)))
        return best

    def recompute_over_limits(self, org_id: str) -> list[str]:
        """Which plan limits the organization exceeds; automation pauses while the list is not empty."""
        entitlements = self.app.entitlements.resolve(org_id, fresh=True)
        used = self.app.tenancy.seat_usage(org_id)
        over: list[str] = []
        limit = entitlements.limit("candidate_seats")
        if limit is not None and used["candidates"] > limit:
            over.append(f"candidate seats ({used['candidates']} of {limit})")
        limit = entitlements.limit("staff_seats")
        if limit is not None and used["staff"] > limit:
            over.append(f"coach and admin seats ({used['staff']} of {limit})")
        limit = entitlements.limit("search_plans_per_candidate")
        if limit is not None:
            most = self.max_plans_per_candidate(org_id)
            if most > limit:
                over.append(f"search plans per candidate ({most} of {limit})")
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT over_limits FROM subscriptions WHERE org_id = ?",
                (org_id,),
            )
            if row is None:
                return over
            before = json.loads(row["over_limits"] or "[]")
            if before != over:
                connection.execute(
                    "UPDATE subscriptions SET over_limits = ?, updated_at = ? WHERE org_id = ?",
                    (dumps(over), self.now, org_id),
                )
                self.app.audit.record(
                    org_id,
                    "automation.paused_over_limits"
                    if over
                    else "automation.resumed_within_limits",
                    actor_type="system",
                    source="scheduler",
                    detail={"limits": over},
                    connection=connection,
                )
        self.app.entitlements.invalidate(org_id)
        if over and not before:
            self.app.billing.notice(
                org_id,
                "warning",
                "Automation paused",
                "Over plan limits: " + ", ".join(over) + ".",
                "#/org/members",
            )
        elif before and not over:
            self.app.billing.notice(
                org_id,
                "success",
                "Automation resumed",
                "The organization is within its plan limits again.",
                "#/org/dashboard",
            )
        return over

    # ------------------------------------------------------------------ exports

    def exports_dir(self, org_id: str) -> Path:
        return self.app.workspaces.org_dir(org_id) / "_exports"

    def create_export(
        self,
        org_id: str,
        *,
        candidate_id: str = "",
        kind: str,
        created_by: str,
        recipient: str = "",
        principal: Principal | None = None,
        ttl_days: int = 30,
        full: bool = False,
    ) -> dict[str, Any]:
        """Build an archive (JSON plus documents) and register a download. ``candidate_id`` empty = the whole organization."""
        export_id = new_id()
        token = crypto.new_token()
        if candidate_id:
            data = self.app.workspaces.export_zip(
                org_id, candidate_id, principal, full=full
            )
        else:
            data = self._organization_archive(org_id, principal)
        target = self.exports_dir(org_id)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{export_id}.zip"
        path.write_bytes(data)
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO exports (export_id, org_id, candidate_id, kind, token_hash, path, size, recipient, created_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    export_id,
                    org_id,
                    candidate_id,
                    kind,
                    crypto.hash_token(token),
                    str(path),
                    len(data),
                    recipient,
                    created_by,
                    self.now,
                    self.now + ttl_days * DAY,
                ),
            )
        return {
            "export_id": export_id,
            "token": token,
            "size": len(data),
            "expires_at": self.now + ttl_days * DAY,
        }

    def _organization_archive(self, org_id: str, principal: Principal | None) -> bytes:
        org = self.app.tenancy.org(org_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            with self.db.read() as connection:
                members = many(
                    connection,
                    "SELECT member_id, email, name, role, status, created_at, last_sign_in_at FROM members WHERE org_id = ? AND removed_at IS NULL",
                    (org_id,),
                )
                assignments = many(
                    connection,
                    "SELECT coach_id, candidate_id, scope FROM coach_assignments WHERE org_id = ?",
                    (org_id,),
                )
                invoices = many(
                    connection,
                    "SELECT invoice_id, kind, amount_cents, currency, status, created_at FROM invoices WHERE org_id = ?",
                    (org_id,),
                )
                candidates = many(
                    connection,
                    "SELECT candidate_id, status FROM workspaces WHERE org_id = ?",
                    (org_id,),
                )
                departed = many(
                    connection,
                    "SELECT candidate_id, removed_at, data_policy, stage, snapshot FROM departed_candidates WHERE org_id = ?",
                    (org_id,),
                )
            archive.writestr(
                "organization.json",
                json.dumps(
                    {
                        "org": {
                            "org_id": org_id,
                            "name": org["name"],
                            "slug": org["slug"],
                            "region": org["region"],
                            "created_at": org["created_at"],
                        },
                        "policy": org["policy"],
                        "members": members,
                        "coach_assignments": assignments,
                        "invoices": invoices,
                        "departed_candidates": departed,
                        "exported_at": self.now,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
            )
            archive.writestr(
                "audit.jsonl",
                "\n".join(
                    json.dumps(row, ensure_ascii=False, default=str)
                    for row in self.app.audit.export_lines(org_id)
                ),
            )
            for candidate in candidates:
                try:
                    blob = self.app.workspaces.export_zip(
                        org_id, candidate["candidate_id"], principal
                    )
                except Exception:  # noqa: BLE001 - one broken workspace must not fail the whole export
                    logger.warning(
                        "Could not export candidate %s",
                        candidate["candidate_id"],
                        exc_info=True,
                    )
                    continue
                archive.writestr(f"candidates/{candidate['candidate_id']}.zip", blob)
        return buffer.getvalue()

    def export_by_token(self, token: str) -> tuple[dict[str, Any], bytes]:
        """The one-time-link download a departed candidate uses; no session is involved."""
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM exports WHERE token_hash = ? AND kind = 'candidate_departure'",
                (crypto.hash_token(token or ""),),
            )
        if row is None:
            raise NotFound("This download link is not valid.")
        if row["expires_at"] <= self.now:
            raise Gone("This download link has expired.")
        return row, self._read_export(row)

    def export_for(
        self, actor: Principal, export_id: str
    ) -> tuple[dict[str, Any], bytes]:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM exports WHERE export_id = ? AND org_id = ?",
                (export_id, actor.org_id),
            )
        if row is None:
            raise NotFound("Export not found.")
        if row["candidate_id"] and row["candidate_id"] == actor.member_id:
            self.app.authz.require(actor, "export.own", candidate_id=actor.member_id)
        else:
            self.app.authz.require(actor, "export.org")
            if _holds_unmasked_data(row):
                # the candidate's own complete data: staff, and operators acting for staff, never download it
                raise NotFound("Export not found.")
        if row["expires_at"] <= self.now:
            raise Gone("This export has expired. Create a new one.")
        return row, self._read_export(row)

    def _read_export(self, row: dict[str, Any]) -> bytes:
        try:
            data = Path(row["path"]).read_bytes()
        except OSError:
            raise Gone("The export file is no longer available.") from None
        with self.db.write() as connection:
            connection.execute(
                "UPDATE exports SET downloaded_at = ? WHERE export_id = ?",
                (self.now, row["export_id"]),
            )
        return data

    def export_organization(self, actor: Principal) -> dict[str, Any]:
        self.app.authz.require(actor, "export.org")
        self.app.entitlements.require_state(actor.org_id)
        result = self.create_export(
            actor.org_id,
            kind="organization",
            created_by=actor.actor_id,
            principal=actor,
        )
        self.app.audit.record(
            actor.org_id,
            "data.export_org",
            actor=actor,
            target_type="export",
            target_id=result["export_id"],
            detail={"size": result["size"]},
        )
        return {k: v for k, v in result.items() if k != "token"}

    def export_candidate(self, actor: Principal, candidate_id: str) -> dict[str, Any]:
        """A candidate exports their own data; owners, admins and auditors export any candidate."""
        if actor.role == "candidate":
            self.app.authz.require(actor, "export.own", candidate_id=candidate_id)
        else:
            self.app.authz.candidate(actor.org_id, candidate_id)
            self.app.authz.require(actor, "export.org")
        self.app.entitlements.require_state(actor.org_id)
        result = self.create_export(
            actor.org_id,
            candidate_id=candidate_id,
            kind="candidate",
            created_by=actor.actor_id,
            principal=actor,
        )
        self.app.audit.record(
            actor.org_id,
            "data.export_candidate",
            actor=actor,
            candidate_id=candidate_id,
            target_type="export",
            target_id=result["export_id"],
            detail={"size": result["size"]},
        )
        return {k: v for k, v in result.items() if k != "token"}

    def list_exports(self, actor: Principal) -> list[dict[str, Any]]:
        clauses, params = ["org_id = ?", "expires_at > ?"], [actor.org_id, self.now]
        if actor.role == "candidate":
            clauses.append("candidate_id = ?")
            params.append(actor.member_id)
        else:
            self.app.authz.require(actor, "export.org")
        with self.db.read() as connection:
            rows = many(
                connection,
                f"SELECT export_id, candidate_id, kind, size, created_at, expires_at, downloaded_at, created_by FROM exports WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                params,
            )
        if actor.role != "candidate":
            rows = [r for r in rows if not _holds_unmasked_data(r)]
        for row in rows:
            row.pop("created_by", None)
        return rows

    # --------------------------------------------------------- candidate workspace deletion

    def delete_candidate_workspace(
        self, actor: Principal, candidate_id: str
    ) -> dict[str, Any]:
        """Remove one candidate's workspace (rows, documents, browser profile) and end their membership."""
        self.app.authz.require(actor, "candidates.manage")
        self.app.authz.candidate(actor.org_id, candidate_id)
        self.app.entitlements.require_state(actor.org_id, write=True)
        if self.on_legal_hold(actor.org_id):
            raise Conflict(
                "This organization is under legal hold; candidate data cannot be deleted.",
                code="legal_hold",
            )
        self.app.workspaces.stop_automation(actor.org_id, candidate_id)
        self.app.workspaces.delete(actor.org_id, candidate_id)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET status = 'removed', removed_at = ?, password_hash = '' WHERE member_id = ? AND org_id = ?",
                (self.now, candidate_id, actor.org_id),
            )
            connection.execute(
                "DELETE FROM coach_assignments WHERE org_id = ? AND candidate_id = ?",
                (actor.org_id, candidate_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND member_id = ? AND revoked_at IS NULL",
                (self.now, actor.org_id, candidate_id),
            )
            self.app.audit.record(
                actor.org_id,
                "data.delete_candidate",
                actor=actor,
                candidate_id=candidate_id,
                connection=connection,
            )
        self.recompute_over_limits(actor.org_id)
        return {"deleted": True, "candidate_id": candidate_id}

    # ------------------------------------------------------------------ closure and deletion

    def request_closure(self, actor: Principal, password: str) -> dict[str, Any]:
        self.app.authz.require(actor, "org.close")
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT password_hash FROM members WHERE member_id = ? AND org_id = ?",
                (actor.member_id, actor.org_id),
            )
        if row is None or not crypto.verify_password(
            password or "", row["password_hash"]
        ):
            # Not a 401: the web app treats that as "your session ended".
            raise ValidationFailed(
                "That password is not correct. Confirm closure with your own password.",
                code="password_incorrect",
            )
        org = self.app.tenancy.org(actor.org_id)
        if org["closure_requested_at"]:
            raise Conflict("Closure was already requested.")
        subscription = self.app.billing.subscription(actor.org_id)
        if (
            subscription["billing_subscription_id"]
            and subscription["status"] == "active"
        ):
            try:
                self.app.billing.provider.cancel_subscription(
                    subscription["billing_subscription_id"], at_period_end=False
                )
            except Exception:  # noqa: BLE001 - the organization is closing regardless
                logger.exception(
                    "Could not cancel the provider subscription during closure"
                )
        export = self.create_export(
            actor.org_id,
            kind="closure",
            created_by=actor.actor_id,
            principal=actor,
            ttl_days=self.app.config.deletion_grace_days,
        )
        window = self.now + self.app.config.deletion_grace_days * DAY
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET closure_requested_at = ?, closure_requested_by = ? WHERE org_id = ?",
                (self.now, actor.actor_id, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "org.closure_requested",
                actor=actor,
                detail={"deletion_at": window},
                connection=connection,
            )
        with (
            self.db.write() as connection
        ):  # remembered so cancelling the closure can put a trial back
            connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"closure_prev:{actor.org_id}", subscription["status"]),
            )
        if subscription["status"] != "cancelled":
            self.app.billing.transition(
                actor.org_id,
                "cancelled",
                reason="organization closure requested",
                trigger_type="person",
                trigger_id=actor.actor_id,
                fields={
                    "cancelled_at": self.now,
                    "export_until": window,
                    "cancel_at_period_end": 0,
                },
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET deletion_scheduled_at = ?, export_until = ? WHERE org_id = ?",
                (window, window, actor.org_id),
            )
        self.app.entitlements.invalidate(actor.org_id)
        return {
            "deletion_at": window,
            "export": {k: v for k, v in export.items() if k != "token"},
        }

    def cancel_closure(self, actor: Principal) -> dict[str, Any]:
        self.app.authz.require(actor, "org.close")
        org = self.app.tenancy.org(actor.org_id)
        if not org["closure_requested_at"]:
            raise Conflict("Closure was not requested.")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET closure_requested_at = NULL, closure_requested_by = '' WHERE org_id = ?",
                (actor.org_id,),
            )
            connection.execute(
                "UPDATE subscriptions SET deletion_scheduled_at = NULL, updated_at = ? WHERE org_id = ?",
                (self.now, actor.org_id),
            )
            previous = one(
                connection,
                "SELECT value FROM cloud_kv WHERE key = ?",
                (f"closure_prev:{actor.org_id}",),
            )
            self.app.audit.record(
                actor.org_id,
                "org.closure_cancelled",
                actor=actor,
                connection=connection,
            )
        subscription = self.app.billing.subscription(actor.org_id)
        restored = ""
        if (
            previous
            and previous["value"] == "trialing"
            and subscription["status"] == "cancelled"
            and (subscription["trial_end"] or 0) > self.now
        ):
            # A trial that was closed early gets its remaining days back; a paid subscription was ended at the
            # provider, so the owner chooses a plan again.
            self.app.billing.transition(
                actor.org_id,
                "trialing",
                reason="closure cancelled",
                trigger_type="person",
                trigger_id=actor.actor_id,
                fields={"export_until": None, "cancelled_at": None},
            )
            restored = "trialing"
        self.app.entitlements.invalidate(actor.org_id)
        return {
            "closure_cancelled": True,
            "restored": restored,
            "needs_plan": subscription["status"] == "cancelled" and not restored,
        }

    def delete_org(
        self,
        org_id: str,
        *,
        reason: str,
        trigger_type: str,
        operator: Principal | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete everything an organization owns; keep the billing record and an audit stub."""
        org = self.app.tenancy.org(org_id)
        if self.on_legal_hold(org_id) and not force:
            raise Conflict(
                "This organization is under legal hold; its data cannot be deleted until the hold is lifted.",
                code="legal_hold",
            )
        if self.app.billing.subscription(org_id)["status"] != "deleted":
            # First, so every request and worker stops at once and the transition lands in the log the stub counts.
            self.app.billing.transition(
                org_id,
                "deleted",
                reason=reason,
                trigger_type=trigger_type,
                trigger_id=operator.operator_id if operator else "",
                fields={"deletion_scheduled_at": None, "overrides": "{}"},
            )
        with self.db.read() as connection:
            workspaces = many(
                connection,
                "SELECT candidate_id FROM workspaces WHERE org_id = ?",
                (org_id,),
            )
        for workspace in workspaces:
            try:
                self.app.workspaces.stop_automation(org_id, workspace["candidate_id"])
                self.app.workspaces.delete(org_id, workspace["candidate_id"])
            except Exception:  # noqa: BLE001 - deletion presses on
                logger.exception(
                    "Could not delete workspace %s", workspace["candidate_id"]
                )
        self.app.workspaces.delete_org_tree(org_id)
        count, last_hash = self.app.audit.delete_org(org_id)
        self.app.secrets.delete_org(org_id)
        name_hash = crypto.hash_token(org["name"].lower())
        retention = self.now + AUDIT_STUB_RETENTION_DAYS * DAY
        with self.db.write() as connection:
            defer_foreign_keys(connection)
            connection.execute(
                "DELETE FROM airaapi_key_index WHERE user_id = ?", (org_id,)
            )
            for table in self._tenant_tables(connection):
                connection.execute(f"DELETE FROM {table} WHERE org_id = ?", (org_id,))
            connection.execute(
                "INSERT OR REPLACE INTO audit_stubs (org_id, org_name_hash, created_at, deleted_at, retention_until, event_count, last_hash, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    org_id,
                    name_hash,
                    org["created_at"],
                    self.now,
                    retention,
                    count,
                    last_hash,
                    reason[:200],
                ),
            )
            connection.execute(
                "UPDATE organizations SET name = '[deleted]', slug = ?, policy = '{}', branding = '{}', status = 'deleted', deleted_at = ?, paused_at = NULL WHERE org_id = ?",
                (f"deleted-{org_id[:12]}", self.now, org_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND revoked_at IS NULL",
                (self.now, org_id),
            )
            self.app.platform_audit.record(
                "org.delete",
                operator=operator,
                operator_label="" if operator else f"{trigger_type}",
                org_id=org_id,
                detail={"reason": reason, "audit_events": count},
                source=trigger_type,
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        return {"deleted": True, "org_id": org_id, "audit_events": count}

    def _tenant_tables(self, connection: Any) -> list[str]:
        tables = []
        for name in table_names(connection):
            if name in RETAINED_TABLES:
                continue
            if "org_id" in table_columns(connection, name):
                tables.append(name)
        return tables

    # ------------------------------------------------------------------ key rotation

    def rotate_org_keys(
        self, org_id: str, *, operator: Principal | None = None
    ) -> dict[str, int]:
        """Rotate an organization's data key without downtime.

        A new key is added and the organization's secrets move to it at once; every stored document and the
        organization's Aira API secrets (its SerpApi key) are then re-encrypted under it, and only after all of
        that succeeds are the older keys dropped. Until then ``secrets.fernet`` still reads data under any key.
        """
        self.app.tenancy.org(org_id)
        secrets_moved = len(self._org_secret_names(org_id))
        self.app.secrets.rotate(org_id)
        fernet = self.app.secrets.fernet(org_id)
        api_secrets = self._reencrypt_api_secrets(org_id, fernet)
        documents = self._reencrypt_documents(org_id, fernet)
        removed = self.app.secrets.retire_old_keys(org_id)
        detail = {
            "documents": documents,
            "api_secrets": api_secrets,
            "secrets": secrets_moved,
            "keys_retired": removed,
        }
        if operator is not None:
            self.app.platform_audit.record(
                "tenant.rotate_keys", operator=operator, org_id=org_id, detail=detail
            )
        self.app.audit.record(
            org_id,
            "security.keys_rotated",
            actor=operator,
            actor_type="system" if operator is None else "",
            source="scheduler" if operator is None else "operator",
            detail=detail,
        )
        return detail

    def _org_secret_names(self, org_id: str) -> list[str]:
        with self.db.read() as connection:
            return [
                row["name"]
                for row in many(
                    connection,
                    "SELECT name FROM org_secrets WHERE org_id = ?",
                    (org_id,),
                )
            ]

    def _reencrypt_api_secrets(self, org_id: str, fernet: Any) -> int:
        import sqlite3

        path = self.app.workspaces.org_api_path(org_id)
        if not path.exists():
            return 0
        connection = sqlite3.connect(path, timeout=15)
        try:
            rows = connection.execute(
                "SELECT name, ciphertext FROM airaapi_secrets"
            ).fetchall()
            for name, ciphertext in rows:
                connection.execute(
                    "UPDATE airaapi_secrets SET ciphertext = ? WHERE name = ?",
                    (fernet.rotate(bytes(ciphertext)), name),
                )
            connection.commit()
            return len(rows)
        except sqlite3.OperationalError:
            return 0
        finally:
            connection.close()

    def _reencrypt_documents(self, org_id: str, fernet: Any) -> int:
        import sqlite3

        backend = self.app.storage.backend(org_id)
        with self.db.read() as connection:
            workspaces = many(
                connection,
                "SELECT candidate_id, data_path FROM workspaces WHERE org_id = ?",
                (org_id,),
            )
        count = 0
        for workspace in workspaces:
            path = Path(workspace["data_path"])
            if not path.exists():
                continue
            database = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10
            )
            try:
                rows = database.execute(
                    "SELECT document_id, owner_id FROM documents WHERE deleted_at IS NULL"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            finally:
                database.close()
            for document_id, owner_id in rows:
                try:
                    token = backend.get(owner_id, document_id)
                except FileNotFoundError:
                    continue
                backend.put(owner_id, document_id, fernet.rotate(token))
                count += 1
        return count

    # ------------------------------------------------------------------ legal hold

    def on_legal_hold(self, org_id: str) -> bool:
        try:
            return bool(self.app.tenancy.policy(org_id).get("legal_hold"))
        except Exception:  # noqa: BLE001 - an unreadable policy is not a hold
            return False

    # ------------------------------------------------------------------ data subject requests

    def data_subject_request(
        self, operator: Principal, kind: str, email: str
    ) -> dict[str, Any]:
        """Handle an access, export or deletion request for one person across every tenant."""
        self.app.authz.require(operator, "platform.abuse.manage")
        if kind not in {"access", "export", "delete"}:
            raise ValidationFailed("Kind must be access, export or delete.")
        email = crypto.normalize_email(email)
        with self.db.read() as connection:
            memberships = many(
                connection,
                """SELECT m.member_id, m.org_id, m.role, o.name AS org_name FROM members m JOIN organizations o ON o.org_id = m.org_id
                WHERE m.email = ? AND m.removed_at IS NULL""",
                (email,),
            )
        result: dict[str, Any] = {
            "email": email,
            "memberships": [
                {"org_id": m["org_id"], "org": m["org_name"], "role": m["role"]}
                for m in memberships
            ],
        }
        if kind == "export":
            exports = []
            for membership in memberships:
                if membership["role"] == "candidate":
                    exports.append(
                        self.create_export(
                            membership["org_id"],
                            candidate_id=membership["member_id"],
                            kind="candidate",
                            created_by=f"dsr:{operator.operator_id}",
                            recipient=email,
                            ttl_days=7,
                        )["export_id"]
                    )
            result["exports"] = exports
        elif kind == "delete":
            deleted = []
            for membership in memberships:
                if (
                    membership["role"] == "owner"
                    and self.app.tenancy._owner_count(membership["org_id"]) <= 1
                ):
                    deleted.append(
                        {
                            "org_id": membership["org_id"],
                            "skipped": "last owner; close the organization instead",
                        }
                    )
                    continue
                if self.on_legal_hold(membership["org_id"]):
                    deleted.append(
                        {"org_id": membership["org_id"], "skipped": "legal hold"}
                    )
                    continue
                actor = Principal(
                    kind="member",
                    org_id=membership["org_id"],
                    role="owner",
                    member_id="dsr",
                    email=f"dsr:{operator.email}",
                )
                self._delete_member_data(actor, membership)
                deleted.append({"org_id": membership["org_id"], "deleted": True})
            result["deleted"] = deleted
        request_id = new_id()
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO data_subject_requests (request_id, kind, subject_email, status, created_by, created_at, completed_at, result) VALUES (?, ?, ?, 'done', ?, ?, ?, ?)",
                (
                    request_id,
                    kind,
                    crypto.hash_token(email),
                    operator.operator_id,
                    self.now,
                    self.now,
                    dumps({"orgs": len(memberships)}),
                ),
            )
        self.app.platform_audit.record(
            "dsr." + kind, operator=operator, detail={"organizations": len(memberships)}
        )
        result["request_id"] = request_id
        return result

    def _delete_member_data(self, actor: Principal, membership: dict[str, Any]) -> None:
        org_id, member_id = membership["org_id"], membership["member_id"]
        if membership["role"] == "candidate":
            self.app.workspaces.stop_automation(org_id, member_id)
            self.app.workspaces.delete(org_id, member_id)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET status = 'removed', removed_at = ?, password_hash = '', email = ?, name = '' WHERE member_id = ? AND org_id = ?",
                (
                    self.now,
                    f"deleted-{member_id[:12]}@deleted.invalid",
                    member_id,
                    org_id,
                ),
            )
            connection.execute(
                "DELETE FROM coach_assignments WHERE org_id = ? AND (coach_id = ? OR candidate_id = ?)",
                (org_id, member_id, member_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND member_id = ? AND revoked_at IS NULL",
                (self.now, org_id, member_id),
            )
            self.app.audit.record(
                org_id,
                "member.erased",
                actor_type="operator",
                actor_label=actor.email,
                target_type="member",
                target_id=member_id,
                source="dsr",
                connection=connection,
            )

    # ------------------------------------------------------------------ the scheduler tick

    def tick(self) -> dict[str, Any]:
        """One pass of everything that depends on time. Safe to run from several replicas (it takes a lease)."""
        if not self._lease("tick", 20):
            return {"skipped": True}
        summary: dict[str, Any] = {}
        summary["billing"] = self.app.billing.process_due()
        summary["plan_migrations"] = self.app.catalog.apply_due_migrations()
        summary["support"] = self.app.platform.expire_support_access()
        summary["housekeeping"] = self._housekeeping()
        summary["queue"] = self.app.scheduler.enqueue_due()
        return summary

    def _lease(self, name: str, seconds: int) -> bool:
        key = f"lease:{name}"
        with self.db.write() as connection:
            row = one(connection, "SELECT value FROM cloud_kv WHERE key = ?", (key,))
            if row and int(row["value"]) > self.now:
                return False
            connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(self.now + seconds)),
            )
        return True

    def _housekeeping(self) -> dict[str, int]:
        done = {
            "sessions": 0,
            "audit_pruned": 0,
            "documents_purged": 0,
            "exports_removed": 0,
        }
        with self.db.write() as connection:
            done["sessions"] = connection.execute(
                "DELETE FROM sessions WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)",
                (self.now - 7 * DAY, self.now - 7 * DAY),
            ).rowcount
            connection.execute(
                "DELETE FROM email_tokens WHERE expires_at < ?", (self.now - 7 * DAY,)
            )
            connection.execute(
                "DELETE FROM sso_states WHERE expires_at < ?", (self.now,)
            )
            connection.execute(
                "DELETE FROM run_outcomes WHERE at < ?", (self.now - 14 * DAY,)
            )
            connection.execute(
                "DELETE FROM remote_login_sessions WHERE expires_at < ? AND (state != 'active' OR used_at < ?)",
                (self.now, self.now - 3600),  # a sign-in in progress outlives its link
            )
            expired = many(
                connection,
                "SELECT export_id, path FROM exports WHERE expires_at < ?",
                (self.now,),
            )
            for export in expired:
                try:
                    Path(export["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
                connection.execute(
                    "DELETE FROM exports WHERE export_id = ?", (export["export_id"],)
                )
            done["exports_removed"] = len(expired)
            orgs = many(
                connection,
                "SELECT o.org_id FROM organizations o WHERE o.deleted_at IS NULL",
            )
        for org in orgs:
            org_id = org["org_id"]
            try:
                if self.on_legal_hold(org_id):
                    continue
                audit_days = self.app.entitlements.resolve(org_id).limit("audit_days")
                if audit_days:
                    done["audit_pruned"] += self.app.audit.prune(
                        org_id, self.now - int(audit_days) * DAY
                    )
                done["documents_purged"] += self.app.workspaces.purge_expired_documents(
                    org_id
                )
            except Exception:  # noqa: BLE001
                logger.exception("Housekeeping failed for %s", org_id)
        return done


__all__ = ["Lifecycle", "RETAINED_TABLES", "Forbidden", "time"]
