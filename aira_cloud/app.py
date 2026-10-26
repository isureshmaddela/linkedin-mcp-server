"""``CloudApp``: the hosted shell's services wired together.

One instance per process (web, worker or both). It owns the control-plane database and every
service that works on it; the HTTP gateway and the worker hold a reference and nothing else.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aira.hosting import HOOKS
from aira_cloud import hooks
from aira_cloud.agent import AgentClient, Verifier
from aira_cloud.launch import LaunchTokens
from aira_cloud.audit import AuditLog, PlatformAudit
from aira_cloud.backup import BackupService
from aira_cloud.billing import Billing
from aira_cloud.config import Clock, CloudConfig
from aira_cloud.crypto import SecretManager
from aira_cloud.kms import KmsSettings, build_master_key
from aira_cloud.dashboard import Dashboard
from aira_cloud.db import open_db
from aira_cloud.entitlements import (
    AppSettings,
    Enforcer,
    EntitlementService,
    FlagService,
    PlanCatalog,
    UsageService,
)
from aira_cloud.gate import Gate
from aira_cloud.identity import Identity
from aira_cloud.jobqueue import JobQueue
from aira_cloud.lifecycle import Lifecycle
from aira_cloud.linkedin import LinkedInSessions
from aira_cloud.mail import Mailer
from aira_cloud.models import ModelGateway
from aira_cloud.platform_admin import PlatformAdmin
from aira_cloud.rbac import Authorizer
from aira_cloud.scheduler import Scheduler
from aira_cloud.sso import Sso
from aira_cloud.storage import StorageService
from aira_cloud.tenancy import Tenancy
from aira_cloud.workspace import WorkspaceManager

logger = logging.getLogger("aira_cloud")


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
            logging.getLogger("aira_cloud").warning(
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
        self.app_settings = AppSettings(self.db, self.clock)
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
        self.agents = AgentClient(self)
        self.linkedin = LinkedInSessions(self)
        self.launch = LaunchTokens(self)
        from aira_cloud.public_links import HostedPublicIndex

        self.public_index = HostedPublicIndex(self)  # public links of the industry apps
        from aira_cloud.oauth import OAuth
        from aira_cloud.webhooks import Webhooks

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
