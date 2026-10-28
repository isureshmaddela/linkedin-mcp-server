"""Versioned SQLite schema migrations for a user's Aira database.

Each migration is additive and idempotent. The ledger table ``schema_migrations``
records which versions ran so later connections only check one row. Migration 1
reproduces the schema that earlier Aira builds created at access time, so
databases from those builds upgrade in place without data loss.

Rollback plan: every migration only adds tables, columns, or indexes. Rolling
back code to an earlier version keeps working because older code ignores the
extra columns. Restore from the pre-upgrade backup only if data must be
reverted; see docs/runbook.md.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from linkedin_mcp_server.job_sources import canonicalize_job_url


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    description: str
    apply: Callable[[sqlite3.Connection], None]


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _add_column(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    if column not in _columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _baseline(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', job_url TEXT NOT NULL,
            application_url TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '', fit_score INTEGER,
            fit_summary TEXT NOT NULL DEFAULT '', strengths TEXT NOT NULL DEFAULT '[]',
            gaps TEXT NOT NULL DEFAULT '[]', resume_draft TEXT NOT NULL DEFAULT '',
            cover_letter TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'discovered',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL DEFAULT 'linkedin', external_id TEXT NOT NULL DEFAULT '',
            content_fingerprint TEXT NOT NULL DEFAULT '', match_evidence TEXT NOT NULL DEFAULT '{}',
            workplace_type TEXT NOT NULL DEFAULT '', resume_hash TEXT
        )"""
    )
    _add_column(connection, "jobs", "source", "TEXT NOT NULL DEFAULT 'linkedin'")
    _add_column(connection, "jobs", "external_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(connection, "jobs", "content_fingerprint", "TEXT NOT NULL DEFAULT ''")
    _add_column(connection, "jobs", "match_evidence", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(connection, "jobs", "workplace_type", "TEXT NOT NULL DEFAULT ''")
    _add_column(connection, "jobs", "application_url", "TEXT NOT NULL DEFAULT ''")
    _add_column(connection, "jobs", "resume_hash", "TEXT")
    connection.execute(
        "UPDATE jobs SET application_url = job_url WHERE application_url = ''"
    )
    connection.execute("UPDATE jobs SET external_id = job_id WHERE external_id = ''")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_external_id ON jobs(source, external_id)"
    )
    has_source_records = _table_exists(connection, "job_source_records")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS job_source_records (
            source TEXT NOT NULL, external_id TEXT NOT NULL, job_id TEXT NOT NULL,
            source_url TEXT NOT NULL, application_url TEXT NOT NULL DEFAULT '',
            canonical_url TEXT NOT NULL DEFAULT '',
            content_fingerprint TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, external_id)
        )"""
    )
    _add_column(
        connection, "job_source_records", "application_url", "TEXT NOT NULL DEFAULT ''"
    )
    if not has_source_records:
        for row in connection.execute(
            "SELECT job_id, source, external_id, job_url, application_url, content_fingerprint FROM jobs"
        ).fetchall():
            try:
                canonical_url = canonicalize_job_url(row[3])
            except ValueError:
                canonical_url = ""
            connection.execute(
                """INSERT OR IGNORE INTO job_source_records
                (source, external_id, job_id, source_url, application_url, canonical_url, content_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row[1], row[2], row[0], row[3], row[4], canonical_url, row[5]),
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_source_records_job_id ON job_source_records(job_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_source_records_canonical_url ON job_source_records(canonical_url)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS application_events (
            event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, previous_status TEXT,
            new_status TEXT NOT NULL, occurred_at INTEGER NOT NULL, source TEXT NOT NULL,
            actor TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', correlation_id TEXT NOT NULL
        )"""
    )
    connection.execute(
        """UPDATE jobs SET stage = CASE stage
            WHEN 'found' THEN 'discovered'
            WHEN 'reviewing' THEN 'needs_review'
            WHEN 'ready' THEN 'ready_to_apply'
            WHEN 'interview' THEN 'recruiter_screen'
            ELSE stage END
            WHERE stage IN ('found', 'reviewing', 'ready', 'interview')"""
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS message_drafts (
            thread_id TEXT PRIMARY KEY, participant TEXT NOT NULL DEFAULT '',
            reply TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT 'United States',
            interval_minutes INTEGER NOT NULL, max_items INTEGER NOT NULL DEFAULT 3,
            min_fit INTEGER NOT NULL DEFAULT 60, enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at INTEGER NOT NULL, last_run_at INTEGER, last_status TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, kind TEXT NOT NULL,
            started_at INTEGER NOT NULL, finished_at INTEGER NOT NULL,
            status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS message_checks (thread_id TEXT PRIMARY KEY, checked_at INTEGER NOT NULL, thread_hash TEXT NOT NULL DEFAULT '')"
    )
    _add_column(connection, "message_checks", "thread_hash", "TEXT NOT NULL DEFAULT ''")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, source TEXT NOT NULL,
            operation TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '', duration_ms INTEGER
        )"""
    )


