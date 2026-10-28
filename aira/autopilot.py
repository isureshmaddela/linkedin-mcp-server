"""Auto-apply: scheduled job searches that apply to the matching jobs.

A *plan* names the positions to look for, where to look (LinkedIn, Google Jobs,
company boards), how good a match must be and how many applications to send per
run. On its schedule (or on "Run now") a *pass* searches every position on every
source, loads and scores what it found, keeps the jobs that clear the plan's bar,
and applies to the best of them through an *application run*.

An application run either submits (the form engine or LinkedIn Easy Apply
confirmed the application), is *held* because a required question has no
confirmed answer (the user answers once, optionally remembering the answer, and
the run resumes), is *ready* (review mode: the form was filled and waits for the
user's click), or ends as failed, unsupported or already applied with the reason.
Every job is tried once; nothing is retried behind the user's back.

The pipeline is written against :class:`Services`, a bundle of callables the
server provides (search, details and scoring, applying, recording a submission),
so the rules here are tested without a browser, LinkedIn or SerpApi.

Hard rules: never invent an answer (the engine and the Easy Apply tool leave a
required question empty and report it); sensitive questions are answered only
from confirmed standard answers; the daily application limit and the global
pause are checked before every application; a job with a submission or any
earlier run is never applied to again.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aira import answer_drafts
from aira.hosting import HOOKS

__all__ = [
    "MODES",
    "PLAN_SOURCES",
    "RUN_STATUSES",
    "Services",
    "candidate_reason",
    "create_plan",
    "ensure_schema",
    "execute_run",
    "run_plan",
    "title_matches",
]

MODES = ("apply", "review")
PLAN_SOURCES = ("linkedin", "aira_jobs", "google_jobs")
BOARD_SOURCES = ("greenhouse", "lever", "ashby", "smartrecruiters")
WORKPLACES = ("", "remote", "hybrid", "on_site")
JOB_TYPES = ("", "full_time", "part_time", "contract", "temporary", "internship")
RUN_STATUSES = (
    "queued",
    "running",
    "submitted",
    "ready",
    "needs_review",
    "failed",
    "unsupported",
    "already_applied",
    "skipped",
)
# Runs in these states still want something from the user.
OPEN_STATUSES = frozenset({"needs_review", "ready"})
# Runs in these states hold the job: a pass never starts another run for it.
ACTIVE_STATUSES = frozenset({"queued", "running"})
OUTCOME_TO_STATUS = {
    "submitted": "submitted",
    "filled": "ready",
    "needs_review": "needs_review",
    "failed": "failed",
    "unsupported": "unsupported",
    "already_applied": "already_applied",
    "external": "unsupported",
}
INTERVAL_CHOICES = (60, 120, 240, 480, 720, 1440)
MIN_INTERVAL = 60
MAX_INTERVAL = 7 * 24 * 60
MAX_PLANS = 10
MAX_POSITIONS = 5
MAX_BOARDS = 10
MAX_PER_RUN = 10
DETAIL_LOADS_PER_PASS = 20
STEPS_KEPT = 120
_SENIORITY_WORDS = frozenset(
    {
        "senior",
        "sr",
        "junior",
        "jr",
        "lead",
        "principal",
        "staff",
        "head",
        "chief",
        "associate",
        "entry",
        "level",
        "mid",
        "ii",
        "iii",
        "iv",
        "remote",
        "hybrid",
        "the",
        "and",
        "for",
        "with",
    }
)
_WORD_RE = re.compile(r"[a-z0-9+#.]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _loads(text: Any, default: Any) -> Any:
    if not isinstance(text, str) or not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the auto-apply tables. Additive and idempotent."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS autopilot_plans (
            plan_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            positions TEXT NOT NULL DEFAULT '[]', location TEXT NOT NULL DEFAULT '',
            workplace TEXT NOT NULL DEFAULT '', job_type TEXT NOT NULL DEFAULT '',
            sources TEXT NOT NULL DEFAULT '[]', boards TEXT NOT NULL DEFAULT '[]',
            exclude_keywords TEXT NOT NULL DEFAULT '[]', exclude_companies TEXT NOT NULL DEFAULT '[]',
            min_score INTEGER NOT NULL DEFAULT 70, max_per_run INTEGER NOT NULL DEFAULT 3,
            interval_minutes INTEGER NOT NULL DEFAULT 240, mode TEXT NOT NULL DEFAULT 'apply',
            match_title INTEGER NOT NULL DEFAULT 1, linkedin_easy_apply INTEGER NOT NULL DEFAULT 0,
            cover_letters INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at TEXT NOT NULL, last_run_at TEXT, last_status TEXT NOT NULL DEFAULT '',
            last_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS autopilot_passes (
            pass_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, trigger TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL DEFAULT 'running',
            counts TEXT NOT NULL DEFAULT '{}', summary TEXT NOT NULL DEFAULT '',
            log TEXT NOT NULL DEFAULT '[]'
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_autopilot_passes_plan ON autopilot_passes(plan_id, started_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS application_runs (
            run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, plan_id TEXT, pass_id TEXT,
            mode TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL,
            route TEXT NOT NULL DEFAULT '', ats TEXT NOT NULL DEFAULT '',
            application_url TEXT NOT NULL DEFAULT '', score REAL,
            questions TEXT NOT NULL DEFAULT '[]', unanswered TEXT NOT NULL DEFAULT '[]',
            answers TEXT NOT NULL DEFAULT '{}', steps TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '', confirmation_text TEXT NOT NULL DEFAULT '',
            confirmation_number TEXT NOT NULL DEFAULT '', evidence_document_id TEXT,
            screenshot_document_id TEXT, created_at TEXT NOT NULL, started_at TEXT,
            finished_at TEXT, correlation_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_runs_job ON application_runs(job_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_runs_status ON application_runs(status, created_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS custom_answers (
            label_key TEXT PRIMARY KEY, label TEXT NOT NULL, value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


def _text_list(raw: Any, *, limit: int, item_chars: int, label: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,\n]", raw)]
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{label} must be a list.")
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split())[:item_chars]
        if text and text.casefold() not in {value.casefold() for value in out}:
            out.append(text)
    if len(out) > limit:
        raise ValueError(f"Use at most {limit} {label.lower()}.")
    return out


def _int(value: Any, name: str, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number.") from None
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return number


def _flag(value: Any) -> int:
    return 1 if value in (True, 1, "1", "true", "on", "yes") else 0


def _boards(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Company boards must be a list.")
    boards: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("Each company board needs a source and an identifier.")
        source = str(item.get("source", "")).strip().lower()
        identifier = str(item.get("identifier", "")).strip()[:128]
        if source not in BOARD_SOURCES:
            raise ValueError(
                "Company boards can be Greenhouse, Lever, Ashby or SmartRecruiters."
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identifier):
            raise ValueError(
                f"Enter the {source} board identifier (letters, digits, dots, dashes)."
            )
        entry = {"source": source, "identifier": identifier}
        if entry not in boards:
            boards.append(entry)
    if len(boards) > MAX_BOARDS:
        raise ValueError(f"Use at most {MAX_BOARDS} company boards.")
    return boards


def validate_plan(
    payload: Mapping[str, Any], existing: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return clean plan columns from ``payload`` (merged over ``existing`` for updates)."""
    base = dict(existing or {})
    merged = {
        **base,
        **{key: value for key, value in payload.items() if value is not None},
    }
    name = " ".join(str(merged.get("name", "")).split())[:120]
    positions = _text_list(
        merged.get("positions"), limit=MAX_POSITIONS, item_chars=120, label="Positions"
    )
    if not positions:
        raise ValueError(
            "Add at least one position to look for, such as Solution Architect."
        )
    if not name:
        name = positions[0]
    sources = [
        source
        for source in _text_list(
            merged.get("sources"), limit=4, item_chars=40, label="Sources"
        )
        if source
    ]
    unknown = [source for source in sources if source not in PLAN_SOURCES]
    if unknown:
        raise ValueError(f"Unknown source: {unknown[0]}.")
    boards = _boards(merged.get("boards"))
    if not sources and not boards:
        raise ValueError(
            "Pick at least one place to search: LinkedIn, Google Jobs or a company board."
        )
    workplace = str(merged.get("workplace", "") or "")
    if workplace not in WORKPLACES:
        raise ValueError("Workplace must be remote, hybrid, on-site or any.")
    job_type = str(merged.get("job_type", "") or "")
    if job_type not in JOB_TYPES:
        raise ValueError("Choose a supported employment type.")
    mode = str(merged.get("mode", "apply") or "apply")
    if mode not in MODES:
        raise ValueError(
            "Mode must be apply (submit automatically) or review (ask me first)."
        )
    return {
        "name": name,
        "positions": positions,
        "location": " ".join(str(merged.get("location", "") or "").split())[:200],
        "workplace": workplace,
        "job_type": job_type,
        "sources": sources,
        "boards": boards,
        "exclude_keywords": _text_list(
            merged.get("exclude_keywords"),
            limit=25,
            item_chars=60,
            label="Excluded words",
        ),
        "exclude_companies": _text_list(
            merged.get("exclude_companies"),
            limit=50,
            item_chars=120,
            label="Excluded companies",
        ),
        "min_score": _int(merged.get("min_score", 70), "Minimum match score", 0, 100),
        "max_per_run": _int(
            merged.get("max_per_run", 3), "Applications per run", 1, MAX_PER_RUN
        ),
        "interval_minutes": _int(
            merged.get("interval_minutes", 240),
            "Run interval (minutes)",
            MIN_INTERVAL,
            MAX_INTERVAL,
        ),
        "mode": mode,
        "match_title": _flag(merged.get("match_title", 1)),
        "linkedin_easy_apply": _flag(merged.get("linkedin_easy_apply", 0)),
        "cover_letters": _flag(merged.get("cover_letters", 1)),
        "enabled": _flag(merged.get("enabled", 1)),
    }


