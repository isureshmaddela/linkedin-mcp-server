"""Recruiter relationship management: contacts, interactions, follow-ups.

Deduplication is conservative: two contacts merge only on an exact email,
exact LinkedIn profile URL, or exact (name, company) pair. Similar names alone
never merge.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from aira.candidate import EMAIL_RE, PHONE_RE

INTERACTION_KINDS = ("email", "message", "call", "meeting", "note", "follow_up")
DIRECTIONS = ("inbound", "outbound")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_linkedin_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "linkedin.com",
        "www.linkedin.com",
    }:
        raise ValueError("Enter a valid LinkedIn profile URL.")
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if not path.startswith("/in/"):
        raise ValueError("Enter a LinkedIn profile URL that starts with /in/.")
    return f"https://www.linkedin.com{path}/"


def _validate(payload: Mapping[str, Any]) -> dict[str, str]:
    name = _clean(payload.get("name"), 160)
    if not name:
        raise ValueError("Enter the recruiter's name.")
    email = _clean(payload.get("email"), 254).lower()
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError("Enter a valid recruiter email address.")
    phone = _clean(payload.get("phone"), 40)
    if phone and not PHONE_RE.fullmatch(phone):
        raise ValueError("Enter a valid recruiter phone number.")
    return {
        "name": name,
        "title": _clean(payload.get("title"), 160),
        "company": _clean(payload.get("company"), 160),
        "email": email,
        "phone": phone,
        "linkedin_url": normalize_linkedin_url(str(payload.get("linkedin_url") or "")),
        "notes": str(payload.get("notes") or "").strip()[:5000],
        "consent_note": _clean(payload.get("consent_note"), 300),
    }


def find_duplicate(
    connection: sqlite3.Connection,
    values: Mapping[str, str],
    *,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    clauses, params = [], []
    if values.get("email"):
        clauses.append("email = ?")
        params.append(values["email"])
    if values.get("linkedin_url"):
        clauses.append("linkedin_url = ?")
        params.append(values["linkedin_url"])
    if values.get("name") and values.get("company"):
        clauses.append("(lower(name) = lower(?) AND lower(company) = lower(?))")
        params.extend([values["name"], values["company"]])
    if not clauses:
        return None
    sql = f"SELECT * FROM recruiters WHERE deleted_at IS NULL AND ({' OR '.join(clauses)})"
    if exclude_id:
        sql += " AND recruiter_id != ?"
        params.append(exclude_id)
    row = connection.execute(sql + " ORDER BY created_at LIMIT 1", params).fetchone()
    return dict(row) if row else None


def create_recruiter(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    source: str = "user",
    merge: bool = True,
) -> dict[str, Any]:
    values = _validate(payload)
    duplicate = find_duplicate(connection, values)
    if duplicate and merge:
        updates = {
            key: value
            for key, value in values.items()
            if value and not duplicate.get(key)
        }
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(
                f"UPDATE recruiters SET {assignments}, updated_at = ? WHERE recruiter_id = ?",
                (*updates.values(), _now(), duplicate["recruiter_id"]),
            )
        return get_recruiter(connection, duplicate["recruiter_id"]) or {}
    if duplicate:
        raise ValueError(
            "A recruiter with the same email, LinkedIn URL, or name and company already exists."
        )
    recruiter_id = uuid.uuid4().hex
    connection.execute(
        """INSERT INTO recruiters (recruiter_id, name, title, company, email, phone, linkedin_url, notes, source, consent_note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            recruiter_id,
            values["name"],
            values["title"],
            values["company"],
            values["email"],
            values["phone"],
            values["linkedin_url"],
            values["notes"],
            source[:40],
            values["consent_note"],
            _now(),
            _now(),
        ),
    )
    return get_recruiter(connection, recruiter_id) or {}


