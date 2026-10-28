"""Application preparation, review, submission, and queue orchestration.

Workflow: Discover → Score → Prepare → Prefill → Request review when required
→ Submit → Capture confirmation → Track.

No provider in this build offers a candidate-side official submission API, so
"Submit" is user-performed on the provider's site. Aira prepares the packet,
prefills mapped answers, flags unknown or sensitive questions for review,
opens the application link, and records the user's confirmation evidence with
an idempotency key so one application is never recorded twice.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from aira.audit import record_audit
from aira.hosting import notify_submission

PREP_STATUSES = ("draft", "needs_review", "approved", "submitted", "failed")
# manual: the person sent Aira's packet; assisted / auto: Aira filled or sent it; outside: the person applied
# another way (email, in person, before using Aira), which the daily limit does not pace.
SUBMISSION_METHODS = ("manual", "assisted", "auto", "outside")
EARLIEST_SUBMISSION_YEAR = 1990
DEFAULT_QUESTIONS: tuple[dict[str, Any], ...] = (
    {"label": "First name", "required": True, "type": "text"},
    {"label": "Last name", "required": True, "type": "text"},
    {"label": "Email", "required": True, "type": "email"},
    {"label": "Phone", "required": True, "type": "phone"},
    {"label": "Location (city)", "required": False, "type": "text"},
    {"label": "LinkedIn Profile", "required": False, "type": "url"},
    {"label": "Resume/CV", "required": True, "type": "file"},
    {"label": "Cover Letter", "required": False, "type": "file"},
    {
        "label": "Are you legally authorized to work in the United States?",
        "required": True,
        "type": "select",
        "options": ["Yes", "No"],
    },
    {
        "label": "Will you now or in the future require sponsorship for employment visa status?",
        "required": True,
        "type": "select",
        "options": ["Yes", "No"],
    },
    {"label": "Desired salary", "required": False, "type": "text"},
    {"label": "How did you hear about this job?", "required": False, "type": "text"},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_packet(
    *,
    job: Mapping[str, Any],
    profile: Mapping[str, Any],
    standard_answers: Mapping[str, Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]] | None,
    questions_source: str,
) -> dict[str, Any]:
    """Map and prefill the application questions; never auto-answer sensitive ones."""
    from aira.field_mapping import map_fields, prefill_fields

    question_list = list(questions) if questions else list(DEFAULT_QUESTIONS)
    mappings = map_fields(question_list)
    prefilled = prefill_fields(mappings, profile, standard_answers)
    fields = []
    counts = {
        "prefilled": 0,
        "needs_confirmation": 0,
        "missing": 0,
        "unknown": 0,
        "invalid": 0,
    }
    required_missing = []
    for question, item in zip(question_list, prefilled, strict=False):
        status = item.status
        counts[status] = counts.get(status, 0) + 1
        required = bool(question.get("required"))
        if (
            required
            and status in {"missing", "unknown", "invalid"}
            and item.mapping.field_type != "file"
        ):
            required_missing.append(question.get("label", ""))
        fields.append(
            {
                "label": item.mapping.detected_label,
                "provider_field_id": item.mapping.provider_field_id,
                "canonical_field": item.mapping.canonical_field,
                "field_type": item.mapping.field_type,
                "required": required,
                "options": list(question.get("options") or []),
                "confidence": item.mapping.confidence,
                "match_kind": item.mapping.match_kind,
                "sensitivity": item.mapping.sensitivity,
                "requires_confirmation": item.mapping.requires_confirmation,
                "validation_rule": item.mapping.validation_rule,
                "value": item.value,
                "value_source": item.value_source,
                "status": status,
                "message": item.message,
            }
        )
    review_required = bool(
        counts["needs_confirmation"]
        or counts["unknown"]
        or counts["invalid"]
        or required_missing
    )
    documents_ready = bool(job.get("resume_draft") and job.get("cover_letter"))
    return {
        "fields": fields,
        "questions_source": questions_source,
        "review_required": review_required,
        "summary": {
            **counts,
            "total": len(fields),
            "required_missing": required_missing,
            "documents_ready": documents_ready,
            "sensitive_pending": [
                f["label"] for f in fields if f["status"] == "needs_confirmation"
            ],
            "unknown_questions": [
                f["label"] for f in fields if f["status"] == "unknown"
            ],
        },
    }


def create_preparation(
    connection: sqlite3.Connection,
    *,
    job: Mapping[str, Any],
    packet: Mapping[str, Any],
    resume_document_id: str | None,
    cover_letter_document_id: str | None,
    actor: str,
    correlation_id: str,
) -> dict[str, Any]:
    prep_id = uuid.uuid4().hex
    status = (
        "needs_review"
        if packet["review_required"] or not packet["summary"]["documents_ready"]
        else "approved"
    )
    connection.execute(
        """INSERT INTO application_preparations (prep_id, job_id, status, resume_document_id, cover_letter_document_id, fields,
            questions_source, review_required, review_notes, summary, created_at, updated_at, correlation_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)""",
        (
            prep_id,
            job["job_id"],
            status,
            resume_document_id,
            cover_letter_document_id,
            json.dumps(packet["fields"], ensure_ascii=False),
            packet["questions_source"],
            int(packet["review_required"]),
            json.dumps(packet["summary"], ensure_ascii=False),
            _now(),
            _now(),
            correlation_id,
        ),
    )
    record_audit(
        connection,
        actor=actor,
        action="application.prepare",
        target_type="job",
        target_id=job["job_id"],
        detail={"prep_id": prep_id, "status": status, "summary": packet["summary"]},
        correlation_id=correlation_id,
    )
    return get_preparation(connection, prep_id) or {}


def get_preparation(
    connection: sqlite3.Connection, prep_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM application_preparations WHERE prep_id = ?", (prep_id,)
    ).fetchone()
    return _decode_prep(row) if row else None


def latest_preparation(
    connection: sqlite3.Connection, job_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM application_preparations WHERE job_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return _decode_prep(row) if row else None


def _decode_prep(row: sqlite3.Row) -> dict[str, Any]:
    prep = dict(row)
    for key, default in (("fields", "[]"), ("summary", "{}")):
        try:
            prep[key] = json.loads(prep.get(key) or default)
        except json.JSONDecodeError:
            prep[key] = json.loads(default)
    prep["review_required"] = bool(prep["review_required"])
    return prep


def update_preparation_fields(
    connection: sqlite3.Connection,
    prep_id: str,
    *,
    answers: Mapping[str, Any],
    notes: str,
    actor: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Apply user-reviewed answers. Sensitive fields become confirmed by this explicit action."""
    prep = get_preparation(connection, prep_id)
    if prep is None:
        raise LookupError("Application preparation not found.")
    if prep["status"] == "submitted":
        raise ValueError(
            "This application was already submitted; prepare a new packet instead."
        )
    from aira.field_mapping import validate_value

    fields = prep["fields"]
    changed = 0
    for field in fields:
        key = field.get("provider_field_id") or field["label"]
        if key not in answers and field["label"] not in answers:
            continue
        raw = answers.get(key, answers.get(field["label"]))
        value = str(raw if raw is not None else "").strip()[:4000]
        if field["field_type"] == "file":
            continue
        error = (
            validate_value(field["canonical_field"], value)
            if field.get("canonical_field") and value
            else None
        )
        field["value"] = value or None
        field["value_source"] = "user_review" if value else None
        if error:
            field["status"] = "invalid"
            field["message"] = error
        elif value:
            field["status"] = "prefilled"
            field["message"] = "Confirmed by you during review."
        else:
            field["status"] = "missing" if field.get("canonical_field") else "unknown"
            field["message"] = "No answer provided."
        changed += 1
    required_missing = [
        f["label"]
        for f in fields
        if f["required"]
        and f["status"] in {"missing", "unknown", "invalid"}
        and f["field_type"] != "file"
    ]
    pending = [
        f["label"] for f in fields if f["status"] in {"needs_confirmation", "invalid"}
    ]
    summary = dict(prep["summary"])
    summary.update(
        {
            "prefilled": sum(1 for f in fields if f["status"] == "prefilled"),
            "needs_confirmation": sum(
                1 for f in fields if f["status"] == "needs_confirmation"
            ),
            "missing": sum(1 for f in fields if f["status"] == "missing"),
            "unknown": sum(1 for f in fields if f["status"] == "unknown"),
            "invalid": sum(1 for f in fields if f["status"] == "invalid"),
            "required_missing": required_missing,
            "sensitive_pending": [
                f["label"] for f in fields if f["status"] == "needs_confirmation"
            ],
            "unknown_questions": [
                f["label"] for f in fields if f["status"] == "unknown"
            ],
        }
    )
    review_required = bool(pending or required_missing)
    status = (
        "needs_review"
        if review_required or not summary.get("documents_ready")
        else "approved"
    )
    connection.execute(
        """UPDATE application_preparations SET fields = ?, summary = ?, review_required = ?, review_notes = ?, status = ?, updated_at = ?
        WHERE prep_id = ?""",
        (
            json.dumps(fields, ensure_ascii=False),
            json.dumps(summary, ensure_ascii=False),
            int(review_required),
            notes.strip()[:4000],
            status,
            _now(),
            prep_id,
        ),
    )
    record_audit(
        connection,
        actor=actor,
        action="application.review",
        target_type="job",
        target_id=prep["job_id"],
        detail={"prep_id": prep_id, "fields_changed": changed, "status": status},
        correlation_id=correlation_id,
    )
    return get_preparation(connection, prep_id) or {}