def _job_enrichment(connection: sqlite3.Connection) -> None:
    for column, definition in (
        ("employment_type", "TEXT NOT NULL DEFAULT ''"),
        ("salary_min", "REAL"),
        ("salary_max", "REAL"),
        ("currency", "TEXT NOT NULL DEFAULT ''"),
        ("seniority", "TEXT NOT NULL DEFAULT ''"),
        ("posted_at", "TEXT"),
        ("expires_at", "TEXT"),
        ("application_method", "TEXT NOT NULL DEFAULT ''"),
        ("required_skills", "TEXT NOT NULL DEFAULT '[]'"),
        ("preferred_skills", "TEXT NOT NULL DEFAULT '[]'"),
        ("sponsorship_statement", "TEXT NOT NULL DEFAULT ''"),
        ("easy_apply", "INTEGER"),
        ("saved", "INTEGER NOT NULL DEFAULT 0"),
        ("saved_at", "TEXT"),
        ("match_score", "REAL"),
        ("match_version", "TEXT NOT NULL DEFAULT ''"),
        ("match_classification", "TEXT NOT NULL DEFAULT ''"),
        ("match_explanation", "TEXT NOT NULL DEFAULT '{}'"),
        ("match_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("scored_at", "TEXT"),
        ("first_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("last_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("stale", "INTEGER NOT NULL DEFAULT 0"),
        ("repost_of", "TEXT NOT NULL DEFAULT ''"),
        ("notes", "TEXT NOT NULL DEFAULT ''"),
        ("follow_up_at", "TEXT"),
        ("applied_at", "TEXT"),
        ("archived_at", "TEXT"),
    ):
        _add_column(connection, "jobs", column, definition)
    connection.execute(
        "UPDATE jobs SET first_seen_at = updated_at WHERE first_seen_at = ''"
    )
    connection.execute(
        "UPDATE jobs SET last_seen_at = updated_at WHERE last_seen_at = ''"
    )
    connection.execute(
        "UPDATE jobs SET saved = 1, saved_at = updated_at WHERE stage = 'saved' AND saved = 0"
    )


def _candidate_answers(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS standard_answers (
            canonical_field TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '',
            confirmed INTEGER NOT NULL DEFAULT 0, sensitivity TEXT NOT NULL DEFAULT 'personal',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _applications(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS application_preparations (
            prep_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
            resume_document_id TEXT, cover_letter_document_id TEXT,
            fields TEXT NOT NULL DEFAULT '[]', questions_source TEXT NOT NULL DEFAULT '',
            review_required INTEGER NOT NULL DEFAULT 1, review_notes TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            correlation_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_preparations_job ON application_preparations(job_id, created_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS application_submissions (
            submission_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, prep_id TEXT,
            submitted_at TEXT NOT NULL, method TEXT NOT NULL DEFAULT 'manual',
            confirmation_text TEXT NOT NULL DEFAULT '', confirmation_number TEXT NOT NULL DEFAULT '',
            evidence_document_id TEXT, actor TEXT NOT NULL DEFAULT 'user',
            correlation_id TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL UNIQUE
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_submissions_job ON application_submissions(job_id)"
    )


def _crm(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS recruiters (
            recruiter_id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
            linkedin_url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'user', consent_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recruiters_company ON recruiters(company)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recruiters_email ON recruiters(email)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS recruiter_interactions (
            interaction_id TEXT PRIMARY KEY, recruiter_id TEXT NOT NULL, job_id TEXT,
            kind TEXT NOT NULL DEFAULT 'note', direction TEXT NOT NULL DEFAULT 'outbound',
            summary TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL,
            follow_up_at TEXT, source_message_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recruiter_interactions_recruiter ON recruiter_interactions(recruiter_id, occurred_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS interviews (
            interview_id TEXT PRIMARY KEY, job_id TEXT, company TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '', interview_type TEXT NOT NULL DEFAULT 'recruiter_screen',
            start_at TEXT NOT NULL, end_at TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'UTC',
            recruiter_id TEXT, hiring_manager TEXT NOT NULL DEFAULT '',
            meeting_link TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '', preparation TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '', action_items TEXT NOT NULL DEFAULT '[]',
            follow_up_at TEXT, status TEXT NOT NULL DEFAULT 'scheduled',
            calendar_uid TEXT NOT NULL DEFAULT '', calendar_sequence INTEGER NOT NULL DEFAULT 0,
            calendar_fingerprint TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'user', source_message_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_interviews_start ON interviews(start_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_interviews_job ON interviews(job_id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS interview_participants (
            participant_id TEXT PRIMARY KEY, interview_id TEXT NOT NULL,
            name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_interview_participants_interview ON interview_participants(interview_id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'reminder', title TEXT NOT NULL,
            due_at TEXT, status TEXT NOT NULL DEFAULT 'open', job_id TEXT, interview_id TEXT,
            recruiter_id TEXT, source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, due_at)"
    )


def _intelligence(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS email_messages (
            message_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL DEFAULT 'paste', subject TEXT NOT NULL DEFAULT '',
            sender_name TEXT NOT NULL DEFAULT '', sender_email TEXT NOT NULL DEFAULT '',
            received_at TEXT, category TEXT NOT NULL DEFAULT 'unrelated',
            confidence REAL NOT NULL DEFAULT 0, signals TEXT NOT NULL DEFAULT '[]',
            details TEXT NOT NULL DEFAULT '{}', suggested_action TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending', job_id TEXT,
            applied_action TEXT NOT NULL DEFAULT '{}', user_category TEXT NOT NULL DEFAULT '',
            body_excerpt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_messages_status ON email_messages(status, created_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS audit_logs (
            audit_id TEXT PRIMARY KEY, at INTEGER NOT NULL, actor TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user', action TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '', target_id TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '{}', correlation_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_at ON audit_logs(at)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_target ON audit_logs(target_type, target_id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS source_sync_runs (
            run_id TEXT PRIMARY KEY, source TEXT NOT NULL, identifier TEXT NOT NULL DEFAULT '',
            query TEXT NOT NULL DEFAULT '{}', started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running', found INTEGER NOT NULL DEFAULT 0,
            imported INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
            correlation_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_sync_runs_source ON source_sync_runs(source, started_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS integration_connections (
            provider TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'disconnected',
            config TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS notifications (
            notification_id TEXT PRIMARY KEY, at INTEGER NOT NULL, level TEXT NOT NULL DEFAULT 'info',
            title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', read INTEGER NOT NULL DEFAULT 0,
            link TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read, at)"
    )


def _query_indexes(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(stage)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_saved ON jobs(saved)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(content_fingerprint)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_events_job ON application_events(job_id, occurred_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_events_status ON application_events(new_status, occurred_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_log_at ON activity_log(at)"
    )


def _documents(connection: sqlite3.Connection) -> None:
    from aira.documents import ensure_schema

    ensure_schema(connection)


def _message_replies(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS message_threads (
            thread_id TEXT PRIMARY KEY, participant TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'manual', recruiter_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_threads_mode ON message_threads(mode)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS message_sends (
            send_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            participant TEXT NOT NULL DEFAULT '', reply TEXT NOT NULL,
            reply_hash TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            conversation_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, correlation_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_sends_thread ON message_sends(thread_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_sends_status ON message_sends(status, created_at)"
    )
    # One automatic send per conversation state: the same inbound text can never
    # trigger two automatic replies, even if two inbox checks overlap.
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_message_sends_auto_once
        ON message_sends(thread_id, conversation_hash)
        WHERE mode = 'auto' AND status = 'sent' AND conversation_hash != ''"""
    )


def _inbox_check_report(connection: sqlite3.Connection) -> None:
    # Rows created automatically (for example when a conversation is opened)
    # keep mode_set_by_user = 0 so the auto-reply scope setting applies to them.
    _add_column(
        connection, "message_threads", "mode_set_by_user", "INTEGER NOT NULL DEFAULT 0"
    )
    connection.execute(
        "UPDATE message_threads SET mode_set_by_user = 1 WHERE mode = 'auto'"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS inbox_check_items (
            item_id TEXT PRIMARY KEY, run_key TEXT NOT NULL, thread_id TEXT NOT NULL,
            participant TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'manual',
            mode_reason TEXT NOT NULL DEFAULT '', speaker TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbox_check_items_run ON inbox_check_items(run_key, created_at)"
    )


def inbox_cache_v1(connection: sqlite3.Connection) -> None:
    """Tables of migration 11, written out in full so later code changes cannot alter it."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS inbox_threads (
            thread_id TEXT PRIMARY KEY,
            participant TEXT NOT NULL DEFAULT '',
            snippet TEXT NOT NULL DEFAULT '',
            last_message_label TEXT NOT NULL DEFAULT '',
            last_message_at TEXT NOT NULL DEFAULT '',
            last_sender TEXT NOT NULL DEFAULT 'unknown',
            needs_reply INTEGER NOT NULL DEFAULT 0,
            is_recruiter INTEGER NOT NULL DEFAULT 0,
            message_count INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            signature TEXT NOT NULL DEFAULT '',
            fetched_signature TEXT NOT NULL DEFAULT '',
            read_signature TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            listed_at INTEGER NOT NULL DEFAULT 0,
            messages_synced_at INTEGER NOT NULL DEFAULT 0,
            synced_at INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbox_threads_order ON inbox_threads(listed_at DESC, position)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS inbox_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            msg_key TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT '',
            own INTEGER NOT NULL DEFAULT 0,
            date_label TEXT NOT NULL DEFAULT '',
            sent_on TEXT NOT NULL DEFAULT '',
            time_label TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'linkedin',
            created_at INTEGER NOT NULL
        )"""
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_messages_key ON inbox_messages(thread_id, msg_key)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbox_messages_order ON inbox_messages(thread_id, ordinal)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS inbox_sync_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'running',
            phase TEXT NOT NULL DEFAULT 'listing',
            total INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            started_at INTEGER NOT NULL,
            finished_at INTEGER
        )"""
    )


def _inbox_cache(connection: sqlite3.Connection) -> None:
    inbox_cache_v1(connection)


def _airaapi(connection: sqlite3.Connection) -> None:
    from aira.airaapi.store import ensure_schema

    ensure_schema(connection)


def _airaapi_parity(connection: sqlite3.Connection) -> None:
    from aira.airaapi.store import backfill_text_index, ensure_schema

    ensure_schema(connection)
    backfill_text_index(connection)


def _autopilot(connection: sqlite3.Connection) -> None:
    from aira.autopilot import ensure_schema

    ensure_schema(connection)


def _careers(connection: sqlite3.Connection) -> None:
    from aira.airaapi.careers import ensure_schema

    ensure_schema(connection)


def _leads(connection: sqlite3.Connection) -> None:
    from aira.leads.store import ensure_schema

    ensure_schema(connection)


def _leads_outreach(connection: sqlite3.Connection) -> None:
    from aira.leads.store import ensure_schema

    ensure_schema(connection)


def _recruiter_contacts(connection: sqlite3.Connection) -> None:
    from aira.recruiter_contacts import ensure_schema

    ensure_schema(connection)


def _normalize_stages(connection: sqlite3.Connection) -> None:
    """Rename the old stage names again.

    Databases created by early builds declare ``jobs.stage DEFAULT 'found'`` and SQLite cannot change a
    column default, so every job inserted without an explicit stage came back as ``found`` after the
    baseline migration had already renamed the old ones. The server now names the stage when it inserts;
    this renames the rows that earlier inserts left behind.
    """
    connection.execute(
        """UPDATE jobs SET stage = CASE stage
            WHEN 'found' THEN 'discovered'
            WHEN 'reviewing' THEN 'needs_review'
            WHEN 'ready' THEN 'ready_to_apply'
            WHEN 'interview' THEN 'recruiter_screen'
            ELSE stage END
            WHERE stage IN ('found', 'reviewing', 'ready', 'interview')"""
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "baseline",
        "Tables created by earlier Monosage builds (jobs, source records, events, settings, drafts, schedules, runs, activity).",
        _baseline,
    ),
    Migration(
        2,
        "job_enrichment",
        "Normalized job fields, saved flag, explainable match score columns, seen timestamps, notes, follow-ups, archive.",
        _job_enrichment,
    ),
    Migration(
        3,
        "standard_answers",
        "User-confirmed standard application answers with sensitivity classification.",
        _candidate_answers,
    ),
    Migration(
        4,
        "applications",
        "Application preparation packets and submission/confirmation records with idempotency keys.",
        _applications,
    ),
    Migration(
        5,
        "crm_interviews_tasks",
        "Recruiter CRM, interactions, interviews, participants, and tasks.",
        _crm,
    ),
    Migration(
        6,
        "intelligence_audit",
        "Email messages, audit logs, source sync runs, integration connections, notifications.",
        _intelligence,
    ),
    Migration(
        7,
        "query_indexes",
        "Indexes for pipeline, source, saved, score, and event queries.",
        _query_indexes,
    ),
    Migration(
        8,
        "documents",
        "Encrypted document metadata and document audit tables.",
        _documents,
    ),
    Migration(
        9,
        "message_replies",
        "Per-conversation reply mode (manual or auto) and an append-only log of sent, held, and failed replies.",
        _message_replies,
    ),
    Migration(
        10,
        "inbox_check_report",
        "Whether a conversation's reply mode was set by the user, and a per-conversation report of each inbox check.",
        _inbox_check_report,
    ),
    Migration(
        11,
        "inbox_cache",
        "Local cache of LinkedIn conversations and their messages, plus a ledger of background inbox syncs.",
        _inbox_cache,
    ),
    Migration(
        12,
        "airaapi",
        "API console: stored searches, saved searches, monitors, usage rollups, encrypted secrets, API keys, response cache.",
        _airaapi,
    ),
    Migration(
        13,
        "careers",
        "Careers workspace: job alerts, the jobs each alert has seen, and stored intelligence reports.",
        _careers,
    ),
    Migration(
        14,
        "airaapi_gone",
        "Tombstones for deleted or pruned API searches, so archive lookups answer 410 like SerpApi.",
        _airaapi,
    ),
    Migration(
        15,
        "airaapi_parity",
        "Full-text index of stored API results (backfilled for the newest 5,000), X-Ray pixel-position copies, monitor monthly budgets and pause on error.",
        _airaapi_parity,
    ),
    Migration(
        16,
        "autopilot",
        "Auto-apply: scheduled search plans, their run log, per-job application runs with questions and evidence, and remembered answers to custom questions.",
        _autopilot,
    ),
    Migration(
        17,
        "normalize_stages",
        "Jobs saved with an old stage name by databases whose column default is 'found' become 'discovered' again, so automatic applying considers them.",
        _normalize_stages,
    ),
    Migration(
        18,
        "leads",
        "Monosage Leads: lead lists, leads with de-duplication keys, saved lead searches, search run history and the ideal customer profile.",
        _leads,
    ),
    Migration(
        19,
        "leads_outreach",
        "Monosage Leads outreach: the email outbox with approval, the suppression list and the call log.",
        _leads_outreach,
    ),
    Migration(
        20,
        "recruiter_contacts",
        "Recruiter contacts: where each contact was found (LinkedIn inbox, email, interview), saved contact lists, "
        "cached AI summaries of a contact's story and the do-not-contact list for recruiter outreach.",
        _recruiter_contacts,
    ),
)


def schema_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "schema_migrations"):
        return 0
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0] or 0)


def migrate(connection: sqlite3.Connection) -> list[int]:
    """Apply pending migrations in order and return the versions that ran."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
        )"""
    )
    current = schema_version(connection)
    applied: list[int] = []
    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        migration.apply(connection)
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
        )
        applied.append(migration.version)
    connection.commit()
    return applied


def describe() -> list[dict[str, object]]:
    """Return the migration ledger definition for documentation and the runbook."""
    return [
        {"version": item.version, "name": item.name, "description": item.description}
        for item in MIGRATIONS
    ]


LATEST_VERSION = MIGRATIONS[-1].version
