"""Roles, permissions and the single ``require(permission, scope)`` guard.

The matrix below is the one source of truth for who may do what. Routes and
background jobs call :meth:`Authorizer.require`; the UI only hides what the
API would refuse.

Candidate-level permissions take a ``candidate_id`` scope:

* the candidate holds all of them for their own workspace;
* a coach holds them for an assigned candidate up to the assignment scope
  (``view`` < ``act`` < ``approve``), capped by what the plan allows;
* owners and admins may read candidates and documents but never edit or submit;
* auditors read everything and change nothing.

An unknown candidate, or one in another organization, is a 404, never a 403, so
identifiers cannot be probed across tenants.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from aira_cloud.db import ControlDB, one
from aira_cloud.errors import Forbidden, NotFound

OWNER, ADMIN, COACH, CANDIDATE, BILLING, AUDITOR = (
    "owner",
    "admin",
    "coach",
    "candidate",
    "billing",
    "auditor",
)
ORG_ROLES = (OWNER, ADMIN, COACH, CANDIDATE, BILLING, AUDITOR)
STAFF_ROLES = (OWNER, ADMIN, COACH, BILLING, AUDITOR)
OPERATOR_SUPPORT, OPERATOR_ADMIN = "support", "platform_admin"
OPERATOR_ROLES = (OPERATOR_SUPPORT, OPERATOR_ADMIN)

SCOPE_LEVELS = {"view": 1, "act": 2, "approve": 3}
COACH_SCOPES = tuple(SCOPE_LEVELS)

# Permissions that sit at the organization level (no candidate scope).
ORG_PERMISSIONS: dict[str, frozenset[str]] = {
    OWNER: frozenset(
        {
            "org.settings",
            "billing.manage",
            "usage.read",
            "members.manage",
            "members.manage_owners",
            "coaches.assign",
            "automation.pause.org",
            "integrations.manage",
            "integrations.read",
            "dashboard.read",
            "export.org",
            "audit.read",
            "support.approve",
            "org.close",
            "candidates.list",
            "candidates.manage",
            "sso.manage",
            "suite.read",
            "suite.manage",
        }
    ),
    ADMIN: frozenset(
        {
            "org.settings",
            "usage.read",
            "members.manage",
            "coaches.assign",
            "automation.pause.org",
            "integrations.manage",
            "integrations.read",
            "dashboard.read",
            "export.org",
            "audit.read",
            "candidates.list",
            "candidates.manage",
            "sso.manage",
            "suite.read",
            "suite.manage",
        }
    ),
    # Industry apps (aira/suite) are the organization's: a coach works in them as a team member.
    COACH: frozenset(
        {"dashboard.read", "candidates.list", "suite.read", "suite.manage"}
    ),
    CANDIDATE: frozenset({"dashboard.read"}),
    BILLING: frozenset({"billing.manage", "usage.read"}),
    AUDITOR: frozenset(
        {
            "usage.read",
            "dashboard.read",
            "export.org",
            "audit.read",
            "candidates.list",
            "integrations.read",
            "suite.read",
        }
    ),
}

# Life apps (aira/suite: Family, Health, Learn, Play, Planner) are for every member, whatever their role: the
# data is each person's own (or their family's), and the apps scope it to the person.
LIFE_PERMISSIONS = frozenset({"life.read", "life.manage"})
ORG_PERMISSIONS = {
    role: permissions | LIFE_PERMISSIONS
    for role, permissions in ORG_PERMISSIONS.items()
}

# Candidate-level permissions by the scope level that unlocks them for a coach.
CANDIDATE_PERMISSION_LEVEL = {
    "candidates.read": "view",
    "documents.read": "view",
    "dashboard.candidate": "view",
    "candidates.write": "act",
    "search.run": "act",
    "leads.read": "act",  # a coach with view scope sees progress, not the third-party contacts a candidate collects
    "leads.write": "act",
    "held.answer": "act",
    "automation.pause.candidate": "act",
    "applications.submit": "approve",
}
# Held only by the candidate, in their own workspace.
CANDIDATE_ONLY = frozenset(
    {
        "linkedin.manage",
        "export.own",
        "sensitive.read",
        "sensitive.write",
        "consent.manage",
        "messages.read",
        "messages.send",
        # Sending email from the candidate's own mail account, approving what goes out, changing the sender
        # identity or the do-not-contact list: a coach drafts and edits, only the candidate sends.
        "leads.outreach",
    }
)
# Candidate-level permissions an owner or admin holds (read-only, plus pausing).
ADMIN_CANDIDATE = frozenset(
    {
        "candidates.read",
        "documents.read",
        "dashboard.candidate",
        "automation.pause.candidate",
    }
)
# Staff who may keep a workspace of their own and use the apps themselves (a personal workspace). Inside it they
# are its candidate; nobody else in the organization can open it.
PERSONAL_ROLES = (OWNER, ADMIN)
AUDITOR_CANDIDATE = frozenset(
    {"candidates.read", "documents.read", "dashboard.candidate"}
)
# What a support grant never includes, whatever role it carries.
NEVER_VIA_SUPPORT = frozenset(
    {
        "billing.manage",
        "support.approve",
        "org.close",
        "linkedin.manage",
        "sensitive.read",
        "sensitive.write",
        "consent.manage",
        "members.manage_owners",
        "export.own",
        "messages.read",
        "messages.send",
        "sso.manage",
        # members' personal life data (health, family chat, budget) is never shown to platform staff
        "life.read",
        "life.manage",
        # anything that can leave access behind once the grant ends: new members, keys, MFA resets
        "members.manage",
        "integrations.manage",
        "candidates.manage",
    }
)

PLATFORM_PERMISSIONS: dict[str, frozenset[str]] = {
    OPERATOR_SUPPORT: frozenset(
        {
            "platform.tenants.read",
            "platform.support.request",
            "platform.announcements.manage",
            "platform.health.read",
            "platform.audit.read",
            "platform.moderation.manage",
        }
    ),
    OPERATOR_ADMIN: frozenset(
        {
            "platform.tenants.read",
            "platform.tenants.manage",
            "platform.plans.manage",
            "platform.flags.manage",
            "platform.billing.manage",
            "platform.support.request",
            "platform.announcements.manage",
            "platform.health.read",
            "platform.audit.read",
            "platform.abuse.manage",
            "platform.moderation.manage",
            "platform.operators.manage",
        }
    ),
}


@dataclass
class Principal:
    kind: str  # "member" | "operator"
    session_id: str = ""
    org_id: str = ""
    member_id: str = ""
    role: str = ""
    email: str = ""
    name: str = ""
    operator_id: str = ""
    operator_role: str = ""
    mfa_verified: bool = False
    mfa_enabled: bool = False
    support_access_id: str = ""
    auth_method: str = "password"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def personal(self) -> bool:
        """Acting in their own personal workspace (an owner or admin using the apps themselves)."""
        return bool(self.extra.get("personal"))

    @property
    def staff_role(self) -> str:
        """The organization role, also while the person acts as their personal workspace's candidate."""
        return str(self.extra.get("staff_role") or self.role)

    def as_own_candidate(self) -> Principal:
        """The same person as the candidate of their personal workspace (for that request only)."""
        return replace(
            self,
            role=CANDIDATE,
            extra={**self.extra, "personal": True, "staff_role": self.role},
        )

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"

    @property
    def via_support(self) -> bool:
        return self.is_operator and bool(self.support_access_id)

    @property
    def actor_id(self) -> str:
        return self.operator_id if self.is_operator else self.member_id

    @property
    def label(self) -> str:
        if self.is_operator:
            return f"operator:{self.email}"
        return f"{self.staff_role}:{self.email}"


