"""``CloudApp``: the hosted shell's services wired together.

One instance per process (web, worker or both). It owns the control-plane database and every
service that works on it; the HTTP gateway and the worker hold a reference and nothing else.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

from aira.hosting import HOOKS
from monosage_cloud import hooks
from monosage_cloud.agent import AgentClient, Verifier
from monosage_cloud.launch import LaunchTokens
from monosage_cloud.audit import AuditLog, PlatformAudit
from monosage_cloud.backup import BackupService
from monosage_cloud.billing import Billing
from monosage_cloud.config import Clock, CloudConfig
from monosage_cloud.crypto import SecretManager
from monosage_cloud.kms import KmsSettings, build_master_key
from monosage_cloud.dashboard import Dashboard
from monosage_cloud.db import open_db
from monosage_cloud.entitlements import (
    AppSettings,
    Enforcer,
    EntitlementService,
    FlagService,
    PlanCatalog,
    UsageService,
)
from monosage_cloud.gate import Gate
from monosage_cloud.identity import Identity
from monosage_cloud.jobqueue import JobQueue
from monosage_cloud.lifecycle import Lifecycle
from monosage_cloud.linkedin import LinkedInSessions
from monosage_cloud.mail import Mailer
from monosage_cloud.models import ModelGateway
from monosage_cloud.platform_admin import PlatformAdmin
from monosage_cloud.rbac import Authorizer
from monosage_cloud.scheduler import Scheduler
from monosage_cloud.sso import Sso
from monosage_cloud.storage import StorageService
from monosage_cloud.tenancy import Tenancy
from monosage_cloud.workspace import WorkspaceManager

logger = logging.getLogger("monosage_cloud")

# A node that has not reported for this long has stopped using the data directory.
HOST_GUARD_STALE_SECONDS = 90
HOST_GUARD_BEAT_SECONDS = 30


def node_id() -> str:
    """The machine this process runs on: the kernel's boot id, which every container on one node shares (so a web
    and a worker container on one Docker host are one node), else the host name."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()[:64]
    except OSError:
        return socket.gethostname()[:64]


class HostGuard:
    """Refuse to run two machines over one data directory.

    The control plane (SQLite in WAL mode) and every workspace file are SQLite: WAL keeps its shared index in
    memory-mapped files that only processes on one machine can share, so two pods on two nodes writing the same
    files over a network volume can corrupt them. Every long-running process records its node in ``cloud_kv``
    and refreshes it; a process that starts while another node has reported recently stops with an error.
    ``AIRA_CLOUD_ALLOW_SHARED_SQLITE=1`` turns this off (only for a file system that is known to be safe).
    """

    PREFIX = "sqlite_node:"

    def __init__(self, app: CloudApp) -> None:
        self.app = app
        self.node = node_id()
        self._last_beat = 0.0
        self._lock = threading.Lock()

    def others(self) -> list[str]:
        now = self.app.clock.now()
        with self.app.db.read() as connection:
            rows = connection.execute(
                "SELECT key, value FROM cloud_kv WHERE key LIKE ?", (f"{self.PREFIX}%",)
            ).fetchall()
        live = []
        for row in rows:
            key, value = row[0], row[1]
            node = str(key)[len(self.PREFIX) :]
            try:
                seen = int(value)
            except (TypeError, ValueError):
                continue
            if node != self.node and seen > now - HOST_GUARD_STALE_SECONDS:
                live.append(node)
        return live

    def claim(self) -> None:
        """At process start: stop if another node is using this data directory, else record this one."""
        others = self.others()
        if others:
            message = (
                f"Another machine ({', '.join(o[:12] for o in others)}) is using this data directory. The control "
                "plane and the workspaces are SQLite files, and two machines writing them over a shared volume can "
                "corrupt them. Run one replica (deploy/k8s runs one `serve --worker` pod on a ReadWriteOnce volume), "
                "or set AIRA_CLOUD_ALLOW_SHARED_SQLITE=1 if you are certain the file system is safe."
            )
            if not self.app.config.allow_shared_sqlite:
                logger.critical(message)
                raise SystemExit(message)
            logger.warning(message)
        self.beat(force=True)

    def beat(self, *, force: bool = False) -> None:
        """Refresh this node's record (cheap: at most every 30 seconds)."""
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_beat < HOST_GUARD_BEAT_SECONDS:
                return
            self._last_beat = now
        try:
            with self.app.db.write() as connection:
                connection.execute(
                    "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (f"{self.PREFIX}{self.node}", str(self.app.clock.now())),
                )
        except Exception:  # noqa: BLE001 - a missed beat only shortens the record
            logger.debug("Could not record this node", exc_info=True)

    def release(self) -> None:
        """At a clean stop: the next pod (perhaps on another node) may start at once."""
        try:
            with self.app.db.write() as connection:
                connection.execute(
                    "DELETE FROM cloud_kv WHERE key = ?", (f"{self.PREFIX}{self.node}",)
                )
        except Exception:  # noqa: BLE001
            logger.debug("Could not release this node", exc_info=True)


