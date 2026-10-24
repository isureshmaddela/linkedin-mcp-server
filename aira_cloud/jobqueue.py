"""A tenant-fair job queue in the control-plane database.

Every job carries ``org_id`` (and usually ``candidate_id``). Leasing picks the waiting job of the
organization with the fewest jobs running, so one tenant with a long backlog cannot starve another,
and a per-organization concurrency cap keeps a burst from occupying the whole pool. One candidate
runs one automation job at a time (the same rule as the local single-account slot).

The SQL is portable; on PostgreSQL the lease uses ``FOR UPDATE SKIP LOCKED`` instead of the
write lock SQLite takes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aira_cloud.db import dumps, loads, many, new_id, one, try_lock

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.queue")

AUTOMATION_KINDS = (
    "autopilot.due",
    "autopilot.exec",
    "schedules.due",
    "inbox.pass",
    "inbox.sync",
)
RETRY_BACKOFF = (30, 120, 600)


class JobQueue:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ producers

    def enqueue(
        self,
        org_id: str,
        kind: str,
        *,
        candidate_id: str = "",
        payload: dict[str, Any] | None = None,
        run_at: int | None = None,
        priority: int = 5,
        dedupe_key: str = "",
        max_attempts: int = 3,
        host: str = "",
    ) -> str | None:
        """Queue a job. With a ``dedupe_key`` an identical job already waiting or running is not added (returns None)."""
        job_id = new_id()
        with self.db.write() as connection:
            if dedupe_key:
                existing = one(
                    connection,
                    "SELECT job_id FROM queue_jobs WHERE org_id = ? AND dedupe_key = ? AND status IN ('queued', 'leased')",
                    (org_id, dedupe_key),
                )
                if existing:
                    return None
            connection.execute(
                """INSERT INTO queue_jobs (job_id, org_id, candidate_id, kind, payload, dedupe_key, status, priority, run_at, max_attempts, created_at, host)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    org_id,
                    candidate_id,
                    kind,
                    dumps(payload or {}),
                    dedupe_key,
                    priority,
                    run_at if run_at is not None else self.now,
                    max_attempts,
                    self.now,
                    host,
                ),
            )
        return job_id

    def active_for(
        self, org_id: str, candidate_id: str, kinds: tuple[str, ...] = AUTOMATION_KINDS
    ) -> dict[str, Any] | None:
        marks = ",".join("?" * len(kinds))
        with self.db.read() as connection:
            row = one(
                connection,
                f"SELECT * FROM queue_jobs WHERE org_id = ? AND candidate_id = ? AND status IN ('queued', 'leased') AND kind IN ({marks}) ORDER BY created_at LIMIT 1",
                (org_id, candidate_id, *kinds),
            )
        if row:
            row["payload"] = loads(row["payload"], {})
        return row

    # ------------------------------------------------------------------ consumers

    def lease(
        self,
        worker_id: str,
        kinds: list[str] | None = None,
        *,
        per_org_limit: int | None = None,
        lease_seconds: int | None = None,
        agent_url: str = "",
    ) -> dict[str, Any] | None:
        """Take the next job fairly: the organization with the fewest running jobs goes first, oldest wait breaking ties."""
        per_org = per_org_limit or self.app.config.per_org_concurrency
        seconds = lease_seconds or self.app.config.lease_seconds
        now = self.now
        with self.db.write(
            exclusive=False
        ) as connection:  # per-organization locks, see _claim
            clauses: list[str] = ["status = 'queued'", "run_at <= ?"]
            params: list[Any] = [now]
            if kinds:
                clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
                params.extend(kinds)
            ready = many(
                connection,
                f"SELECT job_id, org_id, candidate_id, kind, priority, run_at, host FROM queue_jobs WHERE {' AND '.join(clauses)} ORDER BY priority, run_at, created_at LIMIT 500",
                params,
            )
            # A job pinned to the host that holds the candidate's LinkedIn session waits for that host,
            # unless the host stopped reporting (then any worker may take it and fail gracefully).
            live = {
                r["agent_url"]
                for r in many(
                    connection,
                    "SELECT agent_url FROM worker_heartbeats WHERE agent_url != '' AND last_seen_at > ?",
                    (now - 60,),
                )
            }
            ready = [
                job
                for job in ready
                if not job["host"]
                or job["host"] == agent_url
                or job["host"] not in live
            ]
            if not ready:
                return None
            running_by_org = {
                r["org_id"]: int(r["n"])
                for r in many(
                    connection,
                    "SELECT org_id, COUNT(*) AS n FROM queue_jobs WHERE status = 'leased' GROUP BY org_id",
                )
            }
            busy_candidates = {
                (r["org_id"], r["candidate_id"])
                for r in many(
                    connection,
                    "SELECT org_id, candidate_id FROM queue_jobs WHERE status = 'leased' AND candidate_id != ''",
                )
            }
            limits = self._org_limits(connection)
            candidates: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
            seen_orgs: set[str] = set()
            for job in ready:
                org = job["org_id"]
                if (
                    job["candidate_id"]
                    and (org, job["candidate_id"]) in busy_candidates
                ):
                    continue
                if org in seen_orgs:
                    continue
                running = running_by_org.get(org, 0)
                if running >= limits.get(org, per_org):
                    seen_orgs.add(org)
                    continue
                seen_orgs.add(org)
                candidates.append(((job["priority"], running, job["run_at"]), job))
            candidates.sort(key=lambda item: item[0])
            claimed = None
            for _key, best in candidates:
                if self._claim(
                    connection,
                    best,
                    limits.get(best["org_id"], per_org),
                    worker_id,
                    now,
                    seconds,
                ):
                    claimed = best
                    break
            if claimed is None:
                return None
            job = one(
                connection,
                "SELECT * FROM queue_jobs WHERE job_id = ?",
                (claimed["job_id"],),
            )
        if job is None:
            return None
        job["payload"] = loads(job["payload"], {})
        return job

    def _claim(
        self,
        connection: Any,
        job: dict[str, Any],
        org_limit: int,
        worker_id: str,
        now: int,
        seconds: int,
    ) -> bool:
        """Take one job for this worker if it is still queued and its organization and candidate have room.

        The organization is locked (without waiting) for the rest of the transaction, so two workers leasing
        for the same organization cannot both pass the concurrency cap or both take the same candidate, and a
        worker that loses the race moves on to another organization instead of waiting. On PostgreSQL the row itself is claimed with
        ``FOR UPDATE SKIP LOCKED``, so a job another worker is taking right now is skipped, not waited for.
        """
        if not try_lock(connection, f"queue:{job['org_id']}"):
            return False  # another worker is leasing for this organization right now
        running = one(
            connection,
            "SELECT COUNT(*) AS n FROM queue_jobs WHERE org_id = ? AND status = 'leased'",
            (job["org_id"],),
        )
        if running and int(running["n"]) >= org_limit:
            return False
        if job["candidate_id"]:
            busy = one(
                connection,
                "SELECT 1 AS x FROM queue_jobs WHERE org_id = ? AND candidate_id = ? AND status = 'leased'",
                (job["org_id"], job["candidate_id"]),
            )
            if busy:
                return False
        if getattr(connection, "dialect", "sqlite") == "postgres":
            row = connection.execute(
                "SELECT job_id FROM queue_jobs WHERE job_id = ? AND status = 'queued' FOR UPDATE SKIP LOCKED",
                (job["job_id"],),
            ).fetchone()
            if row is None:
                return False
        claimed = connection.execute(
            "UPDATE queue_jobs SET status = 'leased', worker_id = ?, lease_until = ?, attempts = attempts + 1, started_at = ? WHERE job_id = ? AND status = 'queued'",
            (worker_id, now + seconds, now, job["job_id"]),
        ).rowcount
        return claimed == 1

    def _org_limits(self, connection: Any) -> dict[str, int]:
        """Organizations an operator has rate-limited run one job at a time."""
        rows = many(
            connection,
            "SELECT org_id FROM organizations WHERE rate_limited_until IS NOT NULL AND rate_limited_until > ?",
            (self.now,),
        )
        return {r["org_id"]: 1 for r in rows}

    def heartbeat(
        self,
        job_id: str,
        *,
        lease_seconds: int | None = None,
        progress: str | None = None,
        worker_id: str | None = None,
    ) -> bool:
        """Keep the lease alive. With ``worker_id`` it only counts while that worker still holds the job."""
        seconds = lease_seconds or self.app.config.lease_seconds
        mine = " AND worker_id = ?" if worker_id is not None else ""
        owner = (worker_id,) if worker_id is not None else ()
        with self.db.write() as connection:
            if progress is not None:
                connection.execute(
                    f"UPDATE queue_jobs SET lease_until = ?, result = ? WHERE job_id = ? AND status = 'leased'{mine}",
                    (self.now + seconds, progress[:200], job_id, *owner),
                )
            else:
                connection.execute(
                    f"UPDATE queue_jobs SET lease_until = ? WHERE job_id = ? AND status = 'leased'{mine}",
                    (self.now + seconds, job_id, *owner),
                )
            row = one(
                connection,
                "SELECT status, worker_id FROM queue_jobs WHERE job_id = ?",
                (job_id,),
            )
        return bool(
            row
            and row["status"] == "leased"
            and (worker_id is None or row["worker_id"] == worker_id)
        )

    def complete(
        self,
        job_id: str,
        result: dict[str, Any] | str = "",
        *,
        worker_id: str | None = None,
    ) -> None:
        """Finish a leased job. With ``worker_id`` a worker that lost its lease cannot close another worker's run."""
        mine = " AND worker_id = ?" if worker_id is not None else ""
        owner = (worker_id,) if worker_id is not None else ()
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE queue_jobs SET status = 'done', finished_at = ?, result = ?, lease_until = NULL WHERE job_id = ? AND status = 'leased'{mine}",
                (
                    self.now,
                    result if isinstance(result, str) else dumps(result),
                    job_id,
                    *owner,
                ),
            )

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        retry: bool = True,
        worker_id: str | None = None,
    ) -> str:
        """Retry with backoff until attempts run out, then mark the job dead. Returns the new status.

        With ``worker_id`` a worker that no longer holds the lease changes nothing (``"lost"``).
        """
        with self.db.write() as connection:
            job = one(
                connection,
                "SELECT attempts, max_attempts, worker_id, status FROM queue_jobs WHERE job_id = ?",
                (job_id,),
            )
            if job is None:
                return "missing"
            if worker_id is not None and (
                job["worker_id"] != worker_id or job["status"] != "leased"
            ):
                return "lost"
            if retry and job["attempts"] < job["max_attempts"]:
                delay = RETRY_BACKOFF[min(job["attempts"] - 1, len(RETRY_BACKOFF) - 1)]
                connection.execute(
                    "UPDATE queue_jobs SET status = 'queued', run_at = ?, error = ?, worker_id = '', lease_until = NULL WHERE job_id = ?",
                    (self.now + delay, error[:500], job_id),
                )
                return "queued"
            connection.execute(
                "UPDATE queue_jobs SET status = 'dead', error = ?, finished_at = ?, lease_until = NULL WHERE job_id = ?",
                (error[:500], self.now, job_id),
            )
            return "dead"

    def skip(self, job_id: str, reason: str, *, worker_id: str | None = None) -> None:
        """Finish a job that the gate refused: not an error, and not retried."""
        mine = (
            " AND worker_id = ? AND status = 'leased'" if worker_id is not None else ""
        )
        owner = (worker_id,) if worker_id is not None else ()
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE queue_jobs SET status = 'skipped', finished_at = ?, error = ?, lease_until = NULL WHERE job_id = ?{mine}",
                (self.now, reason[:500], job_id, *owner),
            )

    def reap_expired(self) -> int:
        """Return jobs whose worker vanished to the queue (or kill them after the last attempt)."""
        with self.db.write() as connection:
            stale = many(
                connection,
                "SELECT job_id, attempts, max_attempts FROM queue_jobs WHERE status = 'leased' AND lease_until < ?",
                (self.now,),
            )
            for job in stale:
                if job["attempts"] < job["max_attempts"]:
                    connection.execute(
                        "UPDATE queue_jobs SET status = 'queued', worker_id = '', lease_until = NULL, error = 'worker lost' WHERE job_id = ?",
                        (job["job_id"],),
                    )
                else:
                    connection.execute(
                        "UPDATE queue_jobs SET status = 'dead', finished_at = ?, error = 'worker lost', lease_until = NULL WHERE job_id = ?",
                        (self.now, job["job_id"]),
                    )
        return len(stale)

    def cancel_for(self, org_id: str, candidate_id: str) -> int:
        with self.db.write() as connection:
            return connection.execute(
                "UPDATE queue_jobs SET status = 'cancelled', finished_at = ? WHERE org_id = ? AND candidate_id = ? AND status = 'queued'",
                (self.now, org_id, candidate_id),
            ).rowcount

    def prune(self, older_than_days: int = 14) -> int:
        with self.db.write() as connection:
            return connection.execute(
                "DELETE FROM queue_jobs WHERE status IN ('done', 'dead', 'skipped', 'cancelled') AND finished_at < ?",
                (self.now - older_than_days * 86400,),
            ).rowcount

    # ------------------------------------------------------------------ views

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.db.read() as connection:
            row = one(
                connection, "SELECT * FROM queue_jobs WHERE job_id = ?", (job_id,)
            )
        if row:
            row["payload"] = loads(row["payload"], {})
        return row

    def stats(self) -> dict[str, Any]:
        with self.db.read() as connection:
            by_status = {
                r["status"]: int(r["n"])
                for r in many(
                    connection,
                    "SELECT status, COUNT(*) AS n FROM queue_jobs GROUP BY status",
                )
            }
            by_kind = many(
                connection,
                "SELECT kind, status, COUNT(*) AS n FROM queue_jobs WHERE status IN ('queued', 'leased') GROUP BY kind, status",
            )
            oldest = one(
                connection,
                "SELECT MIN(run_at) AS t FROM queue_jobs WHERE status = 'queued' AND run_at <= ?",
                (self.now,),
            )
            by_org = many(
                connection,
                "SELECT org_id, SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued, SUM(CASE WHEN status = 'leased' THEN 1 ELSE 0 END) AS running FROM queue_jobs WHERE status IN ('queued', 'leased') GROUP BY org_id",
            )
            workers = many(
                connection,
                "SELECT * FROM worker_heartbeats WHERE last_seen_at > ?",
                (self.now - 60,),
            )
        return {
            "by_status": by_status,
            "depth": by_status.get("queued", 0),
            "running": by_status.get("leased", 0),
            "by_kind": by_kind,
            "oldest_wait_seconds": max(0, self.now - int(oldest["t"]))
            if oldest and oldest["t"]
            else 0,
            "by_org": by_org,
            "workers": workers,
            "worker_capacity": sum(int(w["capacity"]) for w in workers),
            "worker_busy": sum(int(w["active_jobs"]) for w in workers),
        }

    def register_worker(
        self,
        worker_id: str,
        kind: str,
        host: str,
        capacity: int,
        active: int = 0,
        agent_url: str = "",
    ) -> None:
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO worker_heartbeats (worker_id, kind, host, capacity, active_jobs, started_at, last_seen_at, agent_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET host = excluded.host, capacity = excluded.capacity, active_jobs = excluded.active_jobs,
                last_seen_at = excluded.last_seen_at, agent_url = excluded.agent_url""",
                (
                    worker_id,
                    kind,
                    host,
                    capacity,
                    active,
                    self.now,
                    self.now,
                    agent_url,
                ),
            )

    def deregister_worker(self, worker_id: str) -> None:
        with self.db.write() as connection:
            connection.execute(
                "DELETE FROM worker_heartbeats WHERE worker_id = ?", (worker_id,)
            )
