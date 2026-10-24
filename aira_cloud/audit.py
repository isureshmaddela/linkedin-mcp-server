"""Append-only, hash-chained audit logs: one per organization, one for the platform.

Each row carries the hash of the row before it in the same chain, so a deleted
or edited row is detectable (:meth:`AuditLog.verify`). Database triggers refuse
UPDATE and DELETE except while a maintenance flag is set by retention pruning
or organization deletion, which run inside one transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import builtins
from collections.abc import Mapping
from typing import Any

from aira_cloud.config import Clock
from aira_cloud.db import (
    ControlDB,
    dumps,
    loads,
    lock,
    many,
    new_id,
    one,
    platform_scope,
)
from aira_cloud.rbac import Principal

SECRET_KEY_RE = re.compile(
    r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|ciphertext",
    re.IGNORECASE,
)


def scrub(value: Any, depth: int = 0) -> Any:
    """Drop anything that looks like a credential before it reaches a log."""
    if depth > 6:
        return "…"
    if isinstance(value, Mapping):
        return {
            str(k)[:60]: (
                "[redacted]" if SECRET_KEY_RE.search(str(k)) else scrub(v, depth + 1)
            )
            for k, v in list(value.items())[:60]
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in list(value)[:60]]
    if isinstance(value, str):
        return value[:500]
    return value


def _hash(prev: str, fields: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()


def _set_maintenance(connection: sqlite3.Connection, on: bool) -> None:
    connection.execute(
        "INSERT INTO cloud_kv (key, value) VALUES ('audit_maintenance', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if on else "0",),
    )


class AuditLog:
    """The audit log of one organization (rows are filtered by ``org_id`` on every call)."""

    def __init__(self, db: ControlDB, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def record(
        self,
        org_id: str,
        action: str,
        *,
        actor: Principal | None = None,
        actor_type: str = "",
        actor_id: str = "",
        actor_label: str = "",
        actor_role: str = "",
        candidate_id: str = "",
        target_type: str = "",
        target_id: str = "",
        detail: Mapping[str, Any] | None = None,
        source: str = "user",
        correlation_id: str = "",
        connection: sqlite3.Connection | None = None,
    ) -> str:
        if actor is not None:
            actor_type = actor_type or ("operator" if actor.is_operator else "member")
            actor_id = actor_id or actor.actor_id
            actor_label = actor_label or actor.label
            actor_role = actor_role or (
                f"support:{actor.role}"
                if actor.via_support
                else actor.role or actor.operator_role
            )
        audit_id = new_id()
        fields = {
            "audit_id": audit_id,
            "org_id": org_id,
            "at": self.clock.now(),
            "actor_type": actor_type or "system",
            "actor_id": actor_id,
            "actor_label": actor_label[:200],
            "actor_role": actor_role[:40],
            "candidate_id": candidate_id,
            "action": action[:80],
            "target_type": target_type[:40],
            "target_id": str(target_id)[:256],
            "detail": dumps(scrub(dict(detail or {})))[:4000],
            "source": source[:40],
            "correlation_id": correlation_id[:64],
        }

        def insert(db_connection: sqlite3.Connection) -> None:
            lock(
                db_connection, f"audit:{org_id}"
            )  # the chain is read, extended and written as one step
            previous = one(
                db_connection,
                "SELECT hash FROM audit_logs WHERE org_id = ? ORDER BY seq DESC LIMIT 1",
                (org_id,),
            )
            if previous is None:
                anchor = one(
                    db_connection,
                    "SELECT value FROM cloud_kv WHERE key = ?",
                    (f"audit_anchor:{org_id}",),
                )
                prev_hash = anchor["value"] if anchor else ""
            else:
                prev_hash = previous["hash"]
            digest = _hash(prev_hash, fields)
            db_connection.execute(
                """INSERT INTO audit_logs (audit_id, org_id, at, actor_type, actor_id, actor_label, actor_role, candidate_id,
                action, target_type, target_id, detail, source, correlation_id, prev_hash, hash)
                VALUES (:audit_id, :org_id, :at, :actor_type, :actor_id, :actor_label, :actor_role, :candidate_id,
                :action, :target_type, :target_id, :detail, :source, :correlation_id, :prev_hash, :hash)""",
                {**fields, "prev_hash": prev_hash, "hash": digest},
            )

        if connection is not None:
            insert(connection)
        else:
            with self.db.write() as write_connection:
                insert(write_connection)
        return audit_id

    def list(
        self,
        org_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        candidate_id: str = "",
        action: str = "",
        actor_id: str = "",
        since: int | None = None,
        until: int | None = None,
    ) -> builtins.list[dict[str, Any]]:
        clauses: builtins.list[str] = ["org_id = ?"]
        params: builtins.list[Any] = [org_id]
        if before_seq:
            clauses.append("seq < ?")
            params.append(before_seq)
        if candidate_id:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        if action:
            clauses.append("action LIKE ?")
            params.append(action.replace("%", "") + "%")
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if since:
            clauses.append("at >= ?")
            params.append(since)
        if until:
            clauses.append("at <= ?")
            params.append(until)
        with self.db.read() as connection:
            rows = many(
                connection,
                f"SELECT seq, audit_id, at, actor_type, actor_id, actor_label, actor_role, candidate_id, action, target_type, target_id, detail, source, correlation_id FROM audit_logs WHERE {' AND '.join(clauses)} ORDER BY seq DESC LIMIT ?",
                (*params, max(1, min(limit, 1000))),
            )
        for row in rows:
            row["detail"] = loads(row["detail"], {})
        return rows

    def stream(
        self, org_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> builtins.list[dict[str, Any]]:
        """Rows after a cursor, oldest first: what a SIEM collector polls."""
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM audit_logs WHERE org_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (org_id, after_seq, max(1, min(limit, 5000))),
            )
        for row in rows:
            row["detail"] = loads(row["detail"], {})
        return rows

    def count(self, org_id: str) -> int:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT COUNT(*) AS n FROM audit_logs WHERE org_id = ?",
                (org_id,),
            )
        return int(row["n"]) if row else 0

    def verify(self, org_id: str) -> dict[str, Any]:
        """Recompute the chain; report the first broken row, if any."""
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM audit_logs WHERE org_id = ? ORDER BY seq",
                (org_id,),
            )
            anchor = one(
                connection,
                "SELECT value FROM cloud_kv WHERE key = ?",
                (f"audit_anchor:{org_id}",),
            )
        previous = anchor["value"] if anchor else (rows[0]["prev_hash"] if rows else "")
        for row in rows:
            fields = {
                k: row[k]
                for k in (
                    "audit_id",
                    "org_id",
                    "at",
                    "actor_type",
                    "actor_id",
                    "actor_label",
                    "actor_role",
                    "candidate_id",
                    "action",
                    "target_type",
                    "target_id",
                    "detail",
                    "source",
                    "correlation_id",
                )
            }
            if row["prev_hash"] != previous or _hash(previous, fields) != row["hash"]:
                return {"ok": False, "checked": len(rows), "broken_at": row["seq"]}
            previous = row["hash"]
        return {"ok": True, "checked": len(rows), "broken_at": None}

    def export_lines(self, org_id: str) -> builtins.list[dict[str, Any]]:
        """Every row, oldest first (the SIEM export and the organization export)."""
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM audit_logs WHERE org_id = ? ORDER BY seq",
                (org_id,),
            )
        for row in rows:
            row["detail"] = loads(row["detail"], {})
        return rows

    def prune(self, org_id: str, older_than: int) -> int:
        """Delete rows older than the cutoff (the plan's audit retention); keep the chain verifiable."""
        with self.db.write() as connection:
            last = one(
                connection,
                "SELECT hash FROM audit_logs WHERE org_id = ? AND at < ? ORDER BY seq DESC LIMIT 1",
                (org_id, older_than),
            )
            if last is None:
                return 0
            _set_maintenance(connection, True)
            try:
                removed = connection.execute(
                    "DELETE FROM audit_logs WHERE org_id = ? AND at < ?",
                    (org_id, older_than),
                ).rowcount
            finally:
                _set_maintenance(connection, False)
            connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"audit_anchor:{org_id}", last["hash"]),
            )
        return removed

    def delete_org(self, org_id: str) -> tuple[int, str]:
        """Remove an organization's rows when it is deleted; return ``(count, last hash)`` for the stub."""
        with self.db.write() as connection:
            last = one(
                connection,
                "SELECT hash FROM audit_logs WHERE org_id = ? ORDER BY seq DESC LIMIT 1",
                (org_id,),
            )
            count = one(
                connection,
                "SELECT COUNT(*) AS n FROM audit_logs WHERE org_id = ?",
                (org_id,),
            )
            _set_maintenance(connection, True)
            try:
                connection.execute("DELETE FROM audit_logs WHERE org_id = ?", (org_id,))
            finally:
                _set_maintenance(connection, False)
            connection.execute(
                "DELETE FROM cloud_kv WHERE key = ?", (f"audit_anchor:{org_id}",)
            )
        return (int(count["n"]) if count else 0, last["hash"] if last else "")


