"""Interview tracking with standards-based calendar synchronization.

Calendar sync is file/link based (iCalendar downloads and provider deep links)
because no OAuth credentials are available; UID and SEQUENCE are persisted so
reschedules and cancellations update the same calendar entry.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.audit import create_task

INTERVIEW_TYPES = (
    "recruiter_screen",
    "technical_interview",
    "manager_interview",
    "panel_interview",
    "final_interview",
    "assessment",
    "other",
)
INTERVIEW_STATUSES = ("scheduled", "completed", "cancelled", "rescheduled")
INTERVIEW_STAGE_FOR_TYPE = {
    "recruiter_screen": "recruiter_screen",
    "technical_interview": "technical_interview",
    "manager_interview": "manager_interview",
    "panel_interview": "panel_interview",
    "final_interview": "final_interview",
    "assessment": "assessment",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_when(value: Any, timezone_name: str, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Enter the interview {label}.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"The interview {label} must be an ISO 8601 date-time."
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def _validate(
    payload: Mapping[str, Any], existing: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    base = dict(existing or {})
    merged = {
        **base,
        **{key: value for key, value in payload.items() if value is not None},
    }
    timezone_name = str(merged.get("timezone") or "UTC").strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(
            "Enter a valid IANA time zone such as America/New_York."
        ) from error
    start = _parse_when(merged.get("start_at"), timezone_name, "start time")
    end_raw = merged.get("end_at")
    end = (
        _parse_when(end_raw, timezone_name, "end time")
        if end_raw
        else start + timedelta(minutes=45)
    )
    if end <= start:
        raise ValueError("The interview end time must be after the start time.")
    interview_type = str(merged.get("interview_type") or "recruiter_screen")
    if interview_type not in INTERVIEW_TYPES:
        raise ValueError(
            f"Interview type must be one of: {', '.join(INTERVIEW_TYPES)}."
        )
    status = str(merged.get("status") or "scheduled")
    if status not in INTERVIEW_STATUSES:
        raise ValueError(
            "Interview status must be scheduled, completed, cancelled, or rescheduled."
        )
    meeting_link = str(merged.get("meeting_link") or "").strip()[:500]
    if meeting_link and not re.match(r"^https://", meeting_link):
        raise ValueError("Meeting links must start with https://.")
    action_items = merged.get("action_items") or []
    if isinstance(action_items, str):
        action_items = [
            line.strip() for line in action_items.splitlines() if line.strip()
        ]
    if not isinstance(action_items, list):
        raise ValueError("Action items must be a list of short lines.")
    action_items = [
        str(item).strip()[:300] for item in action_items if str(item).strip()
    ][:50]
    follow_up = merged.get("follow_up_at")
    follow_up_at = None
    if follow_up:
        follow_up_at = _parse_when(
            follow_up, timezone_name, "follow-up date"
        ).isoformat(timespec="minutes")
    participants = merged.get("participants") or []
    if not isinstance(participants, list):
        raise ValueError("Participants must be a list.")
    clean_participants = []
    for item in participants[:20]:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()[:160]
        if name:
            clean_participants.append(
                {
                    "name": name,
                    "role": str(item.get("role") or "").strip()[:120],
                    "email": str(item.get("email") or "").strip()[:254],
                }
            )
    return {
        "job_id": str(merged.get("job_id") or "").strip() or None,
        "company": str(merged.get("company") or "").strip()[:160],
        "role": str(merged.get("role") or "").strip()[:160],
        "interview_type": interview_type,
        "start_at": start.isoformat(timespec="minutes"),
        "end_at": end.isoformat(timespec="minutes"),
        "timezone": timezone_name,
        "recruiter_id": str(merged.get("recruiter_id") or "").strip() or None,
        "hiring_manager": str(merged.get("hiring_manager") or "").strip()[:160],
        "meeting_link": meeting_link,
        "location": str(merged.get("location") or "").strip()[:300],
        "notes": str(merged.get("notes") or "").strip()[:10000],
        "preparation": str(merged.get("preparation") or "").strip()[:10000],
        "feedback": str(merged.get("feedback") or "").strip()[:10000],
        "action_items": action_items,
        "follow_up_at": follow_up_at,
        "status": status,
        "participants": clean_participants,
    }


def _calendar_event(interview: Mapping[str, Any]) -> Any:
    from aira.calendar_sync import CalendarEvent

    start = datetime.fromisoformat(interview["start_at"])
    end = datetime.fromisoformat(interview["end_at"])
    title_parts = [interview.get("interview_type", "").replace("_", " ").title()]
    if interview.get("company"):
        title_parts.append(interview["company"])
    if interview.get("role"):
        title_parts.append(interview["role"])
    description_lines = []
    if interview.get("hiring_manager"):
        description_lines.append(f"Hiring manager: {interview['hiring_manager']}")
    for participant in interview.get("participants", []):
        description_lines.append(
            f"Participant: {participant['name']}{' - ' + participant['role'] if participant.get('role') else ''}"
        )
    if interview.get("preparation"):
        description_lines.append("Preparation:\n" + interview["preparation"])
    attendees = tuple(
        p["email"] for p in interview.get("participants", []) if p.get("email")
    )
    status = "cancelled" if interview.get("status") == "cancelled" else "confirmed"
    return CalendarEvent(
        uid=interview["calendar_uid"],
        title=" · ".join(title_parts),
        start=start,
        end=end,
        timezone=interview["timezone"],
        description="\n".join(description_lines),
        location=interview.get("location", ""),
        url=interview.get("meeting_link", ""),
        attendees=attendees,
        sequence=int(interview.get("calendar_sequence", 0)),
        status=status,
    )


def calendar_payload(interview: Mapping[str, Any]) -> dict[str, Any]:
    from aira.calendar_sync import (
        build_ics,
        google_calendar_link,
        office365_calendar_link,
        outlook_calendar_link,
    )

    event = _calendar_event(interview)
    return {
        "ics": build_ics(event),
        "google": google_calendar_link(event),
        "outlook": outlook_calendar_link(event),
        "office365": office365_calendar_link(event),
        "uid": event.uid,
        "sequence": event.sequence,
    }


def _fingerprint(interview: Mapping[str, Any]) -> str:
    from aira.calendar_sync import event_fingerprint

    return event_fingerprint(_calendar_event(interview))


def _write_participants(
    connection: sqlite3.Connection,
    interview_id: str,
    participants: list[dict[str, str]],
) -> None:
    connection.execute(
        "DELETE FROM interview_participants WHERE interview_id = ?", (interview_id,)
    )
    for participant in participants:
        connection.execute(
            "INSERT INTO interview_participants (participant_id, interview_id, name, role, email) VALUES (?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                interview_id,
                participant["name"],
                participant["role"],
                participant["email"],
            ),
        )


def create_interview(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    source: str = "user",
    source_message_id: str | None = None,
) -> dict[str, Any]:
    values = _validate(payload)
    if values["job_id"]:
        job = connection.execute(
            "SELECT title, company FROM jobs WHERE job_id = ?", (values["job_id"],)
        ).fetchone()
        if job is None:
            raise ValueError("Linked job not found.")
        values["company"] = values["company"] or job["company"]
        values["role"] = values["role"] or job["title"]
    duplicate = connection.execute(
        """SELECT interview_id FROM interviews WHERE status != 'cancelled' AND start_at = ? AND interview_type = ?
        AND lower(company) = lower(?) AND COALESCE(job_id, '') = COALESCE(?, '')""",
        (
            values["start_at"],
            values["interview_type"],
            values["company"],
            values["job_id"],
        ),
    ).fetchone()
    if duplicate:
        raise ValueError(
            "An interview of this type at the same time already exists for this company."
        )
    interview_id = uuid.uuid4().hex
    values["calendar_uid"] = f"{interview_id}@aira"
    values["calendar_sequence"] = 0
    values["calendar_fingerprint"] = _fingerprint(values)
    now = _now().isoformat(timespec="seconds")
    connection.execute(
        """INSERT INTO interviews (interview_id, job_id, company, role, interview_type, start_at, end_at, timezone, recruiter_id,
            hiring_manager, meeting_link, location, notes, preparation, feedback, action_items, follow_up_at, status,
            calendar_uid, calendar_sequence, calendar_fingerprint, source, source_message_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            interview_id,
            values["job_id"],
            values["company"],
            values["role"],
            values["interview_type"],
            values["start_at"],
            values["end_at"],
            values["timezone"],
            values["recruiter_id"],
            values["hiring_manager"],
            values["meeting_link"],
            values["location"],
            values["notes"],
            values["preparation"],
            values["feedback"],
            json.dumps(values["action_items"]),
            values["follow_up_at"],
            values["status"],
            values["calendar_uid"],
            0,
            values["calendar_fingerprint"],
            source,
            source_message_id,
            now,
            now,
        ),
    )
    _write_participants(connection, interview_id, values["participants"])
    _sync_reminders(connection, interview_id, values)
    return get_interview(connection, interview_id) or {}