_JSON_PLAN_FIELDS = (
    "positions",
    "sources",
    "boards",
    "exclude_keywords",
    "exclude_companies",
)
_FLAG_PLAN_FIELDS = ("match_title", "linkedin_easy_apply", "cover_letters", "enabled")


def plan_view(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    plan = dict(row)
    for name in _JSON_PLAN_FIELDS:
        plan[name] = _loads(plan.get(name), [])
    for name in _FLAG_PLAN_FIELDS:
        plan[name] = bool(plan.get(name))
    searches = len(plan["positions"]) * (len(plan["sources"]) + len(plan["boards"]))
    plan["searches_per_run"] = searches
    runs_per_month = math.ceil(
        30 * 24 * 60 / max(MIN_INTERVAL, plan["interval_minutes"])
    )
    plan["google_searches_per_month"] = (
        len(plan["positions"]) * runs_per_month
        if "google_jobs" in plan["sources"]
        else 0
    )
    return plan


def _plan_row(connection: sqlite3.Connection, plan_id: str) -> sqlite3.Row | None:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return cursor.execute(
            "SELECT * FROM autopilot_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
    finally:
        cursor.close()


def get_plan(connection: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    row = _plan_row(connection, plan_id)
    if row is None:
        raise LookupError("Search plan not found.")
    return plan_view(row)


def list_plans(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        rows = cursor.execute(
            "SELECT * FROM autopilot_plans ORDER BY created_at, rowid"
        ).fetchall()
    finally:
        cursor.close()
    return [plan_view(row) for row in rows]


def create_plan(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or _now()
    count = connection.execute("SELECT COUNT(*) FROM autopilot_plans").fetchone()[0]
    limit = MAX_PLANS
    if HOOKS.max_plans is not None:
        limit = (
            HOOKS.max_plans() or MAX_PLANS * 100
        )  # a hosted plan decides; no limit means a very high one
    if count >= limit:
        raise ValueError(f"You can keep up to {limit} search plans.")
    values = validate_plan(payload)
    plan_id = uuid.uuid4().hex
    columns = {
        **{
            key: json.dumps(value) if key in _JSON_PLAN_FIELDS else value
            for key, value in values.items()
        },
        "plan_id": plan_id,
        # The first pass waits a minute, so a plan saved by mistake can still be paused.
        "next_run_at": _iso(moment + timedelta(minutes=1)),
        "created_at": _iso(moment),
        "updated_at": _iso(moment),
    }
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO autopilot_plans ({names}) VALUES ({marks})",
        tuple(columns.values()),
    )
    connection.commit()
    return get_plan(connection, plan_id)


def update_plan(
    connection: sqlite3.Connection,
    plan_id: str,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or _now()
    existing = get_plan(connection, plan_id)
    values = validate_plan(payload, existing)
    columns = {
        key: json.dumps(value) if key in _JSON_PLAN_FIELDS else value
        for key, value in values.items()
    }
    columns["updated_at"] = _iso(moment)
    if values["enabled"] and not existing["enabled"]:
        columns["next_run_at"] = _iso(moment + timedelta(minutes=1))
    elif values["interval_minutes"] != existing["interval_minutes"]:
        columns["next_run_at"] = _iso(
            moment + timedelta(minutes=values["interval_minutes"])
        )
    assignments = ", ".join(f"{key} = ?" for key in columns)
    connection.execute(
        f"UPDATE autopilot_plans SET {assignments} WHERE plan_id = ?",
        (*columns.values(), plan_id),
    )
    connection.commit()
    return get_plan(connection, plan_id)


def delete_plan(connection: sqlite3.Connection, plan_id: str) -> None:
    if not connection.execute(
        "DELETE FROM autopilot_plans WHERE plan_id = ?", (plan_id,)
    ).rowcount:
        raise LookupError("Search plan not found.")
    connection.execute("DELETE FROM autopilot_passes WHERE plan_id = ?", (plan_id,))
    connection.commit()


def due_plans(connection: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        rows = cursor.execute(
            "SELECT * FROM autopilot_plans WHERE enabled = 1 AND next_run_at <= ? ORDER BY next_run_at",
            (_iso(now),),
        ).fetchall()
    finally:
        cursor.close()
    return [plan_view(row) for row in rows]


def claim_plan(
    connection: sqlite3.Connection, plan: Mapping[str, Any], now: datetime
) -> bool:
    """Move a due plan's next run forward so a second tick cannot start it twice."""
    claimed = connection.execute(
        "UPDATE autopilot_plans SET next_run_at = ? WHERE plan_id = ? AND next_run_at = ?",
        (
            _iso(now + timedelta(minutes=int(plan["interval_minutes"]))),
            plan["plan_id"],
            plan["next_run_at"],
        ),
    ).rowcount
    connection.commit()
    return bool(claimed)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #

_JSON_RUN_FIELDS = {
    "questions": "[]",
    "unanswered": "[]",
    "answers": "{}",
    "steps": "[]",
}


def run_view(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    run = dict(row)
    for name, default in _JSON_RUN_FIELDS.items():
        run[name] = _loads(run.get(name), json.loads(default))
    run["open"] = run["status"] in OPEN_STATUSES
    run["drafted_answers"] = answer_drafts.drafted_answers(run["questions"])
    return run


def _run_rows(
    connection: sqlite3.Connection, sql: str, args: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return [run_view(row) for row in cursor.execute(sql, tuple(args)).fetchall()]
    finally:
        cursor.close()


def get_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = _run_rows(
        connection, "SELECT * FROM application_runs WHERE run_id = ?", (run_id,)
    )
    if not rows:
        raise LookupError("Application run not found.")
    return rows[0]


def list_runs(
    connection: sqlite3.Connection,
    *,
    status: str = "",
    plan_id: str = "",
    job_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses, args = [], []
    if status == "open":
        clauses.append("status IN ('needs_review', 'ready')")
    elif status:
        clauses.append("status = ?")
        args.append(status)
    if plan_id:
        clauses.append("plan_id = ?")
        args.append(plan_id)
    if job_id:
        clauses.append("job_id = ?")
        args.append(job_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return _run_rows(
        connection,
        f"SELECT * FROM application_runs{where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (*args, max(1, min(limit, 500))),
    )


def create_run(
    connection: sqlite3.Connection,
    *,
    job: Mapping[str, Any],
    mode: str,
    trigger: str,
    plan_id: str | None = None,
    pass_id: str | None = None,
    correlation_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("Mode must be apply or review.")
    run_id = uuid.uuid4().hex
    connection.execute(
        """INSERT INTO application_runs (run_id, job_id, plan_id, pass_id, mode, trigger, status,
            application_url, score, created_at, correlation_id)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
        (
            run_id,
            job["job_id"],
            plan_id,
            pass_id,
            mode,
            trigger,
            str(job.get("application_url") or job.get("job_url") or ""),
            job.get("match_score"),
            _iso(now or _now()),
            correlation_id,
        ),
    )
    connection.commit()
    return get_run(connection, run_id)


def update_run(
    connection: sqlite3.Connection, run_id: str, fields: Mapping[str, Any]
) -> dict[str, Any]:
    allowed = {
        "status",
        "route",
        "ats",
        "application_url",
        "questions",
        "unanswered",
        "answers",
        "steps",
        "error",
        "confirmation_text",
        "confirmation_number",
        "evidence_document_id",
        "screenshot_document_id",
        "started_at",
        "finished_at",
        "mode",
    }
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Cannot update run fields: {sorted(bad)}")
    values = {
        key: json.dumps(value, ensure_ascii=False) if key in _JSON_RUN_FIELDS else value
        for key, value in fields.items()
    }
    if values:
        assignments = ", ".join(f"{key} = ?" for key in values)
        connection.execute(
            f"UPDATE application_runs SET {assignments} WHERE run_id = ?",
            (*values.values(), run_id),
        )
        connection.commit()
    return get_run(connection, run_id)


def handled_job_ids(connection: sqlite3.Connection, job_ids: Iterable[str]) -> set[str]:
    """Jobs a pass must leave alone: already submitted, or with any earlier run."""
    ids = list(dict.fromkeys(job_ids))
    handled: set[str] = set()
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ", ".join("?" for _ in chunk)
        for table in ("application_runs", "application_submissions"):
            handled.update(
                row[0]
                for row in connection.execute(
                    f"SELECT DISTINCT job_id FROM {table} WHERE job_id IN ({marks})",
                    chunk,
                )
            )
    return handled


def counts_today(
    connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, int]:
    start = (now or _now()).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = connection.execute(
        "SELECT status, COUNT(*) FROM application_runs WHERE created_at >= ? GROUP BY status",
        (_iso(start),),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


# --------------------------------------------------------------------------- #
# Remembered answers to custom questions
# --------------------------------------------------------------------------- #


def label_key(label: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(label).casefold()).split())[:240]


def save_custom_answer(
    connection: sqlite3.Connection,
    label: str,
    value: str,
    *,
    now: datetime | None = None,
) -> None:
    key = label_key(label)
    text = str(value or "").strip()[:4000]
    if not key or not text:
        return
    connection.execute(
        """INSERT INTO custom_answers (label_key, label, value, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(label_key) DO UPDATE SET label = excluded.label, value = excluded.value, updated_at = excluded.updated_at""",
        (key, " ".join(str(label).split())[:300], text, _iso(now or _now())),
    )
    connection.commit()


def custom_answers(connection: sqlite3.Connection) -> dict[str, str]:
    """Remembered answers keyed by the question label as it was shown."""
    return {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT label, value FROM custom_answers ORDER BY label"
        )
    }


def list_custom_answers(connection: sqlite3.Connection) -> list[dict[str, str]]:
    return [
        {"label_key": row[0], "label": row[1], "value": row[2], "updated_at": row[3]}
        for row in connection.execute(
            "SELECT label_key, label, value, updated_at FROM custom_answers ORDER BY label"
        )
    ]


def delete_custom_answer(connection: sqlite3.Connection, key: str) -> None:
    if not connection.execute(
        "DELETE FROM custom_answers WHERE label_key = ?", (key,)
    ).rowcount:
        raise LookupError("Saved answer not found.")
    connection.commit()


# --------------------------------------------------------------------------- #
# Which jobs qualify
# --------------------------------------------------------------------------- #


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").casefold())


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    # "architect" ~ "architects", "engineering" ~ "engineer": a shared stem of 5+ letters
    stem = min(len(a), len(b), 6)
    return stem >= 5 and a[:stem] == b[:stem]


def title_matches(title: str, positions: Sequence[str]) -> bool:
    """Whether ``title`` names one of the positions (half its key words, at least one)."""
    words = _words(title)
    if not words:
        return False
    for position in positions:
        keys = [w for w in _words(position) if w not in _SENIORITY_WORDS and len(w) > 1]
        if not keys:
            continue
        hits = sum(1 for key in keys if any(_same_word(key, word) for word in words))
        if hits >= max(1, math.ceil(len(keys) / 2)):
            return True
    return False


def _excluded(
    plan: Mapping[str, Any], job: Mapping[str, Any], *, with_description: bool
) -> str:
    company = str(job.get("company") or "").casefold()
    for name in plan.get("exclude_companies") or []:
        if name.casefold() and name.casefold() in company:
            return f"company {name} is excluded"
    haystack = " ".join(
        str(job.get(key) or "")
        for key in (
            ("title", "company", "description")
            if with_description
            else ("title", "company")
        )
    ).casefold()
    for word in plan.get("exclude_keywords") or []:
        if re.search(
            rf"(?<![a-z0-9]){re.escape(word.casefold())}(?![a-z0-9])", haystack
        ):
            return f"mentions '{word}'"
    return ""


def quick_reason(plan: Mapping[str, Any], job: Mapping[str, Any]) -> str:
    """Why a job is out before its description is read ('' when it may qualify)."""
    title = str(job.get("title") or "")
    if (
        plan.get("match_title", True)
        and title
        and not title.startswith("LinkedIn job ")
        and not title_matches(title, plan["positions"])
    ):
        return "title does not match the positions"
    return _excluded(plan, job, with_description=False)


# Stages a job can be in and still be new to the pipeline. ``found`` is the pre-rename name of
# ``discovered``: older databases still hand it out as a column default.
ENTRY_STAGES = frozenset({"discovered", "saved", "found"})


def candidate_reason(plan: Mapping[str, Any], job: Mapping[str, Any]) -> str:
    """Why a scored job does not qualify for this plan ('' when it does)."""
    if job.get("archived_at"):
        return "archived"
    if str(job.get("stage") or "discovered") not in ENTRY_STAGES:
        return "already in your pipeline"
    if not job.get("description"):
        return "no description to score"
    if plan.get("match_title", True) and not title_matches(
        str(job.get("title") or ""), plan["positions"]
    ):
        return "title does not match the positions"
    excluded = _excluded(plan, job, with_description=True)
    if excluded:
        return excluded
    score = job.get("match_score")
    if score is None:
        return "could not be scored (complete your profile)"
    explanation = job.get("match_explanation") or {}
    if isinstance(explanation, str):
        explanation = _loads(explanation, {})
    if explanation.get("hard_blockers"):
        return "has a blocker: " + str(explanation["hard_blockers"][0])[:120]
    if job.get("match_classification") == "Not Recommended":
        return "classified Not Recommended"
    if float(score) < float(plan.get("min_score", 70)):
        return f"score below {plan.get('min_score', 70)}"
    return ""


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #


@dataclass
class Services:
    """What a pass needs from the server. Every callable may raise; the pass records it."""

    connect: Callable[[], sqlite3.Connection]
    # (plan, source, identifier, position) -> decoded jobs found (each with job_id)
    search: Callable[[Mapping[str, Any], str, str, str], list[dict[str, Any]]]
    # job_id -> the decoded job with its description loaded and scored, or None
    load_and_score: Callable[[str], dict[str, Any] | None]
    # (job, run, answers, submit, plan) -> outcome dict (see normalize_outcome)
    apply: Callable[
        [
            dict[str, Any],
            dict[str, Any],
            Mapping[str, str],
            bool,
            Mapping[str, Any] | None,
        ],
        dict[str, Any],
    ]
    # (job, run, outcome) -> None: record the submission, move the job to Applied
    on_submitted: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]
    # (job, run, outcome) -> None: move the job to Needs review
    on_held: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]
    # (job_id) -> the decoded job, or None
    job: Callable[[str], dict[str, Any] | None]
    # applications still allowed today (None = no limit)
    daily_remaining: Callable[[], int | None]
    paused: Callable[[], bool]
    notify: Callable[[str, str, str], None] = lambda *_args: None
    now: Callable[[], datetime] = _now
    # sources a pass cannot use right now -> reason (LinkedIn offline, no SerpApi key)
    unavailable: Callable[[str], str] = lambda _source: ""
    # whether a run may submit on its own (a hosted organization can require a person to click first);
    # a person's explicit approval of a filled form always submits
    submit_allowed: Callable[[], bool] = lambda: True
    # (job, unanswered questions) -> model drafts (aira.answer_drafts.draft_answers); None = no drafting
    draft_answers: (
        Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]] | None
    ) = None
    # "Review drafted answers": True holds drafts for the person; False uses confident ones
    review_drafts: Callable[[], bool] = lambda: True
    log: list[str] = field(default_factory=list)


def normalize_outcome(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The common shape of an engine or Easy Apply result."""
    status = str(raw.get("status") or "failed")
    if status not in OUTCOME_TO_STATUS:
        status = "failed"
    return {
        "status": status,
        "route": str(raw.get("route") or ""),
        "ats": str(raw.get("ats") or ""),
        "questions": list(raw.get("questions") or []),
        "unanswered": list(raw.get("unanswered") or []),
        "steps": [
            str(step)[:300] for step in list(raw.get("steps") or [])[-STEPS_KEPT:]
        ],
        "error": str(raw.get("error") or "")[:1000],
        "confirmation_text": str(raw.get("confirmation_text") or "")[:5000],
        "confirmation_number": str(raw.get("confirmation_number") or "")[:120],
        "evidence_document_id": raw.get("evidence_document_id"),
        "screenshot_document_id": raw.get("screenshot_document_id"),
        "url": str(raw.get("url") or ""),
    }


def _job_label(job: Mapping[str, Any]) -> str:
    title = str(job.get("title") or job.get("job_id") or "a job")
    company = str(job.get("company") or "")
    return f"{title} at {company}" if company else title


def execute_run(
    services: Services,
    run_id: str,
    *,
    answers: Mapping[str, str] | None = None,
    submit: bool | None = None,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply for one run and record what happened. ``submit`` defaults to the run's mode."""
    with services.connect() as connection:
        run = get_run(connection, run_id)
    job = services.job(run["job_id"])
    if job is None:
        with services.connect() as connection:
            return update_run(
                connection,
                run_id,
                {
                    "status": "failed",
                    "error": "The job no longer exists.",
                    "finished_at": _iso(services.now()),
                },
            )
    do_submit = run["mode"] == "apply" if submit is None else bool(submit)
    if submit is None and do_submit and not services.submit_allowed():
        do_submit = False  # filled and held for a person's click
    if do_submit:
        remaining = services.daily_remaining()
        if remaining is not None and remaining <= 0:
            raise ValueError(
                "The daily application limit is reached. Raise it, or Monosage continues tomorrow."
            )
    with services.connect() as connection:
        merged_answers = {
            **run["answers"],
            **{k: str(v) for k, v in (answers or {}).items() if str(v).strip()},
        }
        if (
            job.get("submission")
            or connection.execute(
                "SELECT 1 FROM application_submissions WHERE job_id = ?",
                (job["job_id"],),
            ).fetchone()
        ):
            return update_run(
                connection,
                run_id,
                {
                    "status": "already_applied",
                    "error": "Monosage already recorded an application for this job.",
                    "finished_at": _iso(services.now()),
                },
            )
        update_run(
            connection,
            run_id,
            {
                "status": "running",
                "answers": merged_answers,
                "started_at": _iso(services.now()),
                "error": "",
            },
        )
    try:
        outcome = normalize_outcome(
            services.apply(job, run, merged_answers, do_submit, plan)
        )
    except Exception as error:  # noqa: BLE001 - a broken site or tool must not end the pass
        outcome = normalize_outcome(
            {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        )
    # Questions nothing could answer: let the model draft answers (aira.answer_drafts).
    used_drafts: dict[str, dict[str, Any]] = {}
    if (
        services.draft_answers is not None
        and outcome["status"] == "needs_review"
        and outcome["unanswered"]
    ):
        drafter = services.draft_answers
        drafting = answer_drafts.review_plan(
            lambda questions: drafter(job, questions),
            outcome["unanswered"],
            review=services.review_drafts(),
            known=run["unanswered"],
        )
        outcome["unanswered"] = drafting.unanswered
        outcome["steps"] = [*outcome["steps"], *drafting.steps][-STEPS_KEPT:]
        if drafting.use:
            merged_answers = {**merged_answers, **drafting.use}
            used_drafts = drafting.used
            try:
                retried = normalize_outcome(
                    services.apply(job, run, merged_answers, do_submit, plan)
                )
            except Exception as error:  # noqa: BLE001 - same rule as the first attempt
                retried = normalize_outcome(
                    {"status": "failed", "error": f"{type(error).__name__}: {error}"}
                )
            retried["steps"] = [*outcome["steps"], *retried["steps"]][-STEPS_KEPT:]
            retried["unanswered"] = answer_drafts.attach_drafts(
                retried["unanswered"], answer_drafts.drafts_of(drafting.unanswered)
            )
            outcome = retried
    outcome["questions"] = answer_drafts.mark_drafted(
        outcome["questions"],
        used=used_drafts,
        offered=run["unanswered"],
        earlier=run["questions"],
        answers=answers,
    )
    status = OUTCOME_TO_STATUS[outcome["status"]]
    if outcome["status"] == "filled" and do_submit:
        status = "failed"  # the engine never reports "filled" when asked to submit
    if outcome["status"] == "external":
        outcome["error"] = outcome["error"] or (
            f"This job applies on the company site{': ' + outcome['url'] if outcome['url'] else ''}."
        )
    with services.connect() as connection:
        updated = update_run(
            connection,
            run_id,
            {
                "status": status,
                "route": outcome["route"],
                "ats": outcome["ats"],
                "questions": outcome["questions"],
                "unanswered": outcome["unanswered"],
                "steps": outcome["steps"],
                "error": outcome["error"],
                "confirmation_text": outcome["confirmation_text"],
                "confirmation_number": outcome["confirmation_number"],
                "evidence_document_id": outcome["evidence_document_id"],
                "screenshot_document_id": outcome["screenshot_document_id"],
                "application_url": outcome["url"] or run["application_url"],
                "answers": merged_answers,
                "finished_at": _iso(services.now()),
            },
        )
    label = _job_label(job)
    if status == "submitted":
        services.on_submitted(job, updated, outcome)
        services.notify(
            "Applied automatically",
            f"Monosage applied to {label}.{' Confirmation ' + outcome['confirmation_number'] + '.' if outcome['confirmation_number'] else ''}",
            "#/jobs/autopilot",
        )
    elif status in OPEN_STATUSES:
        services.on_held(job, updated, outcome)
        services.notify(
            "Auto-apply needs you" if status == "needs_review" else "Ready to submit",
            (
                f"{label}: {len(outcome['unanswered'])} question(s) need your answer."
                if status == "needs_review"
                else f"{label}: the form is filled and waits for your OK."
            ),
            "#/jobs/autopilot",
        )
    return updated


def _start_pass(
    connection: sqlite3.Connection, plan_id: str, trigger: str, now: datetime
) -> str:
    pass_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO autopilot_passes (pass_id, plan_id, trigger, started_at) VALUES (?, ?, ?, ?)",
        (pass_id, plan_id, trigger, _iso(now)),
    )
    connection.commit()
    return pass_id


def _summary(counts: Mapping[str, int]) -> str:
    parts = [f"{counts.get('found', 0)} found", f"{counts.get('qualified', 0)} matched"]
    for key, word in (
        ("submitted", "applied"),
        ("ready", "ready for your OK"),
        ("needs_review", "need answers"),
        ("failed", "failed"),
        ("unsupported", "apply manually"),
        ("already_applied", "already applied"),
    ):
        if counts.get(key):
            parts.append(f"{counts[key]} {word}")
    if counts.get("search_errors"):
        parts.append(f"{counts['search_errors']} search error(s)")
    return ", ".join(parts)


def run_plan(
    services: Services, plan_id: str, *, trigger: str = "schedule"
) -> dict[str, Any]:
    """One pass of a plan: search, qualify, apply. Returns the pass record."""
    moment = services.now()
    with services.connect() as connection:
        plan = get_plan(connection, plan_id)
        pass_id = _start_pass(connection, plan_id, trigger, moment)
    log: list[str] = []
    counts: dict[str, int] = {
        "found": 0,
        "new": 0,
        "qualified": 0,
        "search_errors": 0,
    }
    reasons: dict[str, int] = {}

    def note(text: str) -> None:
        log.append(text[:300])

    status = "ok"
    if services.paused():
        note("Auto-apply is paused; nothing was searched or sent.")
        status = "paused"
    else:
        found: dict[str, dict[str, Any]] = {}
        places = [(source, "") for source in plan["sources"]] + [
            (board["source"], board["identifier"]) for board in plan["boards"]
        ]
        for source, identifier in places:
            where = f"{source}{' ' + identifier if identifier else ''}"
            blocked = services.unavailable(source)
            if blocked:
                note(f"Skipped {where}: {blocked}")
                counts["search_errors"] += 1
                continue
            for position in plan["positions"]:
                try:
                    jobs = services.search(plan, source, identifier, position)
                except Exception as error:  # noqa: BLE001
                    counts["search_errors"] += 1
                    note(
                        f"Search failed on {where} for '{position}': {str(error)[:160]}"
                    )
                    continue
                note(f"{where}: {len(jobs)} result(s) for '{position}'")
                for job in jobs:
                    if job.get("job_id"):
                        found.setdefault(job["job_id"], job)
        counts["found"] = len(found)
        with services.connect() as connection:
            handled = handled_job_ids(connection, found)
        fresh = [job for job_id, job in found.items() if job_id not in handled]
        counts["new"] = len(fresh)
        qualified: list[dict[str, Any]] = []
        loads = 0
        for job in fresh:
            reason = quick_reason(plan, job)
            if not reason:
                if not job.get("description") and loads >= DETAIL_LOADS_PER_PASS:
                    reason = "left for the next run (detail limit per run)"
                else:
                    if not job.get("description"):
                        loads += 1
                    try:
                        scored = services.load_and_score(job["job_id"])
                    except Exception as error:  # noqa: BLE001
                        scored = None
                        note(f"Could not read {_job_label(job)}: {str(error)[:160]}")
                    reason = (
                        candidate_reason(plan, scored)
                        if scored is not None
                        else "no description to score"
                    )
                    if not reason and scored is not None:
                        qualified.append(scored)
                        continue
            reasons[reason] = reasons.get(reason, 0) + 1
        counts["qualified"] = len(qualified)
        qualified.sort(key=lambda job: float(job.get("match_score") or 0), reverse=True)
        sent = 0
        for job in qualified:
            if sent >= plan["max_per_run"]:
                note(
                    f"Reached this plan's limit of {plan['max_per_run']} application(s) per run."
                )
                break
            if services.paused():
                note("Auto-apply was paused during the run.")
                break
            remaining = services.daily_remaining()
            if plan["mode"] == "apply" and remaining is not None and remaining <= 0:
                note("Daily application limit reached; the rest wait for tomorrow.")
                break
            with services.connect() as connection:
                if handled_job_ids(connection, [job["job_id"]]):
                    continue
                run = create_run(
                    connection,
                    job=job,
                    mode=plan["mode"],
                    trigger=trigger,
                    plan_id=plan_id,
                    pass_id=pass_id,
                    now=services.now(),
                )
            try:
                done = execute_run(services, run["run_id"], plan=plan)
            except ValueError as error:  # the daily limit was reached meanwhile
                with services.connect() as connection:
                    connection.execute(
                        "DELETE FROM application_runs WHERE run_id = ?",
                        (run["run_id"],),
                    )
                    connection.commit()
                note(str(error))
                break
            counts[done["status"]] = counts.get(done["status"], 0) + 1
            note(
                f"{_job_label(job)} ({round(float(job.get('match_score') or 0))}%): {done['status'].replace('_', ' ')}"
            )
            sent += 1
        for reason, number in sorted(reasons.items(), key=lambda item: -item[1])[:8]:
            note(f"{number} not applied: {reason}")
        if counts["search_errors"] and not counts["found"]:
            status = "error"
    summary = _summary(counts) if status != "paused" else "Paused"
    finished = services.now()
    with services.connect() as connection:
        connection.execute(
            "UPDATE autopilot_passes SET finished_at = ?, status = ?, counts = ?, summary = ?, log = ? WHERE pass_id = ?",
            (
                _iso(finished),
                status,
                json.dumps({**counts, "reasons": reasons}),
                summary,
                json.dumps(log),
                pass_id,
            ),
        )
        connection.execute(
            "UPDATE autopilot_plans SET last_run_at = ?, last_status = ?, last_summary = ?, next_run_at = ? WHERE plan_id = ?",
            (
                _iso(finished),
                status,
                summary,
                _iso(finished + timedelta(minutes=int(plan["interval_minutes"]))),
                plan_id,
            ),
        )
        # Keep the last 50 passes per plan.
        connection.execute(
            """DELETE FROM autopilot_passes WHERE plan_id = ? AND pass_id NOT IN (
                SELECT pass_id FROM autopilot_passes WHERE plan_id = ? ORDER BY started_at DESC LIMIT 50)""",
            (plan_id, plan_id),
        )
        connection.commit()
        return get_pass(connection, pass_id)


def get_pass(connection: sqlite3.Connection, pass_id: str) -> dict[str, Any]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        row = cursor.execute(
            "SELECT * FROM autopilot_passes WHERE pass_id = ?", (pass_id,)
        ).fetchone()
    finally:
        cursor.close()
    if row is None:
        raise LookupError("Run not found.")
    data = dict(row)
    data["counts"] = _loads(data.get("counts"), {})
    data["log"] = _loads(data.get("log"), [])
    return data


def list_passes(
    connection: sqlite3.Connection, *, plan_id: str = "", limit: int = 20
) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        rows = cursor.execute(
            "SELECT pass_id FROM autopilot_passes"
            + (" WHERE plan_id = ?" if plan_id else "")
            + " ORDER BY started_at DESC, rowid DESC LIMIT ?",
            ((plan_id, limit) if plan_id else (limit,)),
        ).fetchall()
    finally:
        cursor.close()
    return [get_pass(connection, row["pass_id"]) for row in rows]
