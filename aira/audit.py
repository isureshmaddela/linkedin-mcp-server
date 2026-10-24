"""Append-only audit log, notifications, and tasks for a user's database."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

TASK_KINDS = (
    "follow_up",
    "interview_prep",
    "assessment_deadline",
    "reminder",
    "review",
)
TASK_STATUSES = ("open", "done", "dismissed")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_audit(
    connection: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: Mapping[str, Any] | None = None,
    source: str = "user",
    correlation_id: str = "",
) -> str:
    audit_id = uuid.uuid4().hex
    connection.execute(
        """INSERT INTO audit_logs (audit_id, at, actor, source, action, target_type, target_id, detail, correlation_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id,
            int(time.time()),
            actor[:120],
            source[:40],
            action[:80],
            target_type[:40],
            str(target_id)[:256],
            json.dumps(dict(detail or {}), ensure_ascii=False)[:4000],
            correlation_id[:64],
        ),
    )
    return audit_id


def list_audit(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
    target_type: str | None = None,
    target_id: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if target_type:
        clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        clauses.append("target_id = ?")
        params.append(target_id)
    if action:
        clauses.append("action = ?")
        params.append(action)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM audit_logs {where} ORDER BY at DESC, rowid DESC LIMIT ? OFFSET ?",
        (*params, max(1, min(limit, 500)), max(0, offset)),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item["detail"] or "{}")
        except json.JSONDecodeError:
            item["detail"] = {}
        result.append(item)
    return result


def notify(
    connection: sqlite3.Connection,
    *,
    title: str,
    body: str = "",
    level: str = "info",
    link: str = "",
) -> dict[str, Any]:
    if level not in {"info", "success", "warning", "error"}:
        level = "info"
    notification_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO notifications (notification_id, at, level, title, body, read, link) VALUES (?, ?, ?, ?, ?, 0, ?)",
        (
            notification_id,
            int(time.time()),
            level,
            title[:200],
            body[:1000],
            link[:300],
        ),
    )
    connection.execute(
        "DELETE FROM notifications WHERE notification_id NOT IN (SELECT notification_id FROM notifications ORDER BY at DESC LIMIT 200)"
    )
    return {
        "notification_id": notification_id,
        "at": int(time.time()),
        "level": level,
        "title": title,
        "body": body,
        "read": 0,
        "link": link,
    }


def list_notifications(
    connection: sqlite3.Connection, *, unread_only: bool = False, limit: int = 50
) -> list[dict[str, Any]]:
    where = "WHERE read = 0" if unread_only else ""
    rows = connection.execute(
        f"SELECT * FROM notifications {where} ORDER BY at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def mark_notifications_read(
    connection: sqlite3.Connection, ids: list[str] | None = None
) -> int:
    if ids:
        placeholders = ",".join("?" for _ in ids)
        return connection.execute(
            f"UPDATE notifications SET read = 1 WHERE notification_id IN ({placeholders})",
            ids,
        ).rowcount
    return connection.execute(
        "UPDATE notifications SET read = 1 WHERE read = 0"
    ).rowcount


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _validate_due(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Due date must be an ISO 8601 date or date-time.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="minutes")


def create_task(
    connection: sqlite3.Connection,
    *,
    title: str,
    kind: str = "reminder",
    due_at: Any = None,
    job_id: str | None = None,
    interview_id: str | None = None,
    recruiter_id: str | None = None,
    source: str = "user",
    dedupe: bool = True,
) -> dict[str, Any]:
    title = title.strip()
    if not title or len(title) > 200:
        raise ValueError("Task title must be between 1 and 200 characters.")
    if kind not in TASK_KINDS:
        raise ValueError(f"Task kind must be one of: {', '.join(TASK_KINDS)}.")
    due = _validate_due(due_at)
    if dedupe:
        existing = connection.execute(
            """SELECT task_id FROM tasks WHERE status = 'open' AND title = ? AND kind = ?
            AND COALESCE(job_id, '') = COALESCE(?, '') AND COALESCE(interview_id, '') = COALESCE(?, '')
            AND COALESCE(recruiter_id, '') = COALESCE(?, '')""",
            (title, kind, job_id, interview_id, recruiter_id),
        ).fetchone()
        if existing:
            connection.execute(
                "UPDATE tasks SET due_at = ? WHERE task_id = ?",
                (due, existing["task_id"]),
            )
            return get_task(connection, existing["task_id"]) or {}
    task_id = uuid.uuid4().hex
    connection.execute(
        """INSERT INTO tasks (task_id, kind, title, due_at, status, job_id, interview_id, recruiter_id, source, created_at)
        VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)""",
        (
            task_id,
            kind,
            title,
            due,
            job_id,
            interview_id,
            recruiter_id,
            source,
            now_iso(),
        ),
    )
    return get_task(connection, task_id) or {}


def get_task(connection: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row else None


def list_tasks(
    connection: sqlite3.Connection,
    *,
    status: str = "open",
    limit: int = 100,
    job_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status and status != "all":
        clauses.append("tasks.status = ?")
        params.append(status)
    if job_id:
        clauses.append("tasks.job_id = ?")
        params.append(job_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""SELECT tasks.*, jobs.title AS job_title, jobs.company AS job_company
        FROM tasks LEFT JOIN jobs ON jobs.job_id = tasks.job_id {where}
        ORDER BY CASE WHEN tasks.due_at IS NULL THEN 1 ELSE 0 END, tasks.due_at, tasks.created_at LIMIT ?""",
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def update_task(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    status: str | None = None,
    due_at: Any = None,
    title: str | None = None,
) -> dict[str, Any]:
    task = get_task(connection, task_id)
    if task is None:
        raise LookupError("Task not found.")
    if status is not None:
        if status not in TASK_STATUSES:
            raise ValueError("Task status must be open, done, or dismissed.")
        connection.execute(
            "UPDATE tasks SET status = ?, completed_at = ? WHERE task_id = ?",
            (status, now_iso() if status != "open" else None, task_id),
        )
    if due_at is not None:
        connection.execute(
            "UPDATE tasks SET due_at = ? WHERE task_id = ?",
            (_validate_due(due_at), task_id),
        )
    if title is not None:
        title = title.strip()
        if not title or len(title) > 200:
            raise ValueError("Task title must be between 1 and 200 characters.")
        connection.execute(
            "UPDATE tasks SET title = ? WHERE task_id = ?", (title, task_id)
        )
    return get_task(connection, task_id) or {}


def delete_task(connection: sqlite3.Connection, task_id: str) -> bool:
    return (
        connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,)).rowcount
        > 0
    )
