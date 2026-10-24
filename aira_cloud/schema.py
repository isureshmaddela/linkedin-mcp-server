"""Control-plane schema, as additive numbered migrations.

Rules: every table that holds tenant data has ``org_id`` as a mandatory indexed
column (or as the first column of its primary key); the data layer fills it from
the session. Billing records, the audit stub and platform audit rows carry no
foreign key to ``organizations`` on purpose: they outlive the organization.
"""

from __future__ import annotations

_V1 = """
CREATE TABLE cloud_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE plans (
    plan_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    entitlements TEXT NOT NULL,
    pricing TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    public INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);
CREATE TABLE plan_versions (
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    entitlements TEXT NOT NULL,
    pricing TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (plan_id, version)
);
CREATE TABLE plan_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    effective_at INTEGER NOT NULL,
    applied_at INTEGER,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE organizations (
    org_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    suspended_reason TEXT NOT NULL DEFAULT '',
    policy TEXT NOT NULL DEFAULT '{}',
    branding TEXT NOT NULL DEFAULT '{}',
    paused_at INTEGER,
    paused_reason TEXT NOT NULL DEFAULT '',
    paused_by TEXT NOT NULL DEFAULT '',
    rate_limit_per_minute INTEGER,
    rate_limited_until INTEGER,
    closure_requested_at INTEGER,
    closure_requested_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    deleted_at INTEGER
);

CREATE TABLE members (
    member_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(org_id),
    email TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    email_verified_at INTEGER,
    mfa_secret TEXT NOT NULL DEFAULT '',
    mfa_pending_secret TEXT NOT NULL DEFAULT '',
    mfa_enabled_at INTEGER,
    mfa_last_step INTEGER NOT NULL DEFAULT 0,
    recovery_codes TEXT NOT NULL DEFAULT '[]',
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER,
    last_sign_in_at INTEGER,
    invited_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    removed_at INTEGER,
    session_epoch INTEGER NOT NULL DEFAULT 0,
    external_id TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX members_org_email ON members (org_id, email) WHERE removed_at IS NULL;
CREATE INDEX members_org ON members (org_id, role, status);
CREATE INDEX members_email ON members (email);

CREATE TABLE invites (
    invite_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(org_id),
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    coach_id TEXT NOT NULL DEFAULT '',
    coach_scope TEXT NOT NULL DEFAULT '',
    invited_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    accepted_at INTEGER,
    accepted_member_id TEXT NOT NULL DEFAULT '',
    revoked_at INTEGER,
    resent_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX invites_org ON invites (org_id, email);

CREATE TABLE email_tokens (
    token_hash TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
CREATE INDEX email_tokens_member ON email_tokens (org_id, member_id, kind);

CREATE TABLE operators (
    operator_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'support',
    status TEXT NOT NULL DEFAULT 'active',
    mfa_secret TEXT NOT NULL DEFAULT '',
    mfa_pending_secret TEXT NOT NULL DEFAULT '',
    mfa_enabled_at INTEGER,
    mfa_last_step INTEGER NOT NULL DEFAULT 0,
    recovery_codes TEXT NOT NULL DEFAULT '[]',
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER,
    last_sign_in_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    org_id TEXT NOT NULL DEFAULT '',
    member_id TEXT NOT NULL DEFAULT '',
    operator_id TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0,
    mfa_verified INTEGER NOT NULL DEFAULT 0,
    auth_method TEXT NOT NULL DEFAULT 'password',
    support_access_id TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX sessions_member ON sessions (org_id, member_id);
CREATE INDEX sessions_operator ON sessions (operator_id);

CREATE TABLE workspaces (
    candidate_id TEXT PRIMARY KEY REFERENCES members(member_id),
    org_id TEXT NOT NULL REFERENCES organizations(org_id),
    data_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    stage TEXT NOT NULL DEFAULT 'onboarding',
    stage_override TEXT NOT NULL DEFAULT '',
    stage_changed_at INTEGER NOT NULL,
    paused_at INTEGER,
    paused_reason TEXT NOT NULL DEFAULT '',
    paused_by TEXT NOT NULL DEFAULT '',
    linkedin_port INTEGER,
    login_pid INTEGER,
    worker_pid INTEGER,
    linkedin_opt_in_at INTEGER,
    easy_apply_opt_in_at INTEGER,
    onboarding TEXT NOT NULL DEFAULT '{}',
    snapshot TEXT NOT NULL DEFAULT '{}',
    snapshot_at INTEGER,
    next_due_at INTEGER,
    last_activity_at INTEGER,
    created_at INTEGER NOT NULL,
    exported_at INTEGER,
    deleted_at INTEGER
);
CREATE INDEX workspaces_org ON workspaces (org_id, status);
CREATE TABLE candidate_stage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE INDEX stage_log_org ON candidate_stage_log (org_id, at);

CREATE TABLE coach_assignments (
    org_id TEXT NOT NULL REFERENCES organizations(org_id),
    coach_id TEXT NOT NULL REFERENCES members(member_id),
    candidate_id TEXT NOT NULL REFERENCES members(member_id),
    scope TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (org_id, coach_id, candidate_id)
);
CREATE INDEX assignments_candidate ON coach_assignments (org_id, candidate_id);

CREATE TABLE consents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    granted_at INTEGER NOT NULL,
    revoked_at INTEGER,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX consents_candidate ON consents (org_id, candidate_id, kind);

CREATE TABLE subscriptions (
    org_id TEXT PRIMARY KEY REFERENCES organizations(org_id),
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    candidate_seats INTEGER NOT NULL DEFAULT 0,
    period_start INTEGER,
    period_end INTEGER,
    trial_end INTEGER,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    cancelled_at INTEGER,
    pending_plan_id TEXT NOT NULL DEFAULT '',
    pending_seats INTEGER,
    pending_effective_at INTEGER,
    billing_provider TEXT NOT NULL DEFAULT '',
    billing_customer_id TEXT NOT NULL DEFAULT '',
    billing_subscription_id TEXT NOT NULL DEFAULT '',
    past_due_since INTEGER,
    read_only_at INTEGER,
    deletion_scheduled_at INTEGER,
    deletion_notice_at INTEGER,
    export_until INTEGER,
    overrides TEXT NOT NULL DEFAULT '{}',
    overage_enabled INTEGER NOT NULL DEFAULT 0,
    over_limits TEXT NOT NULL DEFAULT '[]',
    updated_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX subscriptions_status ON subscriptions (status);
CREATE TABLE subscription_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    trigger_type TEXT NOT NULL,
    trigger_id TEXT NOT NULL DEFAULT '',
    at INTEGER NOT NULL
);
CREATE INDEX subscription_events_org ON subscription_events (org_id, at);

CREATE TABLE billing_events (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    type TEXT NOT NULL,
    org_id TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    processed_at INTEGER,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    provider_invoice_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'subscription',
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    status TEXT NOT NULL,
    period_start INTEGER,
    period_end INTEGER,
    hosted_url TEXT NOT NULL DEFAULT '',
    lines TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    paid_at INTEGER
);
CREATE INDEX invoices_org ON invoices (org_id, created_at);
CREATE TABLE billing_credits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    applied_invoice_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX billing_credits_org ON billing_credits (org_id);

CREATE TABLE usage_counters (
    org_id TEXT NOT NULL,
    period TEXT NOT NULL,
    metric TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, period, metric)
);
CREATE TABLE usage_daily (
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    day TEXT NOT NULL,
    metric TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, candidate_id, day, metric)
);

CREATE TABLE audit_logs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    org_id TEXT NOT NULL,
    at INTEGER NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    actor_label TEXT NOT NULL DEFAULT '',
    actor_role TEXT NOT NULL DEFAULT '',
    candidate_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'user',
    correlation_id TEXT NOT NULL DEFAULT '',
    prev_hash TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX audit_org ON audit_logs (org_id, seq);
CREATE INDEX audit_candidate ON audit_logs (org_id, candidate_id, seq);
CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'the audit log is append-only');
END;
CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON audit_logs
WHEN COALESCE((SELECT value FROM cloud_kv WHERE key = 'audit_maintenance'), '0') <> '1'
BEGIN
    SELECT RAISE(ABORT, 'the audit log is append-only');
END;

CREATE TABLE platform_audit_logs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    at INTEGER NOT NULL,
    operator_id TEXT NOT NULL DEFAULT '',
    operator_label TEXT NOT NULL DEFAULT '',
    org_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'operator',
    correlation_id TEXT NOT NULL DEFAULT '',
    prev_hash TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX platform_audit_org ON platform_audit_logs (org_id, seq);
CREATE TRIGGER platform_audit_no_update BEFORE UPDATE ON platform_audit_logs
BEGIN
    SELECT RAISE(ABORT, 'the platform audit log is append-only');
END;
CREATE TRIGGER platform_audit_no_delete BEFORE DELETE ON platform_audit_logs
WHEN COALESCE((SELECT value FROM cloud_kv WHERE key = 'audit_maintenance'), '0') <> '1'
BEGIN
    SELECT RAISE(ABORT, 'the platform audit log is append-only');
END;

CREATE TABLE audit_stubs (
    org_id TEXT PRIMARY KEY,
    org_name_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    deleted_at INTEGER NOT NULL,
    retention_until INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    last_hash TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE feature_flags (
    flag TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    percent INTEGER NOT NULL DEFAULT 100,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (flag, scope_type, scope_id)
);

CREATE TABLE support_access (
    access_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'auditor',
    status TEXT NOT NULL DEFAULT 'requested',
    requested_at INTEGER NOT NULL,
    decided_at INTEGER,
    decided_by TEXT NOT NULL DEFAULT '',
    started_at INTEGER,
    expires_at INTEGER,
    ended_at INTEGER,
    ended_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX support_access_org ON support_access (org_id, status);

CREATE TABLE announcements (
    announcement_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL DEFAULT 'all',
    scope_id TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    starts_at INTEGER NOT NULL,
    ends_at INTEGER,
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    cancelled_at INTEGER
);

CREATE TABLE sso_configs (
    org_id TEXT PRIMARY KEY REFERENCES organizations(org_id),
    protocol TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enforced INTEGER NOT NULL DEFAULT 0,
    domains TEXT NOT NULL DEFAULT '[]',
    group_role_map TEXT NOT NULL DEFAULT '{}',
    default_role TEXT NOT NULL DEFAULT 'candidate',
    updated_at INTEGER NOT NULL
);
CREATE TABLE sso_identities (
    org_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    subject TEXT NOT NULL,
    member_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (org_id, protocol, subject)
);
CREATE TABLE sso_states (
    state TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    nonce TEXT NOT NULL DEFAULT '',
    verifier TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE scim_tokens (
    token_hash TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    revoked_at INTEGER
);

CREATE TABLE org_keys (
    org_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    wrapped_key BLOB NOT NULL,
    created_at INTEGER NOT NULL,
    retired_at INTEGER,
    PRIMARY KEY (org_id, version)
);
CREATE TABLE org_secrets (
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    key_version INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (org_id, name)
);

CREATE TABLE mail_outbox (
    mail_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL DEFAULT '',
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'captured',
    error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);
CREATE INDEX mail_outbox_org ON mail_outbox (org_id, created_at);

CREATE TABLE org_notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    read_at INTEGER
);
CREATE INDEX org_notices_org ON org_notices (org_id, created_at);

CREATE TABLE queue_jobs (
    job_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 5,
    run_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id TEXT NOT NULL DEFAULT '',
    lease_until INTEGER,
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);
CREATE INDEX queue_ready ON queue_jobs (status, run_at);
CREATE INDEX queue_org ON queue_jobs (org_id, status);
CREATE INDEX queue_dedupe ON queue_jobs (org_id, dedupe_key, status);
CREATE TABLE worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT '',
    capacity INTEGER NOT NULL DEFAULT 1,
    active_jobs INTEGER NOT NULL DEFAULT 0,
    started_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);
CREATE TABLE run_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL DEFAULT '',
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    ats TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error_class TEXT NOT NULL DEFAULT ''
);
CREATE INDEX run_outcomes_at ON run_outcomes (at);
CREATE INDEX run_outcomes_org ON run_outcomes (org_id, at);

CREATE TABLE blocked_sources (
    source TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    blocked_by TEXT NOT NULL DEFAULT '',
    blocked_at INTEGER NOT NULL
);
CREATE TABLE data_subject_requests (
    request_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    result TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE remote_login_sessions (
    token_hash TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
-- Same shape as aira.airaapi.store's index; ``user_id`` holds the organization id here.
CREATE TABLE airaapi_key_index (
    key_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL
);
CREATE INDEX airaapi_key_index_org ON airaapi_key_index (user_id);
"""

_V2 = """
CREATE TABLE departed_candidates (
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    removed_at INTEGER NOT NULL,
    data_policy TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (org_id, candidate_id)
);
CREATE TABLE exports (
    export_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    recipient TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    downloaded_at INTEGER
);
CREATE INDEX exports_org ON exports (org_id, created_at);
"""

_V3 = """
ALTER TABLE workspaces ADD COLUMN linkedin_host TEXT NOT NULL DEFAULT '';
ALTER TABLE worker_heartbeats ADD COLUMN agent_url TEXT NOT NULL DEFAULT '';
ALTER TABLE queue_jobs ADD COLUMN host TEXT NOT NULL DEFAULT '';
"""

# (version, name, SQL script)
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial", _V1),
    (2, "departures_and_exports", _V2),
    (3, "worker_agents", _V3),
]
