"""Finds the work that is due and puts it on the queue, one job per candidate and kind.

This replaces the local build's in-process scheduler thread. It reads each active candidate
workspace read-only to see whether a search plan, a schedule or a monitor is due, and skips
organizations that the gate would refuse anyway, so a paused or lapsed tenant adds nothing to the queue.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aira_cloud.db import many
from aira_cloud.workspace import _to_epoch

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.scheduler")


class Scheduler:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def enqueue_due(self) -> dict[str, int]:
        now = self.app.clock.now()
        now_iso = datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"
        )
        counts = {
            "autopilot.due": 0,
            "schedules.due": 0,
            "monitors.due": 0,
            "monitors.org": 0,
            "skipped": 0,
        }
        with self.app.db.read() as connection:
            rows = many(
                connection,
                """SELECT w.candidate_id, w.org_id, w.data_path, w.next_due_at, w.linkedin_port, w.linkedin_host FROM workspaces w
                JOIN organizations o ON o.org_id = w.org_id
                WHERE w.status = 'active' AND o.deleted_at IS NULL AND o.status = 'active'""",
            )
        blocked_orgs: dict[str, bool] = {}
        for row in rows:
            org_id, candidate_id = row["org_id"], row["candidate_id"]
            if row["next_due_at"] is not None and row["next_due_at"] > now:
                continue
            if org_id not in blocked_orgs:
                blocked_orgs[org_id] = (
                    self.app.gate.automation_block(org_id, "") is not None
                )
            if blocked_orgs[org_id] or self.app.gate.automation_block(
                org_id, candidate_id
            ):
                counts["skipped"] += 1
                continue
            due = self._due(Path(row["data_path"]), now_iso)
            next_due = due.pop("next_due")
            for kind, flag in (
                ("autopilot.due", due["plans"]),
                ("schedules.due", due["schedules"] and bool(row["linkedin_port"])),
                ("monitors.due", due["monitors"]),
            ):
                if not flag:
                    continue
                priority = (
                    4
                    if self.app.entitlements.resolve(org_id).has("priority_sources")
                    else 6
                )  # Enterprise work goes first
                job = self.app.queue.enqueue(
                    org_id,
                    kind,
                    candidate_id=candidate_id,
                    dedupe_key=f"{kind}:{candidate_id}",
                    priority=priority,
                    host=row.get("linkedin_host", ""),
                )
                if job:
                    counts[kind] += 1
            with self.app.db.write() as connection:
                connection.execute(
                    "UPDATE workspaces SET next_due_at = ? WHERE candidate_id = ?",
                    (
                        now + 60 if any(due.values()) else (next_due or now + 300),
                        candidate_id,
                    ),
                )
        counts["monitors.org"] = self._enqueue_org_monitors(now_iso, blocked_orgs)
        return counts

    def _enqueue_org_monitors(self, now_iso: str, blocked_orgs: dict[str, bool]) -> int:
        """The organization's own API monitors live in its API workspace, not a candidate's."""
        with self.app.db.read() as connection:
            orgs = many(
                connection,
                "SELECT org_id FROM organizations WHERE deleted_at IS NULL AND status = 'active'",
            )
        queued = 0
        for org in orgs:
            org_id = org["org_id"]
            path = self.app.workspaces.org_api_path(org_id)
            if not path.exists():
                continue
            if org_id not in blocked_orgs:
                blocked_orgs[org_id] = (
                    self.app.gate.automation_block(org_id, "") is not None
                )
            if blocked_orgs[org_id] or not self._due(path, now_iso)["monitors"]:
                continue
            priority = (
                4
                if self.app.entitlements.resolve(org_id).has("priority_sources")
                else 6
            )
            if self.app.queue.enqueue(
                org_id,
                "monitors.org",
                dedupe_key=f"monitors.org:{org_id}",
                priority=priority,
            ):
                queued += 1
        return queued

    def _due(self, path: Path, now_iso: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plans": False,
            "schedules": False,
            "monitors": False,
            "next_due": None,
        }
        if not path.exists():
            return result
        try:
            connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5
            )
        except sqlite3.Error:
            return result
        try:
            result["plans"] = bool(
                connection.execute(
                    "SELECT 1 FROM autopilot_plans WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                    (now_iso,),
                ).fetchone()
            )
            earliest = connection.execute(
                "SELECT MIN(next_run_at) FROM autopilot_plans WHERE enabled = 1"
            ).fetchone()[0]
            result["next_due"] = _to_epoch(earliest)
            try:
                result["schedules"] = bool(
                    connection.execute(
                        "SELECT 1 FROM schedules WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                        (int(datetime.fromisoformat(now_iso).timestamp()),),
                    ).fetchone()
                )
            except sqlite3.Error:
                pass
            try:
                result["monitors"] = bool(
                    connection.execute(
                        "SELECT 1 FROM airaapi_monitors WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                        (now_iso,),
                    ).fetchone()
                )
            except sqlite3.Error:
                pass
        except sqlite3.Error:
            logger.debug("Could not read due work from %s", path.name, exc_info=True)
        finally:
            connection.close()
        return result