class CloudApp:
    gateway: Any  # set by Gateway(app); the control-plane routes reach its limiters through it

    def __init__(
        self,
        config: CloudConfig | None = None,
        *,
        clock: Clock | None = None,
        install_hooks: bool = True,
    ) -> None:
        self.config = config or CloudConfig()
        self.clock = clock or Clock()
        self.config.data_root.mkdir(parents=True, exist_ok=True)
        if os.environ.pop("AIRA_CLOUD_KEEP_ENV_SERPAPI", "") == "":
            # A tenant never inherits a SerpApi key from the host environment (or its .env file).
            os.environ.pop("SERPAPI_API_KEY", None)
        if os.environ.pop("AIRA_LEADS_ALLOW_LOCAL", "") == "1":
            # That switch lets the lead enrichment fetcher reach loopback and private addresses (tests and
            # demos). A hosted deployment reads customers' websites from inside your network: never.
            logging.getLogger("monosage_cloud").warning(
                "Ignoring AIRA_LEADS_ALLOW_LOCAL: the hosted build only reads public websites."
            )
        self.db = open_db(self.config)
        self.db.migrate()
        self.secrets = SecretManager(
            self.db,
            self.clock,
            build_master_key(
                KmsSettings.from_config(self.config), self.config.data_root
            ),
        )
        self.audit = AuditLog(self.db, self.clock)
        self.platform_audit = PlatformAudit(self.db, self.clock)
        self.catalog = PlanCatalog(self.db, self.clock)
        self.catalog.seed()
        self.flags = FlagService(self.db, self.clock)
        self.app_settings = AppSettings(self.db, self.clock, self.catalog)
        self.entitlements = EntitlementService(
            self.db,
            self.clock,
            self.config,
            self.catalog,
            self.flags,
            self.app_settings,
        )
        self.usage = UsageService(self.db, self.clock)
        self.enforcer = Enforcer(self.entitlements, self.usage)
        self.mail = Mailer(self.db, self.clock, self.config)
        self.workspaces = WorkspaceManager(self)
        self.storage = StorageService(self)
        self.queue = JobQueue(self)
        self.mail.queue = (
            self.queue
        )  # with SMTP, platform mail is delivered by the job queue (mail.deliver)
        self.agents = AgentClient(self)
        self.linkedin = LinkedInSessions(self)
        self.launch = LaunchTokens(self)
        from monosage_cloud.public_links import HostedPublicIndex

        self.public_index = HostedPublicIndex(self)  # public links of the industry apps
        from monosage_cloud.oauth import OAuth
        from monosage_cloud.webhooks import Webhooks

        self.oauth = OAuth(self)  # third-party apps and personal access tokens
        self.webhooks = Webhooks(self)
        self.signed_requests = Verifier(self)
        self.tenancy = Tenancy(self)
        self.authz = Authorizer(
            self.db,
            coach_cap=self._coach_cap,
            policy=lambda org_id: self.tenancy.policy(org_id),
        )
        self.mail.branding = self.tenancy.branding
        self.identity = Identity(self)
        self.billing = Billing(self)
        self.lifecycle = Lifecycle(self)
        self.backup = BackupService(self)
        self.dashboard = Dashboard(self)
        self.platform = PlatformAdmin(self)
        self.scheduler = Scheduler(self)
        self.gate = Gate(self)
        self.models = ModelGateway(self)
        self.sso = Sso(self)
        self.host_guard = HostGuard(self)
        from monosage_cloud import crypto

        crypto.set_scrypt_concurrency(self.config.scrypt_concurrency)
        if self.config.operator_email and self.config.operator_password:
            self.identity.bootstrap_operator(
                self.config.operator_email, self.config.operator_password
            )
        if install_hooks:
            hooks.install(self)

    def hosting_key_index(self, user_id: str) -> hooks.ControlKeyIndex:
        return hooks.ControlKeyIndex(self, user_id.removeprefix("org-"))

    def _coach_cap(self, org_id: str) -> str:
        try:
            return str(
                self.entitlements.resolve(org_id).limit("coach_max_scope") or "view"
            )
        except Exception:  # noqa: BLE001 - an unreadable plan means the narrowest scope
            return "view"

    def record_outcome(
        self,
        org_id: str,
        candidate_id: str,
        kind: str,
        *,
        source: str = "",
        ats: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        error_class: str = "",
    ) -> None:
        """Remember how one unit of work went: platform health and per-tenant error budgets read these."""
        try:
            with self.db.write() as connection:
                connection.execute(
                    "INSERT INTO run_outcomes (org_id, candidate_id, at, kind, source, ats, status, duration_ms, error_class) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        org_id,
                        candidate_id,
                        self.clock.now(),
                        kind[:40],
                        source[:40],
                        ats[:40],
                        status[:40],
                        duration_ms,
                        error_class[:40],
                    ),
                )
        except Exception:  # noqa: BLE001 - observability must never fail the work it observes
            logger.debug("Could not record an outcome", exc_info=True)

    def close(self) -> None:
        HOOKS.reset()
        self.db.close()
