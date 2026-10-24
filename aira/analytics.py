"""Analytics aggregates with explicit metric definitions and safe rates.

Every rate is ``None`` when its denominator is zero so the UI never shows a
misleading percentage. Filters: date range (on job first_seen/applied events),
source, and stage.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

PIPELINE_STAGES = (
    "discovered",
    "saved",
    "ready_to_apply",
    "applying",
    "needs_review",
    "applied",
    "assessment",
    "recruiter_screen",
    "technical_interview",
    "manager_interview",
    "panel_interview",
    "final_interview",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
    "failed",
)
INTERVIEW_STAGES = (
    "recruiter_screen",
    "technical_interview",
    "manager_interview",
    "panel_interview",
    "final_interview",
)
RESPONSE_STAGES = (*INTERVIEW_STAGES, "assessment", "offer", "accepted", "rejected")
APPLIED_STAGES = ("applied", *RESPONSE_STAGES, "withdrawn")

METRIC_DEFINITIONS: dict[str, str] = {
    "jobs_discovered": "Jobs first seen in the selected period, after deduplication.",
    "jobs_saved": "Jobs marked as saved in the period.",
    "applications_prepared": "Jobs that reached Ready to Apply (approved documents) in the period.",
    "applications_submitted": "Jobs whose status first became Applied in the period.",
    "interview_rate": "Submitted applications that reached any interview stage, divided by submitted applications.",
    "response_rate": "Submitted applications that received any response (assessment, interview, offer, or rejection), divided by submitted applications.",
    "offer_rate": "Submitted applications that reached Offer or Accepted, divided by submitted applications.",
    "source_conversion": "Per source: submitted applications divided by jobs discovered from that source.",
    "automation_success_rate": "Scheduled runs that finished without error, divided by all scheduled runs in the period.",
}


def _period(
    days: int | None, start: str | None, end: str | None
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if start or end:
        begin = (
            datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
            if start
            else now - timedelta(days=3650)
        )
        finish = (
            (
                datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
                + timedelta(days=1)
            )
            if end
            else now
        )
        return begin, finish
    return now - timedelta(days=days or 30), now


def _rate(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _sql_ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def summary(
    connection: sqlite3.Connection,
    *,
    days: int | None = 30,
    start: str | None = None,
    end: str | None = None,
    source: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    begin, finish = _period(days, start, end)
    begin_ts, finish_ts = _sql_ts(begin), _sql_ts(finish)
    begin_epoch, finish_epoch = int(begin.timestamp()), int(finish.timestamp())
    job_filters, job_params = ["jobs.archived_at IS NULL"], []
    if source:
        job_filters.append(
            "EXISTS (SELECT 1 FROM job_source_records r WHERE r.job_id = jobs.job_id AND r.source = ?)"
        )
        job_params.append(source)
    if stage:
        job_filters.append("jobs.stage = ?")
        job_params.append(stage)
    job_where = " AND ".join(job_filters)

    discovered = connection.execute(
        f"SELECT COUNT(*) FROM jobs WHERE {job_where} AND COALESCE(NULLIF(jobs.first_seen_at, ''), jobs.updated_at) BETWEEN ? AND ?",
        (*job_params, begin_ts, finish_ts),
    ).fetchone()[0]
    saved = connection.execute(
        f"SELECT COUNT(*) FROM jobs WHERE {job_where} AND jobs.saved = 1 AND COALESCE(jobs.saved_at, jobs.updated_at) BETWEEN ? AND ?",
        (*job_params, begin_ts, finish_ts),
    ).fetchone()[0]

    def event_jobs(statuses: tuple[str, ...]) -> set[str]:
        placeholders = ",".join("?" for _ in statuses)
        rows = connection.execute(
            f"""SELECT DISTINCT application_events.job_id FROM application_events
            JOIN jobs ON jobs.job_id = application_events.job_id
            WHERE {job_where} AND application_events.new_status IN ({placeholders})
            AND application_events.occurred_at BETWEEN ? AND ?""",
            (*job_params, *statuses, begin_epoch, finish_epoch),
        ).fetchall()
        return {row[0] for row in rows}

    prepared_jobs = event_jobs(("ready_to_apply",))
    submitted_jobs = event_jobs(("applied",))
    interview_jobs = (
        event_jobs(INTERVIEW_STAGES) & submitted_jobs if submitted_jobs else set()
    )
    response_jobs = (
        event_jobs(RESPONSE_STAGES) & submitted_jobs if submitted_jobs else set()
    )
    offer_jobs = (
        event_jobs(("offer", "accepted")) & submitted_jobs if submitted_jobs else set()
    )

    pipeline_rows = connection.execute(
        f"SELECT jobs.stage, COUNT(*) AS count FROM jobs WHERE {job_where} GROUP BY jobs.stage",
        job_params,
    ).fetchall()
    pipeline_counts = {row["stage"]: row["count"] for row in pipeline_rows}
    pipeline = [
        {"stage": item, "count": pipeline_counts.get(item, 0)}
        for item in PIPELINE_STAGES
    ]

    by_source_rows = connection.execute(
        f"""SELECT r.source, COUNT(DISTINCT jobs.job_id) AS discovered,
            SUM(CASE WHEN jobs.stage IN ({",".join("?" for _ in APPLIED_STAGES)}) THEN 1 ELSE 0 END) AS submitted,
            SUM(CASE WHEN jobs.stage IN ({",".join("?" for _ in INTERVIEW_STAGES)}) THEN 1 ELSE 0 END) AS interviewing
        FROM job_source_records r JOIN jobs ON jobs.job_id = r.job_id WHERE {job_where}
        GROUP BY r.source ORDER BY discovered DESC""",
        (*APPLIED_STAGES, *INTERVIEW_STAGES, *job_params),
    ).fetchall()
    by_source = [
        {
            "source": row["source"],
            "discovered": row["discovered"],
            "submitted": row["submitted"] or 0,
            "interviewing": row["interviewing"] or 0,
            "conversion": _rate(row["submitted"] or 0, row["discovered"]),
        }
        for row in by_source_rows
    ]

    top_companies = [
        dict(row)
        for row in connection.execute(
            f"""SELECT jobs.company, COUNT(*) AS jobs,
                SUM(CASE WHEN jobs.stage IN ({",".join("?" for _ in APPLIED_STAGES)}) THEN 1 ELSE 0 END) AS applications
            FROM jobs WHERE {job_where} AND jobs.company != '' GROUP BY lower(jobs.company) ORDER BY applications DESC, jobs DESC LIMIT 10""",
            (*APPLIED_STAGES, *job_params),
        ).fetchall()
    ]

    runs = connection.execute(
        "SELECT status, COUNT(*) AS count FROM automation_runs WHERE finished_at BETWEEN ? AND ? GROUP BY status",
        (begin_epoch, finish_epoch),
    ).fetchall()
    run_counts = {row["status"]: row["count"] for row in runs}
    run_total = sum(run_counts.values())

    recruiter_activity = connection.execute(
        "SELECT COUNT(*) FROM recruiter_interactions WHERE occurred_at BETWEEN ? AND ?",
        (begin.isoformat(), finish.isoformat()),
    ).fetchone()[0]
    recruiter_count = connection.execute(
        "SELECT COUNT(*) FROM recruiters WHERE deleted_at IS NULL"
    ).fetchone()[0]
    interviews_scheduled = connection.execute(
        "SELECT COUNT(*) FROM interviews WHERE status != 'cancelled' AND start_at BETWEEN ? AND ?",
        (begin.isoformat(), finish.isoformat()),
    ).fetchone()[0]

    resume_versions = [
        dict(row)
        for row in connection.execute(
            f"""SELECT COALESCE(NULLIF(jobs.resume_hash, ''), 'unversioned') AS resume_hash, COUNT(*) AS applications,
                SUM(CASE WHEN jobs.stage IN ({",".join("?" for _ in INTERVIEW_STAGES)}) OR jobs.stage IN ('offer', 'accepted') THEN 1 ELSE 0 END) AS interviews
            FROM jobs WHERE {job_where} AND jobs.stage IN ({",".join("?" for _ in APPLIED_STAGES)})
            GROUP BY resume_hash ORDER BY applications DESC LIMIT 10""",
            (*INTERVIEW_STAGES, *job_params, *APPLIED_STAGES),
        ).fetchall()
    ]
    for item in resume_versions:
        item["interview_rate"] = _rate(item["interviews"] or 0, item["applications"])
    cover_letters = connection.execute(
        f"""SELECT SUM(CASE WHEN jobs.cover_letter != '' THEN 1 ELSE 0 END) AS with_letter,
            SUM(CASE WHEN jobs.cover_letter = '' THEN 1 ELSE 0 END) AS without_letter,
            SUM(CASE WHEN jobs.cover_letter != '' AND jobs.stage IN ({",".join("?" for _ in RESPONSE_STAGES)}) THEN 1 ELSE 0 END) AS with_letter_responses,
            SUM(CASE WHEN jobs.cover_letter = '' AND jobs.stage IN ({",".join("?" for _ in RESPONSE_STAGES)}) THEN 1 ELSE 0 END) AS without_letter_responses
        FROM jobs WHERE {job_where} AND jobs.stage IN ({",".join("?" for _ in APPLIED_STAGES)})""",
        (*RESPONSE_STAGES, *RESPONSE_STAGES, *job_params, *APPLIED_STAGES),
    ).fetchone()

    return {
        "period": {
            "start": begin.isoformat(timespec="seconds"),
            "end": finish.isoformat(timespec="seconds"),
            "days": days,
        },
        "filters": {"source": source, "stage": stage},
        "definitions": METRIC_DEFINITIONS,
        "metrics": {
            "jobs_discovered": discovered,
            "jobs_saved": saved,
            "applications_prepared": len(prepared_jobs),
            "applications_submitted": len(submitted_jobs),
            "interviews_scheduled": interviews_scheduled,
            "interview_rate": _rate(len(interview_jobs), len(submitted_jobs)),
            "response_rate": _rate(len(response_jobs), len(submitted_jobs)),
            "offer_rate": _rate(len(offer_jobs), len(submitted_jobs)),
            "automation_runs": run_total,
            "automation_success_rate": _rate(run_counts.get("ok", 0), run_total),
            "automation_failures": run_counts.get("error", 0),
            "recruiters": recruiter_count,
            "recruiter_interactions": recruiter_activity,
        },
        "pipeline": pipeline,
        "by_source": by_source,
        "top_companies": top_companies,
        "resume_versions": resume_versions,
        "cover_letters": {
            "with_letter": (cover_letters["with_letter"] or 0) if cover_letters else 0,
            "without_letter": (cover_letters["without_letter"] or 0)
            if cover_letters
            else 0,
            "with_letter_response_rate": _rate(
                cover_letters["with_letter_responses"] or 0,
                cover_letters["with_letter"] or 0,
            )
            if cover_letters
            else None,
            "without_letter_response_rate": _rate(
                cover_letters["without_letter_responses"] or 0,
                cover_letters["without_letter"] or 0,
            )
            if cover_letters
            else None,
        },
        "drilldown": {
            "submitted_job_ids": sorted(submitted_jobs),
            "interview_job_ids": sorted(interview_jobs),
            "offer_job_ids": sorted(offer_jobs),
        },
    }


def timeline(connection: sqlite3.Connection, *, days: int = 30) -> list[dict[str, Any]]:
    """Daily counts of discovered and applied events for charts."""
    begin = datetime.now(timezone.utc) - timedelta(days=days)
    rows = connection.execute(
        """SELECT date(occurred_at, 'unixepoch') AS day,
            SUM(CASE WHEN new_status = 'discovered' THEN 1 ELSE 0 END) AS discovered,
            SUM(CASE WHEN new_status = 'applied' THEN 1 ELSE 0 END) AS applied,
            SUM(CASE WHEN new_status IN ('recruiter_screen','technical_interview','manager_interview','panel_interview','final_interview') THEN 1 ELSE 0 END) AS interviews
        FROM application_events WHERE occurred_at >= ? GROUP BY day ORDER BY day""",
        (int(begin.timestamp()),),
    ).fetchall()
    return [dict(row) for row in rows]