class Authorizer:
    def __init__(
        self,
        db: ControlDB,
        coach_cap: Callable[[str], str] | None = None,
        policy: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.db = db
        self._coach_cap = coach_cap or (lambda org_id: "approve")
        self._policy = policy or (lambda org_id: {})

    # -- lookups ---------------------------------------------------------------

    def candidate(self, org_id: str, candidate_id: str) -> dict[str, Any]:
        """The candidate's member row, or NotFound (also for a candidate of another organization)."""
        with self.db.read() as connection:
            row = one(
                connection,
                """SELECT m.member_id, m.org_id, m.email, m.name, m.status, m.role, w.status AS workspace_status
                FROM members m JOIN workspaces w ON w.candidate_id = m.member_id
                WHERE m.member_id = ? AND m.org_id = ? AND m.role IN ('candidate', ?, ?) AND m.removed_at IS NULL""",
                (candidate_id, org_id, *PERSONAL_ROLES),
            )
        if row is None:
            raise NotFound("Candidate not found.")
        return row

    def is_personal(self, org_id: str, candidate_id: str) -> bool:
        """Is this workspace a staff member's own (an owner or admin), not a candidate's?"""
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT role FROM members WHERE member_id = ? AND org_id = ?",
                (candidate_id, org_id),
            )
        return row is not None and row["role"] != CANDIDATE

    def assignment_scope(
        self, org_id: str, coach_id: str, candidate_id: str
    ) -> str | None:
        """The coach's effective scope for the candidate (assignment capped by the plan), or None."""
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT scope FROM coach_assignments WHERE org_id = ? AND coach_id = ? AND candidate_id = ?",
                (org_id, coach_id, candidate_id),
            )
        if row is None:
            return None
        cap = self._coach_cap(org_id)
        scope = row["scope"]
        return scope if SCOPE_LEVELS.get(scope, 0) <= SCOPE_LEVELS.get(cap, 1) else cap

    def assigned_candidates(self, org_id: str, coach_id: str) -> list[str]:
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT candidate_id FROM coach_assignments WHERE org_id = ? AND coach_id = ?",
                (org_id, coach_id),
            ).fetchall()
        return [row["candidate_id"] for row in rows]

    # -- decisions -------------------------------------------------------------

    def can(
        self, principal: Principal, permission: str, *, candidate_id: str | None = None
    ) -> bool:
        if principal.is_operator and not principal.support_access_id:
            return permission in PLATFORM_PERMISSIONS.get(
                principal.operator_role, frozenset()
            )
        if permission.startswith("platform."):
            return (
                permission
                in PLATFORM_PERMISSIONS.get(principal.operator_role, frozenset())
                if principal.is_operator
                else False
            )
        if principal.via_support and permission in NEVER_VIA_SUPPORT:
            return False
        role = principal.role
        if candidate_id is None:
            return permission in ORG_PERMISSIONS.get(role, frozenset())
        return self._can_candidate(principal, role, permission, candidate_id)

    def _can_candidate(
        self, principal: Principal, role: str, permission: str, candidate_id: str
    ) -> bool:
        if role == CANDIDATE:
            if principal.member_id != candidate_id:
                return False
            return (
                permission in CANDIDATE_PERMISSION_LEVEL or permission in CANDIDATE_ONLY
            )
        if role in (OWNER, ADMIN, AUDITOR) and self.is_personal(
            principal.org_id, candidate_id
        ):
            return False  # someone else's personal workspace (their own goes through as_own_candidate)
        if role in (OWNER, ADMIN):
            return permission in ADMIN_CANDIDATE
        if role == AUDITOR:
            return permission in AUDITOR_CANDIDATE
        if role == COACH:
            if permission in CANDIDATE_ONLY:
                return self._coach_extra(principal, permission, candidate_id)
            level = CANDIDATE_PERMISSION_LEVEL.get(permission)
            if level is None:
                return False
            scope = self.assignment_scope(
                principal.org_id, principal.member_id, candidate_id
            )
            return scope is not None and SCOPE_LEVELS[scope] >= SCOPE_LEVELS[level]
        return False

    def _coach_extra(
        self, principal: Principal, permission: str, candidate_id: str
    ) -> bool:
        """Messages are the candidate's own unless the organization policy lets an assigned coach in."""
        if permission not in {"messages.read", "messages.send"}:
            return False
        mode = str(self._policy(principal.org_id).get("coach_messages", "off"))
        scope = self.assignment_scope(
            principal.org_id, principal.member_id, candidate_id
        )
        if scope is None or mode == "off":
            return False
        if permission == "messages.read":
            return True
        return mode == "act" and SCOPE_LEVELS[scope] >= SCOPE_LEVELS["act"]

    def require(
        self, principal: Principal, permission: str, *, candidate_id: str | None = None
    ) -> None:
        if candidate_id is not None:
            # Existence first: a candidate outside the organization is a 404 for every role, and so is a
            # staff member's personal workspace for anyone but its owner.
            row = self.candidate(principal.org_id, candidate_id)
            if row["role"] != CANDIDATE and principal.member_id != candidate_id:
                raise NotFound("Candidate not found.")
        if not self.can(principal, permission, candidate_id=candidate_id):
            raise Forbidden(
                "You do not have permission to do that.", permission=permission
            )

    def can_see_sensitive(self, principal: Principal, candidate_id: str) -> bool:
        """Sensitive profile values: the candidate, or an assigned coach when policy and consent allow."""
        if principal.is_operator or principal.role == CANDIDATE:
            return principal.role == CANDIDATE and principal.member_id == candidate_id
        if principal.role != COACH:
            return False
        if not self._policy(principal.org_id).get("coach_sees_sensitive"):
            return False
        if (
            self.assignment_scope(principal.org_id, principal.member_id, candidate_id)
            is None
        ):
            return False
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT 1 AS x FROM consents WHERE org_id = ? AND candidate_id = ? AND kind = 'sensitive_access' AND revoked_at IS NULL",
                (principal.org_id, candidate_id),
            )
        return row is not None
