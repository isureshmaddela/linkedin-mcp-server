"""Configuration and the injectable clock."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

DAY = 86_400


class Clock:
    """Wall-clock time in whole seconds. Tests substitute :class:`FakeClock`."""

    def now(self) -> int:
        return int(time.time())


class FakeClock(Clock):
    def __init__(self, start: int | None = None) -> None:
        self._now = int(start if start is not None else time.time())
        self._lock = threading.Lock()

    def now(self) -> int:
        with self._lock:
            return self._now

    def advance(self, seconds: int = 0, *, days: int = 0, hours: int = 0) -> int:
        with self._lock:
            self._now += int(seconds) + days * DAY + hours * 3600
            return self._now

    def set(self, value: int) -> None:
        with self._lock:
            self._now = int(value)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass
class CloudConfig:
    data_root: Path = field(
        default_factory=lambda: Path(
            _env("AIRA_CLOUD_DATA", str(Path.home() / ".aira-cloud"))
        )
    )
    host: str = field(default_factory=lambda: _env("AIRA_CLOUD_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("AIRA_CLOUD_PORT", 8780))
    base_url: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_BASE_URL", "http://127.0.0.1:8780")
    )
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower()
            for h in _env("AIRA_CLOUD_ALLOWED_HOSTS").split(",")
            if h.strip()
        )
    )
    region: str = field(default_factory=lambda: _env("AIRA_CLOUD_REGION", "local-1"))
    security_contact: str = field(
        default_factory=lambda: _env(
            "AIRA_CLOUD_SECURITY_CONTACT", "security@aira.invalid"
        )
    )
    trust_proxy: bool = field(default_factory=lambda: _bool("AIRA_CLOUD_TRUST_PROXY"))
    trusted_proxy_hops: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_TRUSTED_PROXY_HOPS", 1)
    )
    tls_cert: str = field(default_factory=lambda: _env("AIRA_CLOUD_TLS_CERT"))
    tls_key: str = field(default_factory=lambda: _env("AIRA_CLOUD_TLS_KEY"))

    # sessions and sign-in
    session_ttl: int = 7 * DAY
    operator_session_ttl: int = 12 * 3600
    invite_ttl: int = 7 * DAY
    email_token_ttl: int = DAY
    max_failed_logins: int = 8
    lockout_seconds: int = 15 * 60
    mfa_issuer: str = "Monosage Career Copilot"

    # subscription lifecycle (days)
    trial_days: int = 14
    # What a new sign-up starts on until the superadmin chooses (platform console > Plans > New sign-ups, stored in
    # the control plane): a 14-day trial of All access (every app and feature). When it ends the organization becomes
    # read-only with an export window unless a plan was bought, like any trial. Organizations that already exist keep
    # what they have. 0 days (no end) can still be chosen for a free plan from the console, on purpose.
    signup_plan: str = "all_access"
    signup_trial_days: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_SIGNUP_TRIAL_DAYS", 14)
    )
    past_due_read_only_days: int = 14
    past_due_deletion_days: int = 60
    cancelled_export_days: int = 30
    deletion_grace_days: int = 30
    trial_retention_days: int = 30
    entitlement_cache_seconds: int = 60

    # collaborators
    mail_from: str = field(
        default_factory=lambda: _env(
            "AIRA_CLOUD_MAIL_FROM", "Monosage <no-reply@aira.invalid>"
        )
    )
    smtp_url: str = field(default_factory=lambda: _env("AIRA_CLOUD_SMTP_URL"))
    # standin (development and demos only), stripe or razorpay
    billing_provider: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_BILLING", "standin")
    )
    # The stand-in activates paid plans without payment, so it only runs on a loopback http base URL unless this
    # demo override is set (a loud warning is logged when it is).
    allow_standin_billing: bool = field(
        default_factory=lambda: _bool("AIRA_CLOUD_ALLOW_STANDIN_BILLING")
    )
    stripe_secret_key: str = field(default_factory=lambda: _env("STRIPE_SECRET_KEY"))
    stripe_webhook_secret: str = field(
        default_factory=lambda: _env("STRIPE_WEBHOOK_SECRET")
    )
    stripe_api_base: str = field(
        default_factory=lambda: _env("STRIPE_API_BASE", "https://api.stripe.com")
    )
    # Every request names the API version the adapter was written for; create the webhook endpoint with the same one.
    stripe_api_version: str = field(
        default_factory=lambda: _env("STRIPE_API_VERSION", "2025-03-31.basil")
    )
    # Stripe Tax: automatic_tax and tax ID collection at checkout (needs Stripe Tax set up on the account).
    stripe_tax: bool = field(default_factory=lambda: _bool("AIRA_CLOUD_STRIPE_TAX"))
    razorpay_key_id: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_RAZORPAY_KEY_ID")
    )
    razorpay_key_secret: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_RAZORPAY_KEY_SECRET")
    )
    razorpay_webhook_secret: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_RAZORPAY_WEBHOOK_SECRET")
    )
    razorpay_api_base: str = field(
        default_factory=lambda: _env(
            "AIRA_CLOUD_RAZORPAY_API_BASE", "https://api.razorpay.com"
        )
    )
    # How many monthly cycles a Razorpay subscription is created for (Razorpay needs an end).
    razorpay_total_count: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_RAZORPAY_TOTAL_COUNT", 120)
    )
    # A fixed rate (rupees per US dollar) for amounts with no explicit INR price (overage, top-ups, custom prices).
    # Plans and the shipped app prices have explicit INR prices in plans.py; this is never a live exchange rate.
    razorpay_inr_per_usd: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_RAZORPAY_INR_PER_USD", 85)
    )
    model_provider: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_MODEL_PROVIDER", "ollama")
    )
    model_url: str = field(default_factory=lambda: _env("AIRA_CLOUD_MODEL_URL"))
    model_api_key: str = field(default_factory=lambda: _env("AIRA_CLOUD_MODEL_KEY"))
    model_name: str = field(default_factory=lambda: _env("AIRA_CLOUD_MODEL_NAME"))
    storage_backend: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_STORAGE", "local")
    )
    s3_endpoint: str = field(default_factory=lambda: _env("AIRA_CLOUD_S3_ENDPOINT"))
    s3_bucket: str = field(default_factory=lambda: _env("AIRA_CLOUD_S3_BUCKET"))
    s3_region: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_S3_REGION", "us-east-1")
    )
    s3_access_key: str = field(default_factory=lambda: _env("AIRA_CLOUD_S3_ACCESS_KEY"))
    s3_secret_key: str = field(default_factory=lambda: _env("AIRA_CLOUD_S3_SECRET_KEY"))
    pooled_serpapi_key: str = field(
        default_factory=lambda: _env("AIRA_POOLED_SERPAPI_KEY")
    )
    operator_email: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_OPERATOR_EMAIL")
    )
    operator_password: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_OPERATOR_PASSWORD")
    )
    master_key: str = field(default_factory=lambda: _env("AIRA_CLOUD_MASTER_KEY"))
    # where the master key that wraps every organization's data key lives: local | aws | vault
    kms: str = field(default_factory=lambda: _env("AIRA_CLOUD_KMS", "local"))
    kms_key_id: str = field(default_factory=lambda: _env("AIRA_CLOUD_KMS_KEY_ID"))
    kms_endpoint: str = field(default_factory=lambda: _env("AIRA_CLOUD_KMS_ENDPOINT"))
    kms_region: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_REGION", "us-east-1")
    )
    kms_access_key: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_ACCESS_KEY")
    )
    kms_secret_key: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_SECRET_KEY")
    )
    kms_session_token: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_SESSION_TOKEN")
    )
    kms_vault_addr: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_VAULT_ADDR")
    )
    kms_vault_token: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_VAULT_TOKEN")
    )
    kms_vault_mount: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_VAULT_MOUNT", "transit")
    )
    kms_vault_namespace: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_KMS_VAULT_NAMESPACE")
    )
    # how long an unwrapped organization key may stay in memory before the KMS is asked again
    kms_cache_seconds: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_KMS_CACHE_SECONDS", 300)
    )

    # worker agents (LinkedIn sessions live on the worker host that holds them)
    agent_port: int = field(default_factory=lambda: _int("AIRA_CLOUD_AGENT_PORT", 0))
    agent_host: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_AGENT_HOST", "127.0.0.1")
    )
    agent_advertise_url: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_AGENT_URL")
    )
    agent_secret: str = field(default_factory=lambda: _env("AIRA_CLOUD_AGENT_SECRET"))
    broker_secret: str = field(default_factory=lambda: _env("AIRA_CLOUD_BROKER_SECRET"))
    # where a candidate signs in to LinkedIn through a short-lived remote browser; {token} is replaced
    remote_login_url: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_REMOTE_LOGIN_URL")
    )
    remote_login_ttl: int = 600
    # LinkedIn automation in the hosted build (sign-in, LinkedIn search and job details, the inbox and replies,
    # schedules, Easy Apply, Sales Navigator): off unless the operator turns it on, for a supervised pilot only.
    # LinkedIn's User Agreement forbids automated access, and many candidates' sessions run from data-centre
    # addresses risk account restrictions (docs/multitenant.md, "LinkedIn in the hosted build"). When it is on, the
    # plan feature, the organization's policy and each candidate's own opt-in still apply.
    linkedin_automation: bool = field(
        default_factory=lambda: _bool("AIRA_CLOUD_LINKEDIN_AUTOMATION")
    )
    # a local installation of the apps (the single-user build) that this account can open
    local_app_url: str = field(default_factory=lambda: _env("AIRA_CLOUD_LOCAL_APP_URL"))
    # shared with that installation as AIRA_ACCOUNT_SECRET: signs launch-token validation (32+ characters)
    launch_secret: str = field(default_factory=lambda: _env("AIRA_CLOUD_LAUNCH_SECRET"))
    launch_ttl: int = 60
    # the sign-in broker (`python -m monosage_cloud broker`): streams the candidate's private browser to them
    broker_host: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_BROKER_HOST", "127.0.0.1")
    )
    broker_port: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_BROKER_PORT", 8790)
    )
    broker_app_url: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_BROKER_APP_URL")
    )
    broker_session_seconds: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_BROKER_SESSION_SECONDS", 600)
    )
    broker_max_sessions: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_BROKER_MAX_SESSIONS", 4)
    )
    broker_start_url: str = field(
        default_factory=lambda: _env(
            "AIRA_CLOUD_BROKER_START_URL", "https://www.linkedin.com/login"
        )
    )
    broker_allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower()
            for h in _env("AIRA_CLOUD_BROKER_ALLOWED_HOSTS").split(",")
            if h.strip()
        )
    )
    broker_signed_in_cookie: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_BROKER_SIGNED_IN_COOKIE", "li_at")
    )
    broker_signed_in_domain: str = field(
        default_factory=lambda: _env(
            "AIRA_CLOUD_BROKER_SIGNED_IN_DOMAIN", "linkedin.com"
        )
    )

    # control-plane database: sqlite (default, one file under the data directory) or postgres
    db_backend: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_DB", "sqlite").strip().lower()
    )
    database_url: str = field(default_factory=lambda: _env("AIRA_CLOUD_DATABASE_URL"))
    database_platform_url: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_DATABASE_PLATFORM_URL")
    )
    database_admin_url: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_DATABASE_ADMIN_URL")
    )
    database_pool_size: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_DATABASE_POOL_SIZE", 24)
    )

    # throughput
    ip_requests_per_minute: int = 1200
    org_requests_per_minute: int = 600
    member_requests_per_minute: int = 240
    worker_threads: int = 8
    per_org_concurrency: int = 2
    lease_seconds: int = 900
    browser_time_cap_seconds: int = 600

    # -- hosted security and operations (docs/multitenant-operations.md, "Limits, draining and budgets") --------
    # signed machine callers: per provider, OAuth client or SCIM token, never the sign-in limiter
    machine_requests_per_minute: int = 3000
    machine_failures_per_window: int = 60  # rejected credentials per address in 5 minutes
    # the HTTP tier (gateway.make_server)
    http_max_connections: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_HTTP_MAX_CONNECTIONS", 512)
    )
    http_socket_timeout: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_HTTP_TIMEOUT", 75)
    )
    http_backlog: int = 128
    http_max_body_bytes: int = 25_000_000
    http_large_body_bytes: int = 1_000_000  # bodies above this share a few slots
    http_large_body_slots: int = 8
    http_drain_seconds: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_DRAIN_SECONDS", 25)
    )
    scrypt_concurrency: int = 4  # each password check holds about 32 MB
    # sessions end after this long without a request (0: only the absolute lifetime applies)
    session_idle_seconds: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_SESSION_IDLE_HOURS", 24) * 3600
    )
    operator_session_idle_seconds: int = 3600
    # sign-up abuse checks (identity.Identity.check_signup)
    signups_per_ip_hour: int = 5
    signups_per_domain_hour: int = 20
    # operator-assisted recovery of a sole owner's second factor waits this long (the owner can cancel)
    recovery_delay_seconds: int = 72 * 3600
    # operations
    metrics_token: str = field(default_factory=lambda: _env("AIRA_CLOUD_METRICS_TOKEN"))
    backup_interval_hours: int = field(
        default_factory=lambda: _int("AIRA_CLOUD_BACKUP_HOURS", 0)
    )
    backup_dir: str = field(default_factory=lambda: _env("AIRA_CLOUD_BACKUP_DIR"))
    backup_keep: int = field(default_factory=lambda: _int("AIRA_CLOUD_BACKUP_KEEP", 7))
    mail_outbox_days: int = 30
    scheduler_idle_recheck_seconds: int = 3600
    # per-organization caps for each job kind (default: per_org_concurrency), and all kinds together
    kind_concurrency: dict[str, int] = field(default_factory=dict)
    per_org_total_concurrency: int = 6
    allow_shared_sqlite: bool = field(
        default_factory=lambda: _bool("AIRA_CLOUD_ALLOW_SHARED_SQLITE")
    )
    # model calls a month answered with the platform's own key, by plan (None: no limit); an organization's own
    # Anthropic key or endpoint is not counted. A plan's ``model_calls_per_month`` limit, when set, wins.
    model_calls_per_month: dict[str, int | None] = field(
        default_factory=lambda: {
            "trial": 300,
            "all_access": 300,
            "starter": 5_000,
            "team": 50_000,
            "enterprise": None,
        }
    )
    model_calls_default: int | None = 1_000  # plans not listed above

    @property
    def db_path(self) -> Path:
        return self.data_root / "control.sqlite3"

    @property
    def tenants_root(self) -> Path:
        return self.data_root / "tenants"

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.lower().startswith("https://")

    def standin_refusal(self) -> str:
        """Why the stand-in billing provider may not run here ('' when it may).

        It activates paid plans for free, so it runs only where nobody else can reach it: an http base URL on a
        loopback host. A public demo sets ``AIRA_CLOUD_ALLOW_STANDIN_BILLING=1`` on purpose."""
        if self.billing_provider != "standin":
            return ""
        from urllib.parse import urlsplit

        parts = urlsplit(self.base_url.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
            return ""
        return (
            f"The stand-in billing provider activates paid plans without payment and cannot run at {self.base_url}. "
            "Set AIRA_CLOUD_BILLING=stripe or razorpay, or AIRA_CLOUD_ALLOW_STANDIN_BILLING=1 for a demo."
        )

    def host_allowed(self, host_header: str) -> bool:
        host = (
            host_header.rsplit(":", 1)[0]
            if host_header.count(":") == 1
            else host_header
        )
        host = host.strip("[]").lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return True
        return host in self.allowed_hosts