class PlatformAudit:
    """Operator actions, kept apart from tenant logs and retained 7 years."""

    def __init__(self, db: ControlDB, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def record(
        self,
        action: str,
        *,
        operator: Principal | None = None,
        operator_label: str = "",
        org_id: str = "",
        target_type: str = "",
        target_id: str = "",
        detail: Mapping[str, Any] | None = None,
        source: str = "operator",
        correlation_id: str = "",
        connection: sqlite3.Connection | None = None,
    ) -> str:
        audit_id = new_id()
        fields = {
            "audit_id": audit_id,
            "at": self.clock.now(),
            "operator_id": operator.operator_id if operator else "",
            "operator_label": (operator.label if operator else operator_label)[:200],
            "org_id": org_id,
            "action": action[:80],
            "target_type": target_type[:40],
            "target_id": str(target_id)[:256],
            "detail": dumps(scrub(dict(detail or {})))[:4000],
            "source": source[:40],
            "correlation_id": correlation_id[:64],
        }

        def insert(db_connection: sqlite3.Connection) -> None:
            lock(db_connection, "platform_audit")
            previous = one(
                db_connection,
                "SELECT hash FROM platform_audit_logs ORDER BY seq DESC LIMIT 1",
            )
            prev_hash = previous["hash"] if previous else ""
            db_connection.execute(
                """INSERT INTO platform_audit_logs (audit_id, at, operator_id, operator_label, org_id, action, target_type,
                target_id, detail, source, correlation_id, prev_hash, hash)
                VALUES (:audit_id, :at, :operator_id, :operator_label, :org_id, :action, :target_type, :target_id, :detail,
                :source, :correlation_id, :prev_hash, :hash)""",
                {**fields, "prev_hash": prev_hash, "hash": _hash(prev_hash, fields)},
            )

        if connection is not None and not getattr(connection, "tenant", False):
            insert(connection)
        else:
            # Under row-level security a tenant transaction may not touch the platform log, so an operator
            # action recorded from tenant code goes in its own platform transaction (its own lock, so it
            # cannot wait on the tenant transaction that is still open).
            with platform_scope(), self.db.write(exclusive=False) as write_connection:
                insert(write_connection)
        return audit_id

    def list(
        self, *, org_id: str = "", limit: int = 100, before_seq: int | None = None
    ) -> builtins.list[dict[str, Any]]:
        clauses: builtins.list[str] = ["1 = 1"]
        params: builtins.list[Any] = []
        if org_id:
            clauses.append("org_id = ?")
            params.append(org_id)
        if before_seq:
            clauses.append("seq < ?")
            params.append(before_seq)
        with self.db.read() as connection:
            rows = many(
                connection,
                f"SELECT * FROM platform_audit_logs WHERE {' AND '.join(clauses)} ORDER BY seq DESC LIMIT ?",
                (*params, max(1, min(limit, 1000))),
            )
        for row in rows:
            row["detail"] = loads(row["detail"], {})
        return rows

    def verify(self) -> dict[str, Any]:
        with self.db.read() as connection:
            rows = many(connection, "SELECT * FROM platform_audit_logs ORDER BY seq")
        previous = rows[0]["prev_hash"] if rows else ""
        for row in rows:
            fields = {
                k: row[k]
                for k in (
                    "audit_id",
                    "at",
                    "operator_id",
                    "operator_label",
                    "org_id",
                    "action",
                    "target_type",
                    "target_id",
                    "detail",
                    "source",
                    "correlation_id",
                )
            }
            if row["prev_hash"] != previous or _hash(previous, fields) != row["hash"]:
                return {"ok": False, "checked": len(rows), "broken_at": row["seq"]}
            previous = row["hash"]
        return {"ok": True, "checked": len(rows), "broken_at": None}
