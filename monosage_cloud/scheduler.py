"""Finds the work that is due and puts it on the queue, one job per candidate and kind.

This replaces the local build's in-process scheduler thread. It reads an active candidate's workspace read-only
to see whether a search plan, a schedule or a monitor is due, and skips organizations that the gate would refuse
anyway, so a paused or lapsed tenant adds nothing to the queue.

It does not open every workspace on every tick: after reading one it records when to look again
(``workspaces.due_check_at``; ``organizations.monitors_check_at`` for an organization's own monitors): the next
plan, schedule, monitor or retry, a minute while something is due, and at most
``scheduler_idle_recheck_seconds`` later otherwise, together with the file's size and modification time. Until
then a tick only looks at the file's metadata (one ``stat``, no SQLite open): a workspace whose file changed is read
again on the next tick, and a change made through the gateway (:meth:`Scheduler.touch`) clears the mark as well.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from monosage_cloud.db import many
from monosage_cloud.workspace import _to_epoch

if TYPE_CHECKING:
    from monosage_cloud.app import CloudApp

logger = logging.getLogger("monosage_cloud.scheduler")


class Scheduler:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def touch(self, org_id: str, candidate_id: str = "") -> None:
        """Something changed in a workspace: look at it on the next tick instead of at its recorded time."""
        try:
            with self.app.db.write() as connection:
                if candidate_id:
                    connection.execute(
                        "UPDATE workspaces SET due_check_at = NULL WHERE org_id = ? AND candidate_id = ?",
                        (org_id, candidate_id),
                    )
                else:
                    connection.execute(
                        "UPDATE organizations SET monitors_check_at = NULL WHERE org_id = ?",
                        (org_id,),
                    )
        except Exception:  # noqa: BLE001 - the idle recheck finds it later anyway
            logger.debug("Could not mark %s for the scheduler", org_id, exc_info=True)

    @staticmethod
    def signature(path: Path) -> str:
        """Size and modification time of a workspace file and its write-ahead log: changes whenever it is written."""
        parts = []
        for candidate in (path, path.with_name(path.name + "-wal")):
            try:
                stat = candidate.stat()
            except OSError:
                parts.append("-")
                continue
            # an empty log is the same as none (opening the file read-only can create one)
            parts.append(f"{stat.st_size}:{stat.st_mtime_ns}" if stat.st_size else "-")
        return "|".join(parts)

    @staticmethod
    def _unchanged(
        row: dict[str, Any], at: str, sig: str, current: str, now: int
    ) -> bool:
        return bool(
            row.get(at) is not None and int(row[at]) > now and row.get(sig) == current
        )

    def _next_check(self, now: int, due: dict[str, Any], next_due: int | None) -> int:
        if any(due.values()):
            return now + 60
        idle = max(60, int(self.app.config.scheduler_idle_recheck_seconds))
        if next_due is not None:
            return max(now + 30, min(int(next_due), now + idle))
        return now + idle

    def enqueue_due(self, renew: Any = None) -> dict[str, int]:
        """``renew`` is called as the pass goes (it keeps the tick's lease); when it answers False the pass stops."""
        now = self.app.clock.now()
        now_iso = datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"
        )
        counts = {
            "autopilot.due": 0,
            "schedules.due": 0,
            "monitors.due": 0,
            "monitors.org": 0,
            "suite.tick": 0,
            "skipped": 0,
        }
        with self.app.db.read() as connection:
            rows = many(
                connection,
                """SELECT w.candidate_id, w.org_id, w.data_path, w.next_due_at, w.due_check_at, w.due_check_sig,
                w.linkedin_port, w.linkedin_host FROM workspaces w
                JOIN organizations o ON o.org_id = w.org_id
                WHERE w.status = 'active' AND o.deleted_at IS NULL AND o.status = 'active'""",
            )
        blocked_orgs: dict[str, bool] = {}
        counts["opened"] = 0
        for row in rows:
            if renew is not None and renew() is False:
                break  # another pass holds the lease now
            org_id, candidate_id = row["org_id"], row["candidate_id"]
            data_path = Path(row["data_path"])
            sig = self.signature(data_path)
            if self._unchanged(row, "due_check_at", "due_check_sig", sig, now):
                continue  # nothing due before its recorded time, and the file is as it was
            if org_id not in blocked_orgs:
                blocked_orgs[org_id] = (
                    self.app.gate.automation_block(org_id, "") is not None
                )
            if blocked_orgs[org_id] or self.app.gate.automation_block(
                org_id, candidate_id
            ):
                counts["skipped"] += 1
                continue
            due = self._due(data_path, now_iso)
            counts["opened"] += 1
            next_due = due.pop("next_due")
            due.pop("next_monitor", None)
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
                    "UPDATE workspaces SET next_due_at = ?, due_check_at = ?, due_check_sig = ? WHERE candidate_id = ?",
                    (
                        now + 60 if any(due.values()) else (next_due or now + 300),
                        self._next_check(now, due, next_due),
                        sig,
                        candidate_id,
                    ),
                )
        counts["monitors.org"] = self._enqueue_org_monitors(now_iso, blocked_orgs)
        counts["suite.tick"] = self._enqueue_suite_ticks(now)
        return counts

    def _enqueue_suite_ticks(self, now: int) -> int:
        """Every five minutes, each organization that has an industry or life app: their periodic checks and due
        mail (aira.suite.background), as one job per organization per window."""
        from aira import apps as app_catalog
        from aira.suite import background

        suite_apps = {
            app.id for app in (*app_catalog.INDUSTRY_APPS, *app_catalog.LIFE_APPS)
        }
        window = now // background.INTERVAL_SECONDS
        with self.app.db.read() as connection:
            orgs = many(
                connection,
                "SELECT org_id FROM organizations WHERE deleted_at IS NULL AND status = 'active'",
            )
        queued = 0
        for org in orgs:
            org_id = org["org_id"]
            members = self.app.workspaces.member_apps_paths(org_id)
            if not members and not self.app.workspaces.org_api_path(org_id).exists():
                continue
            if not suite_apps & set(self.app.entitlements.resolve(org_id).apps):
                continue
            has_org_apps = self.app.workspaces.org_api_path(org_id).exists()
            if has_org_apps and self.app.queue.enqueue(
                org_id,
                "suite.tick",
                dedupe_key=f"suite.tick:{org_id}:{window}",
                priority=7,
            ):
                queued += 1
            # each member's own business apps (monosage_cloud/spaces.py) have reminders and due mail of their own
            for member_id, _path in members:
                if not self.app.workspaces.member_apps_live(org_id, member_id):
                    continue  # removed, or the organization works in shared apps now
                if self.app.queue.enqueue(
                    org_id,
                    "suite.tick",
                    payload={"member_id": member_id},
                    dedupe_key=f"suite.tick:{org_id}:{member_id}:{window}",
                    priority=7,
                ):
                    queued += 1
        return queued

    def _enqueue_org_monitors(self, now_iso: str, blocked_orgs: dict[str, bool]) -> int:
        """The organization's own API monitors live in its API workspace, not a candidate's."""
        now = int(datetime.fromisoformat(now_iso).timestamp())
        with self.app.db.read() as connection:
            orgs = many(
                connection,
                "SELECT org_id, monitors_check_at, monitors_check_sig FROM organizations "
                "WHERE deleted_at IS NULL AND status = 'active'",
            )
        queued = 0
        for org in orgs:
            org_id = org["org_id"]
            path = self.app.workspaces.org_api_path(org_id)
            sig = self.signature(path)
            if self._unchanged(
                org, "monitors_check_at", "monitors_check_sig", sig, now
            ):
                continue
            if not path.exists():
                self._mark_org(org_id, self._next_check(now, {}, None), sig)
                continue
            if org_id not in blocked_orgs:
                blocked_orgs[org_id] = (
                    self.app.gate.automation_block(org_id, "") is not None
                )
            if blocked_orgs[org_id]:
                continue
            due = self._due(path, now_iso)
            self._mark_org(
                org_id,
                self._next_check(
                    now, {"monitors": due["monitors"]}, due.get("next_monitor")
                ),
                sig,
            )
            if not due["monitors"]:
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

    def _mark_org(self, org_id: str, at: int, sig: str) -> None:
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET monitors_check_at = ?, monitors_check_sig = ? WHERE org_id = ?",
                (at, sig, org_id),
            )

    @staticmethod
    def _autopilot_recovery(
        connection: sqlite3.Connection, now_iso: str
    ) -> dict[str, Any]:
        """Auto-apply work besides plans: a retry that is due, or a run running past its time limit (a lost worker).

        Either queues ``autopilot.due``, whose worker interrupts lost runs and tries due retries before the plans
        (``auto_apply_assistant._autopilot_recover``). ``next`` is when the next of them comes due (epoch).
        """
        from aira.autopilot import RUN_TIME_LIMIT

        moment = datetime.fromisoformat(now_iso)
        stale_iso = (moment - RUN_TIME_LIMIT).isoformat(timespec="seconds")
        try:
            due = connection.execute(
                "SELECT 1 FROM application_runs WHERE (status = 'queued' AND next_try_at IS NOT NULL AND next_try_at <= ?) "
                "OR (status = 'running' AND COALESCE(started_at, created_at) < ?) LIMIT 1",
                (now_iso, stale_iso),
            ).fetchone()
            row = connection.execute(
                "SELECT MIN(next_try_at), (SELECT MIN(COALESCE(started_at, created_at)) FROM application_runs "
                "WHERE status = 'running') FROM application_runs WHERE status = 'queued' AND next_try_at IS NOT NULL"
            ).fetchone()
        except sqlite3.Error:  # no runs table yet, or before migration 21
            return {"due": False, "next": None}
        moments = [_to_epoch(row[0]) if row else None]
        if row and row[1]:
            started = _to_epoch(row[1])
            if started is not None:
                moments.append(started + int(RUN_TIME_LIMIT.total_seconds()) + 1)
        upcoming = [value for value in moments if value is not None]
        return {"due": due is not None, "next": min(upcoming) if upcoming else None}

    def _due(self, path: Path, now_iso: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plans": False,
            "schedules": False,
            "monitors": False,
            "next_due": None,
        }
        if not path.exists():
            return result
        result[
            "next_any"
        ] = []  # the soonest of everything (plans, retries, schedules, monitors)
        result["next_monitor"] = None
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
            recovery = self._autopilot_recovery(connection, now_iso)
            result["plans"] = result["plans"] or recovery["due"]
            if recovery["next"] is not None and (
                result["next_due"] is None or recovery["next"] < result["next_due"]
            ):
                result["next_due"] = recovery["next"]
            try:
                result["schedules"] = bool(
                    connection.execute(
                        "SELECT 1 FROM schedules WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                        (int(datetime.fromisoformat(now_iso).timestamp()),),
                    ).fetchone()
                )
                upcoming = connection.execute(
                    "SELECT MIN(next_run_at) FROM schedules WHERE enabled = 1"
                ).fetchone()[0]
                if upcoming is not None:
                    result["next_any"].append(int(upcoming))
            except (sqlite3.Error, TypeError, ValueError):
                pass
            try:
                result["monitors"] = bool(
                    connection.execute(
                        "SELECT 1 FROM airaapi_monitors WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                        (now_iso,),
                    ).fetchone()
                )
                upcoming = _to_epoch(
                    connection.execute(
                        "SELECT MIN(next_run_at) FROM airaapi_monitors WHERE enabled = 1"
                    ).fetchone()[0]
                )
                if upcoming is not None:
                    result["next_any"].append(upcoming)
                    result["next_monitor"] = upcoming
            except sqlite3.Error:
                pass
        except sqlite3.Error:
            logger.debug("Could not read due work from %s", path.name, exc_info=True)
        finally:
            connection.close()
        upcoming = [
            value
            for value in [result["next_due"], *result.pop("next_any", [])]
            if value is not None
        ]
        result["next_due"] = min(upcoming) if upcoming else None
        return result