def approve_preparation(
    connection: sqlite3.Connection, prep_id: str, *, actor: str, correlation_id: str
) -> dict[str, Any]:
    prep = get_preparation(connection, prep_id)
    if prep is None:
        raise LookupError("Application preparation not found.")
    if prep["summary"].get("required_missing"):
        raise ValueError(
            "Answer the required questions before approving: "
            + ", ".join(prep["summary"]["required_missing"][:5])
            + "."
        )
    if prep["summary"].get("sensitive_pending"):
        raise ValueError(
            "Confirm the sensitive answers before approving: "
            + ", ".join(prep["summary"]["sensitive_pending"][:5])
            + "."
        )
    if not prep["summary"].get("documents_ready"):
        raise ValueError(
            "Generate and approve the tailored resume and cover letter first."
        )
    connection.execute(
        "UPDATE application_preparations SET status = 'approved', review_required = 0, updated_at = ? WHERE prep_id = ?",
        (_now(), prep_id),
    )
    record_audit(
        connection,
        actor=actor,
        action="application.approve",
        target_type="job",
        target_id=prep["job_id"],
        detail={"prep_id": prep_id},
        correlation_id=correlation_id,
    )
    return get_preparation(connection, prep_id) or {}


def record_submission(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    prep_id: str | None,
    confirmation_text: str,
    confirmation_number: str,
    evidence_document_id: str | None,
    method: str,
    actor: str,
    correlation_id: str,
    idempotency_key: str | None = None,
    submitted_at: str | None = None,
) -> dict[str, Any]:
    if method not in SUBMISSION_METHODS:
        raise ValueError("Submission method must be manual, assisted, auto or outside.")
    existing = connection.execute(
        "SELECT * FROM application_submissions WHERE job_id = ?", (job_id,)
    ).fetchone()
    key = (idempotency_key or f"job:{job_id}").strip()[:120]
    if existing:
        return dict(existing) | {"duplicate": True}
    submitted_iso = (
        parse_submitted_at(submitted_at).isoformat(timespec="minutes")
        if submitted_at
        else _now()
    )
    submission_id = uuid.uuid4().hex
    try:
        connection.execute(
            """INSERT INTO application_submissions (submission_id, job_id, prep_id, submitted_at, method, confirmation_text, confirmation_number,
                evidence_document_id, actor, correlation_id, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                submission_id,
                job_id,
                prep_id,
                submitted_iso,
                method,
                confirmation_text.strip()[:5000],
                confirmation_number.strip()[:120],
                evidence_document_id,
                actor,
                correlation_id,
                key,
            ),
        )
    except sqlite3.IntegrityError:
        row = connection.execute(
            "SELECT * FROM application_submissions WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return dict(row) | {"duplicate": True}
    if prep_id:
        connection.execute(
            "UPDATE application_preparations SET status = 'submitted', updated_at = ? WHERE prep_id = ?",
            (_now(), prep_id),
        )
    record_audit(
        connection,
        actor=actor,
        action="application.submit",
        target_type="job",
        target_id=job_id,
        detail={
            "submission_id": submission_id,
            "method": method,
            "has_evidence": bool(evidence_document_id),
            "confirmation_number": confirmation_number[:120],
        },
        correlation_id=correlation_id,
    )
    row = connection.execute(
        "SELECT * FROM application_submissions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    result = dict(row) | {"duplicate": False}
    notify_submission(connection, result)
    return result


def parse_submitted_at(value: str, *, now: datetime | None = None) -> datetime:
    """When an application was sent, in UTC; never in the future (a typo in the year would otherwise take a
    place in the daily limit every day until then).

    A time without a zone is read as UTC. A time up to 14 hours ahead (the widest time-zone offset: "today at
    noon" typed in the morning, or a local time read as UTC) is taken as now; anything later is refused.
    """
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Submission time must be an ISO 8601 date-time.") from error
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    when = (
        when.replace(tzinfo=timezone.utc)
        if when.tzinfo is None
        else when.astimezone(timezone.utc)
    )
    if when > current + timedelta(hours=14):
        raise ValueError(
            "The day you applied cannot be in the future. Check the date and the year."
        )
    if when.year < EARLIEST_SUBMISSION_YEAR:
        raise ValueError("Check the year of the day you applied.")
    return min(when, current)


def get_submission(
    connection: sqlite3.Connection, job_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM application_submissions WHERE job_id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def submissions_today(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    """Applications sent through Aira today (UTC): what the daily limit paces.

    Applications the person recorded as sent another way (``method = 'outside'``) are not counted, and only
    today's rows count (a row dated later never takes a place every day until then).
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return connection.execute(
        """SELECT COUNT(*) FROM application_submissions
        WHERE submitted_at >= ? AND submitted_at < ? AND method != 'outside'""",
        (
            start.isoformat(timespec="minutes"),
            (start + timedelta(days=1)).isoformat(timespec="minutes"),
        ),
    ).fetchone()[0]


def queue(
    connection: sqlite3.Connection, *, daily_limit: int, now: datetime | None = None
) -> dict[str, Any]:
    """Human-review and ready-to-submit queues plus the daily budget."""
    rows = connection.execute(
        """SELECT jobs.job_id, jobs.title, jobs.company, jobs.stage, jobs.match_score, jobs.match_classification, jobs.application_url, jobs.job_url,
            jobs.resume_draft != '' AND jobs.cover_letter != '' AS documents_ready, jobs.updated_at,
            (SELECT status FROM application_preparations p WHERE p.job_id = jobs.job_id ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1) AS prep_status,
            (SELECT prep_id FROM application_preparations p WHERE p.job_id = jobs.job_id ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1) AS prep_id
        FROM jobs WHERE jobs.archived_at IS NULL AND jobs.stage IN ('needs_review', 'ready_to_apply', 'applying')
        ORDER BY COALESCE(jobs.match_score, -1) DESC, jobs.updated_at DESC"""
    ).fetchall()
    review, ready, in_progress = [], [], []
    for row in rows:
        item = dict(row)
        item["documents_ready"] = bool(item["documents_ready"])
        if item["stage"] == "applying":
            in_progress.append(item)
        elif (
            item["stage"] == "ready_to_apply" and item.get("prep_status") == "approved"
        ):
            ready.append(item)
        else:
            review.append(item)
    used = submissions_today(connection, now=now)
    return {
        "daily_limit": daily_limit,
        "submitted_today": used,
        "remaining_today": max(0, daily_limit - used),
        "ready": ready,
        "needs_review": review,
        "in_progress": in_progress,
    }


def check_daily_limit(
    connection: sqlite3.Connection, *, daily_limit: int, now: datetime | None = None
) -> None:
    if daily_limit and submissions_today(connection, now=now) >= daily_limit:
        raise ValueError(
            f"Daily application limit of {daily_limit} reached. Raise it in Settings or continue tomorrow."
        )


def auto_submit_policy(settings: Mapping[str, str]) -> dict[str, Any]:
    """Automatic applications run from search plans in Careers > Auto-apply.

    The legacy ``auto_submit_enabled`` switch stays off: what is sent is decided
    per plan (sources, minimum score, mode) under the global pause and daily limit.
    """
    return {
        "enabled": settings.get("auto_apply_paused") != "1",
        "approved_sources": [
            "greenhouse",
            "lever",
            "ashby",
            "smartrecruiters",
            "generic",
            "linkedin",
        ],
        "approved_answer_profiles": ["confirmed_standard_answers"],
        "available": True,
        "paused": settings.get("auto_apply_paused") == "1",
        "reason": "Search plans in Auto-apply fill and submit forms for jobs that clear the plan's bar. Questions without a confirmed answer are held for you; nothing is guessed.",
    }


def preparation_as_dict(value: Any) -> dict[str, Any]:
    return asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)


def stale_preparations(
    connection: sqlite3.Connection, *, older_than_days: int = 30
) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(
        timespec="seconds"
    )
    return connection.execute(
        "SELECT COUNT(*) FROM application_preparations WHERE status IN ('draft', 'needs_review') AND updated_at < ?",
        (cutoff,),
    ).fetchone()[0]
