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
    billing_provider: str = field(
        default_factory=lambda: _env("AIRA_CLOUD_BILLING", "standin")
    )
    stripe_secret_key: str = field(default_factory=lambda: _env("STRIPE_SECRET_KEY"))
    stripe_webhook_secret: str = field(
        default_factory=lambda: _env("STRIPE_WEBHOOK_SECRET")
    )
    stripe_api_base: str = field(
        default_factory=lambda: _env("STRIPE_API_BASE", "https://api.stripe.com")
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
    # a local installation of the apps (the single-user build) that this account can open
    local_app_url: str = field(default_factory=lambda: _env("AIRA_CLOUD_LOCAL_APP_URL"))
    # shared with that installation as AIRA_ACCOUNT_SECRET: signs launch-token validation (32+ characters)
    launch_secret: str = field(default_factory=lambda: _env("AIRA_CLOUD_LAUNCH_SECRET"))
    launch_ttl: int = 60
    # the sign-in broker (`python -m aira_cloud broker`): streams the candidate's private browser to them
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

    @property
    def db_path(self) -> Path:
        return self.data_root / "control.sqlite3"

    @property
    def tenants_root(self) -> Path:
        return self.data_root / "tenants"

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.lower().startswith("https://")

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
