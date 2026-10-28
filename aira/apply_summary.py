"""One count of every application, however it was sent (Careers > Applications).

Each job that became an application is counted once, in the state it is in now:

* ``submitted``: a submission is recorded (by you, another way, or by Auto-apply), or the job's status is
  Applied or later.
* ``in_process``: Auto-apply has a run queued or filling the form, or you opened the application and have
  not recorded it yet (status Applying).
* ``needs_you``: an Auto-apply run is held: it needs your answers, or it filled the form and waits for
  your click.
* ``failed``: the last Auto-apply run failed or the form is not supported, or you marked the
  application as failed; nothing was sent.
* ``interviews``: submitted applications that reached an interview (an interview is booked for the job, or
  its status reached an interview stage or an offer). They are also counted as submitted.

``via`` says how it was sent: ``manual`` (you sent it, here or another way) or ``auto`` (Monosage
filled in the form: Auto-apply, "Apply for me" and "Fill it, I'll submit"). Runs that were skipped, or
that found the job already applied to, are not applications of their own. Archived jobs are left out.
"""

from __future__ import annotations

import sqlite3
from typing import Any

__all__ = ["BUCKETS", "VIAS", "applications", "summary"]

BUCKETS = ("submitted", "in_process", "needs_you", "failed", "interviews")
VIAS = ("manual", "auto")
AUTO_METHODS = frozenset({"auto", "assisted"})
INTERVIEW_STAGES = frozenset(
    {
        "recruiter_screen",
        "technical_interview",
        "manager_interview",
        "panel_interview",
        "final_interview",
    }
)
REACHED_INTERVIEW_STAGES = INTERVIEW_STAGES | {"offer", "accepted"}
# Statuses that mean the job was sent, even when no submission row exists (moved there by hand).
SENT_STAGES = frozenset(
    {"applied", "assessment", *REACHED_INTERVIEW_STAGES, "rejected", "withdrawn"}
)
RUN_BUCKETS = {
    "queued": "in_process",
    "running": "in_process",
    "needs_review": "needs_you",
    "ready": "needs_you",
    "failed": "failed",
    "unsupported": "failed",
}
LIST_LIMIT = 200


def _table(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every application, newest activity first: one row per job with its bucket and how it was sent."""
    has_runs = _table(connection, "application_runs")
    has_interviews = _table(connection, "interviews")
    jobs = {
        row["job_id"]: dict(row)
        for row in connection.execute(
            "SELECT job_id, title, company, stage, updated_at FROM jobs WHERE archived_at IS NULL"
        ).fetchall()
    }
    submissions = {
        row["job_id"]: dict(row)
        for row in connection.execute(
            "SELECT job_id, method, submitted_at, confirmation_number FROM application_submissions"
        ).fetchall()
    }
    runs: dict[str, dict[str, Any]] = {}
    if has_runs:
        # The newest run per job decides its state (rowid breaks ties within one second).
        for row in connection.execute(
            "SELECT run_id, job_id, mode, status, created_at, finished_at, error FROM application_runs "
            "WHERE status NOT IN ('skipped', 'already_applied') ORDER BY created_at ASC, rowid ASC"
        ).fetchall():
            runs[row["job_id"]] = dict(row)
    interviewed: set[str] = set()
    if has_interviews:
        interviewed = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT job_id FROM interviews WHERE job_id IS NOT NULL AND status != 'cancelled'"
            ).fetchall()
        }
    placeholders = ",".join("?" for _ in INTERVIEW_STAGES)
    interviewed |= {
        row[0]
        for row in connection.execute(
            f"SELECT DISTINCT job_id FROM application_events WHERE new_status IN ({placeholders})",
            tuple(INTERVIEW_STAGES),
        ).fetchall()
    }

    out: list[dict[str, Any]] = []
    for job_id, job in jobs.items():
        submission = submissions.get(job_id)
        run = runs.get(job_id)
        stage = job["stage"]
        bucket = via = ""
        when = job["updated_at"] or ""
        if submission is not None:
            bucket = "submitted"
            via = "auto" if submission["method"] in AUTO_METHODS else "manual"
            when = submission["submitted_at"] or when
        elif stage in SENT_STAGES:
            bucket, via = (
                "submitted",
                "auto" if run and run["status"] == "submitted" else "manual",
            )
        elif run is not None and run["status"] in RUN_BUCKETS:
            bucket, via = RUN_BUCKETS[run["status"]], "auto"
            when = run["finished_at"] or run["created_at"] or when
        elif stage == "applying":
            bucket, via = "in_process", "manual"
        elif stage == "failed":
            bucket, via = "failed", "auto" if run is not None else "manual"
        if not bucket:
            continue
        reached = bucket == "submitted" and (
            job_id in interviewed or stage in REACHED_INTERVIEW_STAGES
        )
        out.append(
            {
                "job_id": job_id,
                "title": job["title"],
                "company": job["company"],
                "stage": stage,
                "bucket": bucket,
                "via": via,
                "interview": reached,
                "method": submission["method"] if submission else None,
                "confirmation_number": submission["confirmation_number"]
                if submission
                else "",
                "run_id": run["run_id"] if run else None,
                "run_status": run["status"] if run else None,
                "run_error": (run["error"] or "")[:300] if run else "",
                "at": str(when),
            }
        )
    out.sort(key=lambda item: item["at"], reverse=True)
    return out


def _empty() -> dict[str, int]:
    return {bucket: 0 for bucket in BUCKETS}


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Counts for the Careers summary: all, and split by how each application was sent."""
    counts = _empty()
    by_via = {via: _empty() for via in VIAS}
    totals = {via: 0 for via in VIAS}
    for item in _rows(connection):
        keys = [item["bucket"], *(["interviews"] if item["interview"] else [])]
        for key in keys:
            counts[key] += 1
            by_via[item["via"]][key] += 1
        totals[item["via"]] += 1
    attempts: dict[str, int] = {}
    searching = False
    if _table(connection, "application_runs"):
        attempts = {
            row["status"]: row["n"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS n FROM application_runs GROUP BY status"
            ).fetchall()
        }
    if _table(connection, "autopilot_passes"):
        searching = (
            connection.execute(
                "SELECT 1 FROM autopilot_passes WHERE status = 'running' LIMIT 1"
            ).fetchone()
            is not None
        )
    return {
        "total": sum(totals.values()),
        "counts": counts,
        "by_via": by_via,
        "totals_by_via": totals,
        "attempts": attempts,
        "searching": searching,
    }


def applications(
    connection: sqlite3.Connection,
    *,
    bucket: str = "",
    via: str = "",
    limit: int = LIST_LIMIT,
) -> list[dict[str, Any]]:
    """The applications behind one count (``bucket`` and/or ``via``; empty means all)."""
    if bucket and bucket not in BUCKETS:
        raise ValueError("Choose one of: " + ", ".join(BUCKETS) + ".")
    if via and via not in VIAS:
        raise ValueError("Choose manual or auto.")
    items = _rows(connection)
    if bucket == "interviews":
        items = [item for item in items if item["interview"]]
    elif bucket:
        items = [item for item in items if item["bucket"] == bucket]
    if via:
        items = [item for item in items if item["via"] == via]
    return items[: max(1, min(int(limit), LIST_LIMIT))]