def update_recruiter(
    connection: sqlite3.Connection, recruiter_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    existing = get_recruiter(connection, recruiter_id)
    if existing is None:
        raise LookupError("Recruiter not found.")
    merged = {
        key: payload.get(key, existing.get(key, ""))
        for key in (
            "name",
            "title",
            "company",
            "email",
            "phone",
            "linkedin_url",
            "notes",
            "consent_note",
        )
    }
    values = _validate(merged)
    duplicate = find_duplicate(connection, values, exclude_id=recruiter_id)
    if duplicate:
        raise ValueError(
            "Another recruiter already uses that email, LinkedIn URL, or name and company."
        )
    connection.execute(
        """UPDATE recruiters SET name = ?, title = ?, company = ?, email = ?, phone = ?, linkedin_url = ?, notes = ?, consent_note = ?, updated_at = ?
        WHERE recruiter_id = ?""",
        (
            values["name"],
            values["title"],
            values["company"],
            values["email"],
            values["phone"],
            values["linkedin_url"],
            values["notes"],
            values["consent_note"],
            _now(),
            recruiter_id,
        ),
    )
    return get_recruiter(connection, recruiter_id) or {}


def delete_recruiter(connection: sqlite3.Connection, recruiter_id: str) -> bool:
    return (
        connection.execute(
            "UPDATE recruiters SET deleted_at = ? WHERE recruiter_id = ? AND deleted_at IS NULL",
            (_now(), recruiter_id),
        ).rowcount
        > 0
    )


def get_recruiter(
    connection: sqlite3.Connection, recruiter_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM recruiters WHERE recruiter_id = ? AND deleted_at IS NULL",
        (recruiter_id,),
    ).fetchone()
    if row is None:
        return None
    recruiter = dict(row)
    recruiter["interactions"] = list_interactions(connection, recruiter_id)
    recruiter["related_jobs"] = [
        dict(item)
        for item in connection.execute(
            """SELECT DISTINCT jobs.job_id, jobs.title, jobs.company, jobs.stage FROM jobs
            JOIN recruiter_interactions ON recruiter_interactions.job_id = jobs.job_id
            WHERE recruiter_interactions.recruiter_id = ?
            UNION
            SELECT DISTINCT jobs.job_id, jobs.title, jobs.company, jobs.stage FROM jobs
            JOIN interviews ON interviews.job_id = jobs.job_id WHERE interviews.recruiter_id = ?""",
            (recruiter_id, recruiter_id),
        ).fetchall()
    ]
    recruiter["interviews"] = [
        dict(item)
        for item in connection.execute(
            "SELECT interview_id, company, role, interview_type, start_at, status FROM interviews WHERE recruiter_id = ? ORDER BY start_at DESC LIMIT 20",
            (recruiter_id,),
        ).fetchall()
    ]
    return recruiter


def list_recruiters(
    connection: sqlite3.Connection,
    *,
    search: str = "",
    company: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses, params = ["recruiters.deleted_at IS NULL"], []
    if search:
        like = f"%{search.strip()}%"
        clauses.append(
            "(recruiters.name LIKE ? OR recruiters.company LIKE ? OR recruiters.email LIKE ? OR recruiters.title LIKE ?)"
        )
        params.extend([like, like, like, like])
    if company:
        clauses.append("lower(recruiters.company) = lower(?)")
        params.append(company)
    rows = connection.execute(
        f"""SELECT recruiters.*,
            (SELECT COUNT(*) FROM recruiter_interactions WHERE recruiter_interactions.recruiter_id = recruiters.recruiter_id) AS interaction_count,
            (SELECT MAX(occurred_at) FROM recruiter_interactions WHERE recruiter_interactions.recruiter_id = recruiters.recruiter_id) AS last_interaction_at,
            (SELECT MIN(follow_up_at) FROM recruiter_interactions WHERE recruiter_interactions.recruiter_id = recruiters.recruiter_id AND follow_up_at IS NOT NULL AND follow_up_at >= ?) AS next_follow_up_at,
            (SELECT MAX(last_contact_at) FROM recruiter_sources WHERE recruiter_sources.recruiter_id = recruiters.recruiter_id) AS last_found_contact_at,
            (SELECT group_concat(DISTINCT kind) FROM recruiter_sources WHERE recruiter_sources.recruiter_id = recruiters.recruiter_id) AS found_in
        FROM recruiters WHERE {" AND ".join(clauses)}
        ORDER BY COALESCE(last_interaction_at, recruiters.updated_at) DESC LIMIT ? OFFSET ?""",
        (_now(), *params, max(1, min(limit, 500)), max(0, offset)),
    ).fetchall()
    return [dict(row) for row in rows]


def add_interaction(
    connection: sqlite3.Connection, recruiter_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if get_recruiter(connection, recruiter_id) is None:
        raise LookupError("Recruiter not found.")
    kind = str(payload.get("kind") or "note")
    if kind not in INTERACTION_KINDS:
        raise ValueError(
            f"Interaction kind must be one of: {', '.join(INTERACTION_KINDS)}."
        )
    direction = str(payload.get("direction") or "outbound")
    if direction not in DIRECTIONS:
        raise ValueError("Direction must be inbound or outbound.")
    summary = str(payload.get("summary") or "").strip()[:4000]
    if not summary:
        raise ValueError("Enter a short summary of the interaction.")
    occurred_at = _parse_datetime(payload.get("occurred_at")) or _now()
    follow_up_at = _parse_datetime(payload.get("follow_up_at"))
    job_id = str(payload.get("job_id") or "").strip() or None
    if (
        job_id
        and connection.execute(
            "SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        is None
    ):
        raise ValueError("Linked job not found.")
    interaction_id = uuid.uuid4().hex
    connection.execute(
        """INSERT INTO recruiter_interactions (interaction_id, recruiter_id, job_id, kind, direction, summary, occurred_at, follow_up_at, source_message_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            interaction_id,
            recruiter_id,
            job_id,
            kind,
            direction,
            summary,
            occurred_at,
            follow_up_at,
            str(payload.get("source_message_id") or "") or None,
            _now(),
        ),
    )
    connection.execute(
        "UPDATE recruiters SET updated_at = ? WHERE recruiter_id = ?",
        (_now(), recruiter_id),
    )
    row = connection.execute(
        "SELECT * FROM recruiter_interactions WHERE interaction_id = ?",
        (interaction_id,),
    ).fetchone()
    return dict(row)


def list_interactions(
    connection: sqlite3.Connection, recruiter_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT recruiter_interactions.*, jobs.title AS job_title FROM recruiter_interactions
        LEFT JOIN jobs ON jobs.job_id = recruiter_interactions.job_id
        WHERE recruiter_id = ? ORDER BY occurred_at DESC LIMIT ?""",
        (recruiter_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_interaction(connection: sqlite3.Connection, interaction_id: str) -> bool:
    return (
        connection.execute(
            "DELETE FROM recruiter_interactions WHERE interaction_id = ?",
            (interaction_id,),
        ).rowcount
        > 0
    )


def upcoming_follow_ups(
    connection: sqlite3.Connection, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT recruiter_interactions.follow_up_at, recruiter_interactions.summary, recruiters.recruiter_id, recruiters.name, recruiters.company
        FROM recruiter_interactions JOIN recruiters ON recruiters.recruiter_id = recruiter_interactions.recruiter_id
        WHERE recruiters.deleted_at IS NULL AND recruiter_interactions.follow_up_at IS NOT NULL
        ORDER BY recruiter_interactions.follow_up_at LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Dates must use ISO 8601 format.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="minutes")
