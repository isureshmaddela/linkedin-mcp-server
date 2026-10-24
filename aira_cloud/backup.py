"""Per-tenant backup and restore.

A backup is one archive holding an organization's control-plane rows and its files (workspaces, encrypted
documents, exports) with a SHA-256 for every file. Restoring it replaces *that organization only*: every
other tenant's rows and files are left exactly as they are, which is what the restore drill must show.

What a backup contains and does not:

* it holds the organization's wrapped data keys and encrypted documents; the **master key is not included**,
  so a backup is useless without it (keep the two apart);
* sessions, queued jobs, outbound mail and metrics are omitted (everyone signs in again after a restore);
* with S3 storage the document objects stay in the bucket (use bucket versioning or replication); the archive
  then carries the control-plane rows and workspace databases only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira_cloud.audit import _set_maintenance
from aira_cloud.db import (
    defer_foreign_keys,
    many,
    one,
    sync_sequences,
    table_columns,
    table_names,
)
from aira_cloud.errors import Conflict, NotFound, ValidationFailed
from aira_cloud.rbac import Principal
from aira_cloud.schema import MIGRATIONS

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

# Rows that are ephemeral or can be rebuilt are not backed up.
SKIP_TABLES = frozenset(
    {
        "sessions",
        "queue_jobs",
        "mail_outbox",
        "run_outcomes",
        "email_tokens",
        "sso_states",
        "remote_login_sessions",
        "worker_heartbeats",
        "usage_daily",
        "org_notices",
    }
)
# Tables that hold an organization's rows even though they are retained after deletion.
ALWAYS_INCLUDE = (
    "organizations",
    "subscriptions",
    "invoices",
    "billing_events",
    "subscription_events",
    "billing_credits",
    "audit_stubs",
)
FORMAT_VERSION = 1


def _encode(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__b64__"}:
        return base64.b64decode(value["__b64__"])
    return value


class BackupService:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    # ------------------------------------------------------------------ what belongs to an organization

    def _tables(self, connection: sqlite3.Connection) -> list[tuple[str, str]]:
        """``(table, column)`` pairs whose rows belong to an organization."""
        found: list[tuple[str, str]] = []
        for name in table_names(connection):
            if name in SKIP_TABLES or name in {
                "cloud_kv",
                "cloud_migrations",
                "plans",
                "plan_versions",
                "plan_migrations",
                "feature_flags",
                "announcements",
                "operators",
                "blocked_sources",
                "platform_audit_logs",
                "data_subject_requests",
                "support_access",
            }:
                continue
            columns = table_columns(connection, name)
            if "org_id" in columns:
                found.append((name, "org_id"))
            elif name == "airaapi_key_index":
                found.append((name, "user_id"))
        return found

    # ------------------------------------------------------------------ backup

    def backup_org(self, org_id: str, *, operator: Principal | None = None) -> bytes:
        tables: dict[str, list[dict[str, Any]]] = {}
        with self.db.read() as connection:
            if (
                one(
                    connection,
                    "SELECT 1 AS x FROM organizations WHERE org_id = ?",
                    (org_id,),
                )
                is None
            ):
                raise NotFound("Organization not found.")
            for name, column in self._tables(connection):
                rows = many(
                    connection, f"SELECT * FROM {name} WHERE {column} = ?", (org_id,)
                )
                if rows:
                    tables[name] = [
                        {key: _encode(value) for key, value in row.items()}
                        for row in rows
                    ]
            anchor = one(
                connection,
                "SELECT value FROM cloud_kv WHERE key = ?",
                (f"audit_anchor:{org_id}",),
            )
            support = many(
                connection, "SELECT * FROM support_access WHERE org_id = ?", (org_id,)
            )
        if support:
            tables["support_access"] = [
                {key: _encode(value) for key, value in row.items()} for row in support
            ]
        buffer = io.BytesIO()
        checksums: dict[str, str] = {}
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            control = json.dumps(
                {"tables": tables, "audit_anchor": anchor["value"] if anchor else ""},
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
            archive.writestr("control.json", control)
            checksums["control.json"] = hashlib.sha256(control).hexdigest()
            root = self.app.workspaces.org_dir(org_id)
            if root.exists():
                for path in sorted(root.rglob("*")):
                    if (
                        path.is_file()
                        and "_exports" not in path.parts
                        and not path.name.endswith(("-wal", "-shm"))
                    ):
                        relative = f"files/{path.relative_to(root).as_posix()}"
                        data = self._read_consistent(path)
                        archive.writestr(relative, data)
                        checksums[relative] = hashlib.sha256(data).hexdigest()
            manifest = {
                "format": FORMAT_VERSION,
                "org_id": org_id,
                "created_at": self.app.clock.now(),
                "schema_version": MIGRATIONS[-1][0],
                "storage": self.app.config.storage_backend,
                "tables": {name: len(rows) for name, rows in tables.items()},
                "checksums": checksums,
            }
            manifest["signature"] = self._sign(manifest)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        if operator is not None:
            self.app.platform_audit.record(
                "tenant.backup",
                operator=operator,
                org_id=org_id,
                detail={"files": len(checksums) - 1, "size": len(buffer.getvalue())},
            )
        return buffer.getvalue()

    @staticmethod
    def _read_consistent(path: Path) -> bytes:
        """A SQLite database is copied through the backup API (a consistent snapshot even while it is in use)."""
        if path.suffix == ".sqlite3":
            source = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15
            )
            try:
                target = sqlite3.connect(":memory:")
                source.backup(target)
                return bytes(target.serialize())
            finally:
                source.close()
        return path.read_bytes()

    # ------------------------------------------------------------------ verify and restore

    MAC_SCOPE = "_platform"
    MAC_NAME = "backup_mac_key"

    def _mac_key(self, *, create: bool) -> bytes | None:
        """The deployment's backup-signing key, kept encrypted in the control plane (created with the first backup)."""
        value = self.app.secrets.get(self.MAC_SCOPE, self.MAC_NAME)
        if value is None and create:
            value = secrets.token_urlsafe(48)
            self.app.secrets.put(self.MAC_SCOPE, self.MAC_NAME, value)
        return value.encode() if value else None

    @staticmethod
    def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
        return json.dumps(
            {k: v for k, v in manifest.items() if k not in {"signature", "signed"}},
            sort_keys=True,
        ).encode()

    def _sign(self, manifest: dict[str, Any]) -> str:
        key = self._mac_key(create=True)
        assert key is not None
        return hmac.new(key, self._manifest_bytes(manifest), hashlib.sha256).hexdigest()

    def _signature_ok(self, manifest: dict[str, Any]) -> bool:
        key = self._mac_key(create=False)
        signature = str(manifest.get("signature") or "")
        if key is None or not signature:
            return False
        expected = hmac.new(
            key, self._manifest_bytes(manifest), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _check_contents(
        self, org_id: str, manifest: dict[str, Any], control: dict[str, Any]
    ) -> None:
        """Refuse an archive that names tables, rows or files outside the organization before anything changes."""
        with self.db.read() as connection:
            allowed = dict(self._tables(connection))
        for name, rows in control.get("tables", {}).items():
            column = allowed.get(name)
            if column is None:
                raise ValidationFailed(
                    f"The backup holds rows for a table it may not restore ({name}).",
                    code="bad_backup",
                )
            if column == "org_id":
                for row in rows:
                    if row.get("org_id") != org_id:
                        raise ValidationFailed(
                            f"The backup holds a {name} row that belongs to another organization.",
                            code="bad_backup",
                        )
        for name in manifest["checksums"]:
            if name.startswith("files/"):
                parts = name.removeprefix("files/").replace("\\", "/").split("/")
                if (
                    not parts
                    or any(part in {"", ".", ".."} for part in parts)
                    or re.match(r"^[A-Za-z]:", parts[0])
                ):
                    raise ValidationFailed(
                        f"The backup holds an unsafe file path ({name}).",
                        code="bad_backup",
                    )

    def verify(self, data: bytes, *, allow_unsigned: bool = False) -> dict[str, Any]:
        """Check an archive's manifest and every checksum; raise if it was altered or is incomplete."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
            manifest = json.loads(archive.read("manifest.json"))
        except (zipfile.BadZipFile, KeyError, ValueError):
            raise ValidationFailed(
                "This is not a valid Aira backup.", code="bad_backup"
            ) from None
        if manifest.get("format") != FORMAT_VERSION:
            raise ValidationFailed(
                "This backup was made by an incompatible version.", code="bad_backup"
            )
        if not re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("org_id", ""))):
            raise ValidationFailed(
                "The backup names an invalid organization.", code="bad_backup"
            )
        manifest["signed"] = self._signature_ok(manifest)
        if not manifest["signed"] and not allow_unsigned:
            raise ValidationFailed(
                "This backup was not signed by this deployment (or its signature does not match). "
                "Restore it only if you made it elsewhere and trust it, with allow-unsigned.",
                code="bad_backup",
            )
        if int(manifest.get("schema_version", 0)) > MIGRATIONS[-1][0]:
            raise ValidationFailed(
                "This backup is newer than the running software; upgrade first.",
                code="bad_backup",
            )
        for name, expected in manifest["checksums"].items():
            try:
                actual = hashlib.sha256(archive.read(name)).hexdigest()
            except KeyError:
                raise ValidationFailed(
                    f"The backup is missing {name}.", code="bad_backup"
                ) from None
            if actual != expected:
                raise ValidationFailed(
                    f"The backup was altered: {name} does not match its checksum.",
                    code="bad_backup",
                )
        return manifest

    def restore_org(
        self,
        data: bytes,
        *,
        replace: bool = False,
        operator: Principal | None = None,
        allow_unsigned: bool = False,
    ) -> dict[str, Any]:
        """Restore one organization from a backup, touching nothing that belongs to another."""
        manifest = self.verify(data, allow_unsigned=allow_unsigned)
        org_id = manifest["org_id"]
        archive = zipfile.ZipFile(io.BytesIO(data))
        control = json.loads(archive.read("control.json"))
        self._check_contents(org_id, manifest, control)
        with self.db.read() as connection:
            existing = one(
                connection,
                "SELECT status, deleted_at FROM organizations WHERE org_id = ?",
                (org_id,),
            )
        if existing is not None and not replace:
            raise Conflict(
                "This organization exists. Restore with replace to overwrite it (only this organization is touched).",
                code="exists",
            )
        # files first into a staging directory, then swapped in, so a failure leaves the current tree alone
        staging = self.app.workspaces.org_dir(org_id).with_name(f"{org_id}.restore")
        shutil.rmtree(staging, ignore_errors=True)
        written = 0
        for name in manifest["checksums"]:
            if name.startswith("files/"):
                target = staging / name.removeprefix("files/")
                if not target.resolve().is_relative_to(staging.resolve()):
                    raise ValidationFailed(
                        "The backup holds an unsafe file path.", code="bad_backup"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                written += 1
        with self.db.write() as connection:
            defer_foreign_keys(connection)
            _set_maintenance(
                connection, True
            )  # the audit log refuses deletes except in maintenance such as this
            try:
                for name, column in self._tables(connection):
                    connection.execute(
                        f"DELETE FROM {name} WHERE {column} = ?", (org_id,)
                    )
            finally:
                _set_maintenance(connection, False)
            connection.execute("DELETE FROM support_access WHERE org_id = ?", (org_id,))
            for name, rows in control["tables"].items():
                columns = table_columns(connection, name)
                for row in rows:
                    values = {
                        key: _decode(value)
                        for key, value in row.items()
                        if key in columns
                    }
                    placeholders = ", ".join("?" for _ in values)
                    connection.execute(
                        f"INSERT INTO {name} ({', '.join(values)}) VALUES ({placeholders})",
                        tuple(values.values()),
                    )
                sync_sequences(connection, name)
            if control.get("audit_anchor"):
                connection.execute(
                    "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (f"audit_anchor:{org_id}", control["audit_anchor"]),
                )
            self.app.platform_audit.record(
                "tenant.restore",
                operator=operator,
                operator_label="" if operator else "cli",
                org_id=org_id,
                detail={
                    "backup_created_at": manifest["created_at"],
                    "files": written,
                    "replaced": existing is not None,
                },
                connection=connection,
            )
        target_root = self.app.workspaces.org_dir(org_id)
        shutil.rmtree(target_root, ignore_errors=True)
        if staging.exists():
            staging.rename(target_root)
        else:
            target_root.mkdir(parents=True, exist_ok=True)
        # workspace paths are absolute: point them at this installation's tree
        with self.db.write() as connection:
            for row in many(
                connection,
                "SELECT candidate_id FROM workspaces WHERE org_id = ?",
                (org_id,),
            ):
                connection.execute(
                    "UPDATE workspaces SET data_path = ?, linkedin_port = NULL, login_pid = NULL, worker_pid = NULL WHERE candidate_id = ?",
                    (
                        str(self.app.workspaces.data_path(org_id, row["candidate_id"])),
                        row["candidate_id"],
                    ),
                )
        self.app.entitlements.invalidate(org_id)
        self.app.tenancy.invalidate_policy(org_id)
        self.app.secrets.forget(org_id)
        return {
            "org_id": org_id,
            "tables": manifest["tables"],
            "files": written,
            "audit": self.app.audit.verify(org_id),
        }