def update_interview(
    connection: sqlite3.Connection, interview_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    existing = get_interview(connection, interview_id)
    if existing is None:
        raise LookupError("Interview not found.")
    values = _validate(payload, existing)
    time_changed = (
        values["start_at"] != existing["start_at"]
        or values["end_at"] != existing["end_at"]
    )
    status_changed = values["status"] != existing["status"]
    if (
        time_changed
        and values["status"] == "scheduled"
        and existing["status"] == "scheduled"
    ):
        values["status"] = "rescheduled"
    sequence = int(existing["calendar_sequence"])
    if (
        time_changed
        or status_changed
        or values["location"] != existing["location"]
        or values["meeting_link"] != existing["meeting_link"]
    ):
        sequence += 1
    values["calendar_uid"] = existing["calendar_uid"]
    values["calendar_sequence"] = sequence
    values["calendar_fingerprint"] = _fingerprint(values)
    connection.execute(
        """UPDATE interviews SET job_id = ?, company = ?, role = ?, interview_type = ?, start_at = ?, end_at = ?, timezone = ?, recruiter_id = ?,
            hiring_manager = ?, meeting_link = ?, location = ?, notes = ?, preparation = ?, feedback = ?, action_items = ?, follow_up_at = ?, status = ?,
            calendar_sequence = ?, calendar_fingerprint = ?, updated_at = ? WHERE interview_id = ?""",
        (
            values["job_id"],
            values["company"],
            values["role"],
            values["interview_type"],
            values["start_at"],
            values["end_at"],
            values["timezone"],
            values["recruiter_id"],
            values["hiring_manager"],
            values["meeting_link"],
            values["location"],
            values["notes"],
            values["preparation"],
            values["feedback"],
            json.dumps(values["action_items"]),
            values["follow_up_at"],
            values["status"],
            sequence,
            values["calendar_fingerprint"],
            _now().isoformat(timespec="seconds"),
            interview_id,
        ),
    )
    _write_participants(connection, interview_id, values["participants"])
    if values["status"] == "cancelled":
        connection.execute(
            "UPDATE tasks SET status = 'dismissed', completed_at = ? WHERE interview_id = ? AND status = 'open'",
            (_now().isoformat(timespec="seconds"), interview_id),
        )
    else:
        _sync_reminders(connection, interview_id, values)
    return get_interview(connection, interview_id) or {}


def delete_interview(connection: sqlite3.Connection, interview_id: str) -> bool:
    connection.execute(
        "DELETE FROM interview_participants WHERE interview_id = ?", (interview_id,)
    )
    connection.execute(
        "UPDATE tasks SET status = 'dismissed' WHERE interview_id = ? AND status = 'open'",
        (interview_id,),
    )
    return (
        connection.execute(
            "DELETE FROM interviews WHERE interview_id = ?", (interview_id,)
        ).rowcount
        > 0
    )


def _sync_reminders(
    connection: sqlite3.Connection, interview_id: str, values: Mapping[str, Any]
) -> None:
    start = datetime.fromisoformat(values["start_at"])
    connection.execute(
        "UPDATE tasks SET status = 'dismissed' WHERE interview_id = ? AND status = 'open' AND kind IN ('interview_prep', 'follow_up')",
        (interview_id,),
    )
    label = f"{values['company'] or 'Interview'} {values['interview_type'].replace('_', ' ')}"
    if start > _now():
        create_task(
            connection,
            title=f"Prepare for {label}",
            kind="interview_prep",
            due_at=(start - timedelta(days=1)).isoformat(timespec="minutes"),
            job_id=values["job_id"],
            interview_id=interview_id,
            recruiter_id=values["recruiter_id"],
            source="interview",
            dedupe=False,
        )
    follow_up_at = values["follow_up_at"] or (
        datetime.fromisoformat(values["end_at"]) + timedelta(days=1)
    ).isoformat(timespec="minutes")
    create_task(
        connection,
        title=f"Send follow-up after {label}",
        kind="follow_up",
        due_at=follow_up_at,
        job_id=values["job_id"],
        interview_id=interview_id,
        recruiter_id=values["recruiter_id"],
        source="interview",
        dedupe=False,
    )


def get_interview(
    connection: sqlite3.Connection, interview_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT interviews.*, jobs.title AS job_title, jobs.stage AS job_stage, recruiters.name AS recruiter_name
        FROM interviews LEFT JOIN jobs ON jobs.job_id = interviews.job_id
        LEFT JOIN recruiters ON recruiters.recruiter_id = interviews.recruiter_id WHERE interview_id = ?""",
        (interview_id,),
    ).fetchone()
    if row is None:
        return None
    interview = dict(row)
    try:
        interview["action_items"] = json.loads(interview.get("action_items") or "[]")
    except json.JSONDecodeError:
        interview["action_items"] = []
    interview["participants"] = [
        dict(item)
        for item in connection.execute(
            "SELECT participant_id, name, role, email FROM interview_participants WHERE interview_id = ? ORDER BY rowid",
            (interview_id,),
        ).fetchall()
    ]
    return interview


def list_interviews(
    connection: sqlite3.Connection,
    *,
    scope: str = "upcoming",
    job_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    now = _now().isoformat(timespec="minutes")
    if scope == "upcoming":
        clauses.append("interviews.end_at >= ? AND interviews.status != 'cancelled'")
        params.append(now)
    elif scope == "past":
        clauses.append(
            "(interviews.end_at < ? OR interviews.status IN ('completed', 'cancelled'))"
        )
        params.append(now)
    if job_id:
        clauses.append("interviews.job_id = ?")
        params.append(job_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "ASC" if scope == "upcoming" else "DESC"
    rows = connection.execute(
        f"""SELECT interviews.*, jobs.title AS job_title, recruiters.name AS recruiter_name
        FROM interviews LEFT JOIN jobs ON jobs.job_id = interviews.job_id
        LEFT JOIN recruiters ON recruiters.recruiter_id = interviews.recruiter_id {where}
        ORDER BY interviews.start_at {order} LIMIT ?""",
        (*params, limit),
    ).fetchall()
    result = []
    for row in rows:
        interview = dict(row)
        try:
            interview["action_items"] = json.loads(
                interview.get("action_items") or "[]"
            )
        except json.JSONDecodeError:
            interview["action_items"] = []
        result.append(interview)
    return result
