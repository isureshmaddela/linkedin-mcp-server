"""Organizations, members, coaches, policy, pause switches, consent and offboarding."""

from __future__ import annotations

import logging
import sqlite3
import statistics
from typing import TYPE_CHECKING, Any

from aira_cloud import crypto
from aira_cloud.db import dumps, loads, many, one
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PlanRequired,
    ValidationFailed,
)
from aira_cloud.plans import ALL_SOURCES, SOURCE_FEATURES
from aira_cloud.rbac import (
    ADMIN,
    CANDIDATE,
    CANDIDATE_ONLY,
    CANDIDATE_PERMISSION_LEVEL,
    COACH,
    COACH_SCOPES,
    ORG_ROLES,
    OWNER,
    PERSONAL_ROLES,
    SCOPE_LEVELS,
    STAFF_ROLES,
    Principal,
)

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.tenancy")

# Owners, admins and coaches take a "coach and admin" seat; billing contacts and auditors do not.
SEAT_STAFF_ROLES = (OWNER, ADMIN, COACH)
CONSENT_VERSION = "2026-10-1"
CONSENT_KINDS = ("automation", "coach_access", "sensitive_policy", "sensitive_access")
REQUIRED_CONSENTS = ("automation", "coach_access", "sensitive_policy")
CANDIDATE_DATA_POLICIES = ("candidate_keeps", "organization_keeps")

DEFAULT_POLICY: dict[str, Any] = {
    "submission_mode": "review",
    "daily_cap_per_candidate": 10,
    "allowed_sources": ["aira_jobs", "company_boards"],
    "linkedin_allowed": False,
    "easy_apply_allowed": False,
    "cover_letters": True,
    "retention_days": 365,
    "candidate_data_policy": "candidate_keeps",
    "coach_sees_sensitive": False,
    "coach_messages": "off",
    "require_mfa_staff": False,
    "review_required_regions": [],
    "use_pooled_serpapi": False,
    "legal_hold": False,
}


class Tenancy:
    def __init__(self, app: CloudApp) -> None:
        self.app = app
        self._policy_cache: dict[str, tuple[int, dict[str, Any]]] = {}

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ organizations

    def default_policy(self) -> dict[str, Any]:
        return dict(DEFAULT_POLICY)

    def org(self, org_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM organizations WHERE org_id = ? AND deleted_at IS NULL",
                (org_id,),
            )
        if row is None:
            raise NotFound("Organization not found.")
        row["policy"] = {**DEFAULT_POLICY, **loads(row["policy"], {})}
        row["branding"] = loads(row["branding"], {})
        return row

    def policy(self, org_id: str) -> dict[str, Any]:
        cached = self._policy_cache.get(org_id)
        if cached and cached[0] > self.now:
            return cached[1]
        try:
            policy = self.org(org_id)["policy"]
        except NotFound:
            return dict(DEFAULT_POLICY)
        self._policy_cache[org_id] = (self.now + 5, policy)
        return policy

    def invalidate_policy(self, org_id: str) -> None:
        self._policy_cache.pop(org_id, None)

    def branding(self, org_id: str) -> dict[str, Any]:
        try:
            org = self.org(org_id)
        except NotFound:
            return {}
        entitled = False
        try:
            entitled = self.app.entitlements.resolve(org_id).has("branding")
        except NotFound:
            pass
        data = {"name": org["name"]}
        if entitled:
            data.update(
                {"branded": True, "logo_url": org["branding"].get("logo_url", "")}
            )
        return data

    def set_branding(
        self, actor: Principal, *, logo_url: str = "", display_name: str = ""
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "org.settings")
        self.app.entitlements.require_state(actor.org_id, write=True)
        self.app.entitlements.require_feature(
            actor.org_id, "branding", label="Branding"
        )
        logo_url = str(logo_url or "").strip()
        if logo_url and not logo_url.lower().startswith("https://"):
            raise ValidationFailed("The logo must be an https:// address.")
        if len(logo_url) > 500:
            raise ValidationFailed("The logo address is too long.")
        name = str(display_name or "").strip()
        with self.db.write() as connection:
            if name:
                if not 2 <= len(name) <= 120:
                    raise ValidationFailed(
                        "The organization name must be 2 to 120 characters."
                    )
                connection.execute(
                    "UPDATE organizations SET name = ? WHERE org_id = ?",
                    (name, actor.org_id),
                )
            connection.execute(
                "UPDATE organizations SET branding = ? WHERE org_id = ?",
                # ``public``: the organization chose to show its name and logo on its sign-in page
                (dumps({"logo_url": logo_url, "public": True}), actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "org.branding",
                actor=actor,
                detail={"logo": bool(logo_url), "name": name},
                connection=connection,
            )
        return self.branding(actor.org_id)

    def update_policy(self, actor: Principal, patch: dict[str, Any]) -> dict[str, Any]:
        self.app.authz.require(actor, "org.settings")
        self.app.entitlements.require_state(actor.org_id, write=True)
        current = self.org(actor.org_id)["policy"]
        cleaned = self._validate_policy(actor.org_id, patch)
        updated = {**current, **cleaned}
        changes = {
            k: {"from": current.get(k), "to": v}
            for k, v in cleaned.items()
            if current.get(k) != v
        }
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET policy = ? WHERE org_id = ?",
                (dumps(updated), actor.org_id),
            )
            if changes:
                self.app.audit.record(
                    actor.org_id,
                    "org.policy",
                    actor=actor,
                    detail={"changes": changes},
                    connection=connection,
                )
        self.invalidate_policy(actor.org_id)
        return updated

    def _validate_policy(self, org_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        entitlements = self.app.entitlements.resolve(org_id)
        unknown = sorted(set(patch) - set(DEFAULT_POLICY))
        if unknown:
            raise ValidationFailed(f"Unknown policy setting: {', '.join(unknown)}.")
        cleaned: dict[str, Any] = {}
        ent = self.app.entitlements

        for key, value in patch.items():
            if key == "submission_mode":
                if value not in ("review", "auto"):
                    raise ValidationFailed("Submission mode must be review or auto.")
                if value == "auto":
                    ent.require_feature(
                        org_id, "automation.submit", label="Automatic submission"
                    )
                cleaned[key] = value
            elif key == "daily_cap_per_candidate":
                cap = self._int(value, key, 1, 1000)
                plan_cap = entitlements.limit("apps_per_candidate_day")
                if plan_cap is not None and cap > plan_cap:
                    raise PlanRequired(
                        f"The {entitlements.plan_id.title()} plan allows up to {plan_cap} applications per candidate per day.",
                        limit="apps_per_candidate_day",
                        limit_value=plan_cap,
                        required_plan=None,
                        current_plan=entitlements.plan_id,
                    )
                cleaned[key] = cap
            elif key == "allowed_sources":
                if not isinstance(value, list) or not all(
                    isinstance(v, str) for v in value
                ):
                    raise ValidationFailed("Allowed sources must be a list.")
                bad = [v for v in value if v not in ALL_SOURCES]
                if bad:
                    raise ValidationFailed(f"Unknown source: {', '.join(bad)}.")
                for source in value:
                    ent.require_feature(
                        org_id,
                        SOURCE_FEATURES[source],
                        label=f"The {source.replace('_', ' ')} source",
                    )
                cleaned[key] = [s for s in ALL_SOURCES if s in value]
            elif key == "linkedin_allowed":
                flag = self._bool(value, key)
                if flag:
                    ent.require_feature(org_id, "sources.linkedin", label="LinkedIn")
                cleaned[key] = flag
            elif key == "easy_apply_allowed":
                flag = self._bool(value, key)
                if flag:
                    ent.require_feature(org_id, "sources.linkedin", label="LinkedIn")
                    if self.app.flags.killed("linkedin.easy_apply"):
                        raise PlanRequired(
                            "LinkedIn Easy Apply is switched off for every organization right now.",
                            feature="linkedin.easy_apply",
                        )
                cleaned[key] = flag
            elif key == "cover_letters":
                flag = self._bool(value, key)
                if flag:
                    ent.require_feature(
                        org_id, "ai.cover_letters", label="AI cover letters"
                    )
                cleaned[key] = flag
            elif key == "retention_days":
                days = self._int(value, key, 30, 3650)
                plan_days = entitlements.limit("retention_days")
                if plan_days is not None and days > plan_days:
                    raise PlanRequired(
                        f"The {entitlements.plan_id.title()} plan keeps data for up to {plan_days} days.",
                        limit="retention_days",
                        limit_value=plan_days,
                        required_plan=None,
                        current_plan=entitlements.plan_id,
                    )
                cleaned[key] = days
            elif key == "candidate_data_policy":
                if value not in CANDIDATE_DATA_POLICIES:
                    raise ValidationFailed(
                        "Candidate data policy must be candidate_keeps or organization_keeps."
                    )
                cleaned[key] = value
            elif key == "coach_messages":
                if value not in ("off", "view", "act"):
                    raise ValidationFailed(
                        "Coach message access must be off, view or act."
                    )
                cleaned[key] = value
            elif key == "review_required_regions":
                if (
                    not isinstance(value, list)
                    or len(value) > 20
                    or not all(isinstance(v, str) and 1 <= len(v) <= 60 for v in value)
                ):
                    raise ValidationFailed(
                        "Regions must be a list of up to 20 short names."
                    )
                cleaned[key] = [v.strip() for v in value]
            elif key == "legal_hold":
                flag = self._bool(value, key)
                ent.require_feature(org_id, "legal_hold", label="Legal hold")
                cleaned[key] = flag
            elif key == "use_pooled_serpapi":
                flag = self._bool(value, key)
                if flag:
                    ent.require_feature(
                        org_id, "pooled_serpapi", label="Pooled SerpApi searches"
                    )
                cleaned[key] = flag
            else:  # coach_sees_sensitive, require_mfa_staff
                cleaned[key] = self._bool(value, key)

        merged = {**self.org(org_id)["policy"], **cleaned}
        # A source the plan or policy no longer allows cannot stay allowed.
        if "linkedin" in merged["allowed_sources"] and not merged["linkedin_allowed"]:
            if "allowed_sources" in cleaned and "linkedin_allowed" not in cleaned:
                raise ValidationFailed(
                    "Turn on 'LinkedIn allowed' before allowing the LinkedIn source."
                )
            cleaned["allowed_sources"] = [
                s for s in merged["allowed_sources"] if s != "linkedin"
            ]
        return cleaned

    @staticmethod
    def _int(value: Any, name: str, low: int, high: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValidationFailed(
                f"{name.replace('_', ' ').capitalize()} must be a whole number."
            ) from None
        if not low <= number <= high:
            raise ValidationFailed(
                f"{name.replace('_', ' ').capitalize()} must be between {low} and {high}."
            )
        return number

    @staticmethod
    def _bool(value: Any, name: str) -> bool:
        if isinstance(value, bool):
            return value
        if value in (0, 1, "0", "1", "true", "false"):
            return value in (1, "1", "true")
        raise ValidationFailed(
            f"{name.replace('_', ' ').capitalize()} must be true or false."
        )

    # ---------------------------------------------------------------------- members

    def member(self, org_id: str, member_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM members WHERE member_id = ? AND org_id = ? AND removed_at IS NULL",
                (member_id, org_id),
            )
        if row is None:
            raise NotFound("Member not found.")
        return row

    @staticmethod
    def public_member(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "member_id": row["member_id"],
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "status": row["status"],
            "email_verified": bool(row["email_verified_at"]),
            "mfa_enabled": bool(row["mfa_enabled_at"]),
            "last_sign_in_at": row["last_sign_in_at"],
            "created_at": row["created_at"],
        }

    def list_members(
        self,
        actor: Principal,
        *,
        role: str = "",
        q: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not self.app.authz.can(actor, "members.manage"):
            self.app.authz.require(actor, "candidates.list")
        clauses, params = ["org_id = ?", "removed_at IS NULL"], [actor.org_id]
        if role:
            clauses.append("role = ?")
            params.append(role)
        if q:
            clauses.append("(email LIKE ? OR name LIKE ?)")
            params.extend([f"%{q.strip()}%"] * 2)
        if actor.role == COACH:
            assigned = self.app.authz.assigned_candidates(actor.org_id, actor.member_id)
            clauses.append(
                f"(role != 'candidate' OR member_id IN ({','.join('?' * len(assigned)) or 'NULL'}))"
            )
            params.extend(assigned)
        with self.db.read() as connection:
            rows = many(
                connection,
                f"SELECT * FROM members WHERE {' AND '.join(clauses)} ORDER BY created_at LIMIT ? OFFSET ?",
                (*params, max(1, min(limit, 500)), max(0, offset)),
            )
        return [self.public_member(r) for r in rows]

    def seat_usage(
        self, org_id: str, *, exclude_invite: str = "", exclude_email: str = ""
    ) -> dict[str, int]:
        with self.db.read() as connection:
            members = many(
                connection,
                "SELECT role, COUNT(*) AS n FROM members WHERE org_id = ? AND removed_at IS NULL AND status = 'active' GROUP BY role",
                (org_id,),
            )
            invites = many(
                connection,
                """SELECT invite_id, email, role FROM invites WHERE org_id = ? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?""",
                (org_id, self.now),
            )
            personal = one(
                connection,
                """SELECT COUNT(*) AS n FROM workspaces w JOIN members m ON m.member_id = w.candidate_id
                WHERE w.org_id = ? AND m.role != 'candidate' AND m.removed_at IS NULL AND m.status = 'active'""",
                (org_id,),
            )
        by_role = {r["role"]: int(r["n"]) for r in members}
        # A personal workspace (an owner or admin using the apps themselves) takes a candidate seat.
        candidates = by_role.get(CANDIDATE, 0) + int(personal["n"] if personal else 0)
        staff = sum(n for role, n in by_role.items() if role in SEAT_STAFF_ROLES)
        for invite in invites:
            if invite["invite_id"] == exclude_invite or (
                exclude_email and invite["email"] == exclude_email
            ):
                continue
            if invite["role"] == CANDIDATE:
                candidates += 1
            elif invite["role"] in SEAT_STAFF_ROLES:
                staff += 1
        return {"candidates": candidates, "staff": staff}

    def require_seat(
        self,
        org_id: str,
        role: str,
        *,
        replacing_invite_email: str = "",
        accepting_invite: str = "",
    ) -> None:
        if role != CANDIDATE and role not in SEAT_STAFF_ROLES:
            return  # billing contacts and auditors do not take a seat
        entitlements = self.app.entitlements.resolve(org_id, fresh=True)
        used = self.seat_usage(
            org_id,
            exclude_invite=accepting_invite,
            exclude_email=replacing_invite_email,
        )
        kind, limit_name, label = (
            ("candidates", "candidate_seats", "candidate")
            if role == CANDIDATE
            else ("staff", "staff_seats", "coach and admin")
        )
        limit = entitlements.limit(limit_name)
        if limit is not None and used[kind] >= limit:
            needed = self.app.entitlements.catalog.current()
            from aira_cloud.plans import plan_for_limit

            plan = plan_for_limit(needed, limit_name, limit + 1)
            raise PlanRequired(
                f"All {limit} {label} seats on the {entitlements.plan_id.title()} plan are in use."
                + (
                    f" Upgrade to {plan.title()} or add seats."
                    if plan and plan != entitlements.plan_id
                    else " Add seats to invite more people."
                ),
                limit=limit_name,
                used=used[kind],
                limit_value=limit,
                required_plan=plan if plan != entitlements.plan_id else None,
                current_plan=entitlements.plan_id,
            )

    def change_role(
        self, actor: Principal, member_id: str, role: str
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "members.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        if role not in ORG_ROLES:
            raise ValidationFailed("Choose a valid role.")
        target = self.member(actor.org_id, member_id)
        if target["role"] == role:
            return self.public_member(target)
        if OWNER in (target["role"], role):
            self.app.authz.require(actor, "members.manage_owners")
        if CANDIDATE in (target["role"], role):
            raise ValidationFailed(
                "A candidate cannot change role. Remove the candidate and invite them again with the new role."
            )
        if target["role"] == OWNER and self._owner_count(actor.org_id) <= 1:
            raise Conflict(
                "The organization needs at least one owner. Make someone else an owner first."
            )
        if role not in PERSONAL_ROLES and self.has_personal_workspace(
            actor.org_id, member_id
        ):
            raise Conflict(
                "This person has their own workspace, which only owners and admins can keep. They end it first "
                "(account menu, End my workspace), or remove them from the organization."
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET role = ? WHERE member_id = ? AND org_id = ?",
                (role, member_id, actor.org_id),
            )
            if target["role"] == COACH:
                connection.execute(
                    "DELETE FROM coach_assignments WHERE org_id = ? AND coach_id = ?",
                    (actor.org_id, member_id),
                )
            self.app.identity.end_all_sessions(actor.org_id, member_id, connection)
            self.app.audit.record(
                actor.org_id,
                "member.role_change",
                actor=actor,
                target_type="member",
                target_id=member_id,
                detail={"from": target["role"], "to": role},
                connection=connection,
            )
        self.app.lifecycle.recompute_over_limits(actor.org_id)
        return self.public_member(self.member(actor.org_id, member_id))

    # ------------------------------------------------------------------ personal workspaces

    def has_personal_workspace(self, org_id: str, member_id: str) -> bool:
        with self.db.read() as connection:
            row = one(
                connection,
                """SELECT 1 AS x FROM workspaces w JOIN members m ON m.member_id = w.candidate_id
                WHERE w.org_id = ? AND w.candidate_id = ? AND m.role != 'candidate'""",
                (org_id, member_id),
            )
        return row is not None

    def personal_workspace(self, principal: Principal) -> dict[str, Any]:
        """Whether this owner or admin may keep a workspace of their own, and its state."""
        available = principal.staff_role in PERSONAL_ROLES and not principal.is_operator
        state: dict[str, Any] = {"available": available, "exists": False}
        if available and self.has_personal_workspace(
            principal.org_id, principal.member_id
        ):
            workspace = self.app.workspaces.row(principal.org_id, principal.member_id)
            state.update(
                {
                    "exists": True,
                    "stage": workspace["stage"],
                    "status": workspace["status"],
                    "paused": bool(workspace["paused_at"]),
                    "linkedin_opted_in": bool(workspace["linkedin_opt_in_at"]),
                    # Inside it the person holds a candidate's permissions over their own data.
                    "candidate_permissions": sorted(
                        set(CANDIDATE_PERMISSION_LEVEL) | CANDIDATE_ONLY
                    ),
                }
            )
        return state

    def start_personal_workspace(self, principal: Principal) -> dict[str, Any]:
        """An owner or admin starts using the apps themselves: a workspace of their own, one candidate seat.

        Starting it is the person's agreement to automation on their own behalf and to how sensitive answers are
        handled (the consents a candidate gives at onboarding); no coach can be given access to it.
        """
        if principal.is_operator or principal.staff_role not in PERSONAL_ROLES:
            raise Forbidden(
                "Only owners and admins can keep a workspace of their own.",
                code="personal_not_allowed",
            )
        org_id, member_id = principal.org_id, principal.member_id
        if self.has_personal_workspace(org_id, member_id):
            return self.personal_workspace(principal)
        self.app.entitlements.require_state(org_id, write=True)
        self.require_seat(org_id, CANDIDATE)
        with self.db.write() as connection:
            self.app.workspaces.create(org_id, member_id, connection=connection)
            for kind in ("automation", "sensitive_policy"):
                connection.execute(
                    "INSERT INTO consents (org_id, candidate_id, kind, version, granted_at, detail) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        org_id,
                        member_id,
                        kind,
                        CONSENT_VERSION,
                        self.now,
                        dumps({"personal": True}),
                    ),
                )
            self.app.audit.record(
                org_id,
                "workspace.personal_start",
                actor=principal,
                candidate_id=member_id,
                target_type="member",
                target_id=member_id,
                connection=connection,
            )
        self.app.lifecycle.recompute_over_limits(org_id)
        return self.personal_workspace(principal)

    def end_personal_workspace(self, principal: Principal) -> dict[str, Any]:
        """The owner or admin ends their own workspace: its data is deleted and the seat is free again."""
        org_id, member_id = principal.org_id, principal.member_id
        if not self.has_personal_workspace(org_id, member_id):
            raise NotFound("You do not have a workspace of your own.")
        self.app.entitlements.require_state(org_id, write=True)
        outcome = self._close_personal_workspace(org_id, member_id)
        self.app.audit.record(
            org_id,
            "workspace.personal_end",
            actor=principal,
            candidate_id=member_id,
            target_type="member",
            target_id=member_id,
            detail={"outcome": outcome},
        )
        self.app.lifecycle.recompute_over_limits(org_id)
        return {**self.personal_workspace(principal), "outcome": outcome}

    def _close_personal_workspace(self, org_id: str, member_id: str) -> str:
        self.app.workspaces.stop_automation(org_id, member_id)
        if self.app.lifecycle.on_legal_hold(org_id):
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE workspaces SET status = 'archived' WHERE org_id = ? AND candidate_id = ?",
                    (org_id, member_id),
                )
            return "archived"  # nothing is deleted while a legal hold is in place
        self.app.workspaces.delete(org_id, member_id)
        with self.db.write() as connection:
            connection.execute(
                "DELETE FROM consents WHERE org_id = ? AND candidate_id = ?",
                (org_id, member_id),
            )
        return "deleted"

    def _owner_count(self, org_id: str) -> int:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT COUNT(*) AS n FROM members WHERE org_id = ? AND role = 'owner' AND removed_at IS NULL AND status = 'active'",
                (org_id,),
            )
        return int(row["n"]) if row else 0

    def update_name(self, principal: Principal, name: str) -> None:
        name = str(name or "").strip()
        if len(name) > 120:
            raise ValidationFailed("The name is too long.")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET name = ? WHERE member_id = ? AND org_id = ?",
                (name, principal.member_id, principal.org_id),
            )

    def remove_member(self, actor: Principal, member_id: str) -> dict[str, Any]:
        self.app.authz.require(actor, "members.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        target = self.member(actor.org_id, member_id)
        if target["role"] == OWNER:
            self.app.authz.require(actor, "members.manage_owners")
            if self._owner_count(actor.org_id) <= 1:
                raise Conflict(
                    "Removing the last owner is refused until another owner exists."
                )
        result: dict[str, Any] = {"member_id": member_id, "role": target["role"]}
        if target["role"] == CANDIDATE:
            result.update(self._offboard_candidate(actor, target))
        elif self.has_personal_workspace(actor.org_id, member_id):
            result["personal_workspace"] = self._close_personal_workspace(
                actor.org_id, member_id
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET status = 'removed', removed_at = ?, password_hash = '' WHERE member_id = ? AND org_id = ?",
                (self.now, member_id, actor.org_id),
            )
            connection.execute(
                "DELETE FROM coach_assignments WHERE org_id = ? AND (coach_id = ? OR candidate_id = ?)",
                (actor.org_id, member_id, member_id),
            )
            connection.execute(
                "DELETE FROM sso_identities WHERE org_id = ? AND member_id = ?",
                (actor.org_id, member_id),
            )
            connection.execute(
                "UPDATE invites SET revoked_at = ? WHERE org_id = ? AND email = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                (self.now, actor.org_id, target["email"]),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND member_id = ? AND revoked_at IS NULL",
                (self.now, actor.org_id, member_id),
            )
            self.app.audit.record(
                actor.org_id,
                "member.remove",
                actor=actor,
                target_type="member",
                target_id=member_id,
                candidate_id=member_id if target["role"] == CANDIDATE else "",
                detail={
                    "role": target["role"],
                    **{
                        k: v
                        for k, v in result.items()
                        if k in ("data_policy", "export_id")
                    },
                },
                connection=connection,
            )
        self.app.secrets.delete(actor.org_id, f"mfa:{member_id}")
        from aira_cloud.hooks import erase_social, erase_videos

        erase_social(
            actor.org_id, (member_id,)
        )  # their Social profile, posts and messages
        erase_videos(actor.org_id, (member_id,))  # their channel, videos and files
        self.app.lifecycle.recompute_over_limits(actor.org_id)
        return result

    def _offboard_candidate(
        self, actor: Principal, target: dict[str, Any]
    ) -> dict[str, Any]:
        """End a candidate's runs, then keep or export their workspace by the organization's data policy."""
        org_id, candidate_id = actor.org_id, target["member_id"]
        policy = self.policy(org_id)["candidate_data_policy"]
        if self.app.lifecycle.on_legal_hold(org_id):
            policy = "organization_keeps"  # nothing is deleted or handed over while a hold is in place
        self.app.workspaces.stop_automation(org_id, candidate_id)
        snapshot = self.app.workspaces.compute_snapshot(org_id, candidate_id)
        outcome: dict[str, Any] = {"data_policy": policy}
        if policy == "candidate_keeps":
            export = self.app.lifecycle.create_export(
                org_id,
                candidate_id=candidate_id,
                kind="candidate_departure",
                created_by=actor.actor_id,
                recipient=target["email"],
                principal=actor,
                full=True,  # the candidate's own data, on its way to the candidate
            )
            self.app.mail.send(
                org_id,
                target["email"],
                "data_export_ready",
                org=self.org(org_id)["name"],
                link=f"{self.app.config.base_url}/api/exports/download/{export['token']}",
            )
            outcome["export_id"] = export["export_id"]
            self.app.workspaces.delete(org_id, candidate_id)
            with self.db.write() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO departed_candidates (org_id, candidate_id, removed_at, data_policy, stage, snapshot) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        org_id,
                        candidate_id,
                        self.now,
                        policy,
                        self.app.workspaces.stage_of(snapshot),
                        dumps(
                            {
                                k: snapshot[k]
                                for k in (
                                    "submissions",
                                    "interviews",
                                    "offers",
                                    "placed",
                                    "by_source",
                                )
                            }
                        ),
                    ),
                )
        else:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE workspaces SET status = 'archived', paused_at = ?, paused_reason = 'Member left the organization' WHERE candidate_id = ? AND org_id = ?",
                    (self.now, candidate_id, org_id),
                )
        return outcome

    # ------------------------------------------------------------------ coach assignments

    def assign_coach(
        self, actor: Principal, coach_id: str, candidate_id: str, scope: str
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "coaches.assign")
        self.app.entitlements.require_state(actor.org_id, write=True)
        if scope not in COACH_SCOPES:
            raise ValidationFailed("Scope must be view, act or approve.")
        coach = self.member(actor.org_id, coach_id)
        if coach["role"] != COACH or coach["status"] != "active":
            raise ValidationFailed("Choose an active coach.")
        self.app.authz.candidate(actor.org_id, candidate_id)
        entitlements = self.app.entitlements.resolve(actor.org_id)
        if scope not in entitlements.coach_scopes():
            needed = self.app.entitlements.required_plan(
                "coach.approve" if scope == "approve" else "coach.act"
            )
            raise PlanRequired(
                f"The {entitlements.plan_id.title()} plan allows coach scopes: {', '.join(entitlements.coach_scopes())}."
                + (f" Upgrade to {needed.title()} to use '{scope}'." if needed else ""),
                feature="coach." + scope,
                required_plan=needed,
                current_plan=entitlements.plan_id,
            )
        with self.db.write() as connection:
            existing = one(
                connection,
                "SELECT scope FROM coach_assignments WHERE org_id = ? AND coach_id = ? AND candidate_id = ?",
                (actor.org_id, coach_id, candidate_id),
            )
            connection.execute(
                """INSERT INTO coach_assignments (org_id, coach_id, candidate_id, scope, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(org_id, coach_id, candidate_id) DO UPDATE SET scope = excluded.scope""",
                (actor.org_id, coach_id, candidate_id, scope, actor.actor_id, self.now),
            )
            self.app.audit.record(
                actor.org_id,
                "coach.assign" if not existing else "coach.scope_change",
                actor=actor,
                candidate_id=candidate_id,
                target_type="member",
                target_id=coach_id,
                detail={
                    "scope": scope,
                    "previous": existing["scope"] if existing else None,
                },
                connection=connection,
            )
        return {"coach_id": coach_id, "candidate_id": candidate_id, "scope": scope}

    def unassign_coach(
        self, actor: Principal, coach_id: str, candidate_id: str
    ) -> None:
        self.app.authz.require(actor, "coaches.assign")
        with self.db.write() as connection:
            removed = connection.execute(
                "DELETE FROM coach_assignments WHERE org_id = ? AND coach_id = ? AND candidate_id = ?",
                (actor.org_id, coach_id, candidate_id),
            ).rowcount
            if not removed:
                raise NotFound("Assignment not found.")
            self.app.audit.record(
                actor.org_id,
                "coach.unassign",
                actor=actor,
                candidate_id=candidate_id,
                target_type="member",
                target_id=coach_id,
                connection=connection,
            )

    def bulk_reassign(
        self, actor: Principal, from_coach: str, to_coach: str, scope: str = ""
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "coaches.assign")
        self.app.entitlements.require_state(actor.org_id, write=True)
        target = self.member(actor.org_id, to_coach)
        self.member(actor.org_id, from_coach)
        if target["role"] != COACH:
            raise ValidationFailed("Choose an active coach to receive the candidates.")
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT candidate_id, scope FROM coach_assignments WHERE org_id = ? AND coach_id = ?",
                (actor.org_id, from_coach),
            )
        moved = 0
        for row in rows:
            self.assign_coach(
                actor, to_coach, row["candidate_id"], scope or row["scope"]
            )
            self.unassign_coach(actor, from_coach, row["candidate_id"])
            moved += 1
        return {"moved": moved}

    def list_assignments(
        self, actor: Principal, *, coach_id: str = "", candidate_id: str = ""
    ) -> list[dict[str, Any]]:
        clauses, params = ["a.org_id = ?"], [actor.org_id]
        if actor.role == COACH:
            clauses.append("a.coach_id = ?")
            params.append(actor.member_id)
        elif actor.role == CANDIDATE:
            clauses.append("a.candidate_id = ?")
            params.append(actor.member_id)
        elif not self.app.authz.can(actor, "candidates.list"):
            raise Forbidden("You do not have permission to do that.")
        if coach_id:
            clauses.append("a.coach_id = ?")
            params.append(coach_id)
        if candidate_id:
            clauses.append("a.candidate_id = ?")
            params.append(candidate_id)
        with self.db.read() as connection:
            rows = many(
                connection,
                f"""SELECT a.coach_id, a.candidate_id, a.scope, a.created_at, c.name AS coach_name, c.email AS coach_email,
                m.name AS candidate_name, m.email AS candidate_email FROM coach_assignments a
                JOIN members c ON c.member_id = a.coach_id JOIN members m ON m.member_id = a.candidate_id
                WHERE {" AND ".join(clauses)} ORDER BY a.created_at""",
                params,
            )
        cap = self.app.entitlements.resolve(actor.org_id).coach_scopes()
        for row in rows:
            row["effective_scope"] = row["scope"] if row["scope"] in cap else cap[-1]
        return rows

    # ----------------------------------------------------------------------- pausing

    def pause_org(self, actor: Principal, reason: str) -> dict[str, Any]:
        self.app.authz.require(actor, "automation.pause.org")
        reason = str(reason or "").strip()
        if not reason:
            raise ValidationFailed("Give a reason; it appears on the dashboard.")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET paused_at = ?, paused_reason = ?, paused_by = ? WHERE org_id = ?",
                (self.now, reason[:300], actor.actor_id, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "automation.pause_org",
                actor=actor,
                detail={"reason": reason[:300]},
                connection=connection,
            )
        return {"paused": True, "reason": reason[:300]}

    def resume_org(self, actor: Principal) -> dict[str, Any]:
        self.app.authz.require(actor, "automation.pause.org")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET paused_at = NULL, paused_reason = '', paused_by = '' WHERE org_id = ?",
                (actor.org_id,),
            )
            self.app.audit.record(
                actor.org_id,
                "automation.resume_org",
                actor=actor,
                connection=connection,
            )
        return {"paused": False}

    def pause_candidate(
        self, actor: Principal, candidate_id: str, reason: str
    ) -> dict[str, Any]:
        self.app.authz.require(
            actor, "automation.pause.candidate", candidate_id=candidate_id
        )
        reason = str(reason or "").strip()
        if not reason:
            raise ValidationFailed("Give a reason; it appears on the dashboard.")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET paused_at = ?, paused_reason = ?, paused_by = ? WHERE candidate_id = ? AND org_id = ?",
                (self.now, reason[:300], actor.actor_id, candidate_id, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "automation.pause_candidate",
                actor=actor,
                candidate_id=candidate_id,
                detail={"reason": reason[:300]},
                connection=connection,
            )
        self.app.workspaces.invalidate_snapshot(actor.org_id, candidate_id)
        return {"paused": True, "reason": reason[:300]}

    def resume_candidate(self, actor: Principal, candidate_id: str) -> dict[str, Any]:
        self.app.authz.require(
            actor, "automation.pause.candidate", candidate_id=candidate_id
        )
        workspace = self.app.workspaces.row(actor.org_id, candidate_id)
        if (
            workspace["paused_at"]
            and workspace["paused_by"]
            and workspace["paused_by"] != actor.actor_id
        ):
            paused_by = None
            with self.db.read() as connection:
                paused_by = one(
                    connection,
                    "SELECT role FROM members WHERE member_id = ?",
                    (workspace["paused_by"],),
                )
            if (
                paused_by
                and paused_by["role"] in (OWNER, ADMIN)
                and actor.role in (COACH, CANDIDATE)
            ):
                raise Forbidden(
                    "An administrator paused this candidate's automation. Ask them to resume it."
                )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET paused_at = NULL, paused_reason = '', paused_by = '' WHERE candidate_id = ? AND org_id = ?",
                (candidate_id, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "automation.resume_candidate",
                actor=actor,
                candidate_id=candidate_id,
                connection=connection,
            )
        return {"paused": False}

    # ------------------------------------------------------------------------ consent

    def record_consent(
        self, principal: Principal, kinds: list[str], version: str = CONSENT_VERSION
    ) -> dict[str, Any]:
        if principal.role != CANDIDATE:
            raise Forbidden("Only a candidate records their own consent.")
        for kind in kinds:
            if kind not in CONSENT_KINDS:
                raise ValidationFailed(f"Unknown consent: {kind}.")
        with self.db.write() as connection:
            for kind in kinds:
                connection.execute(
                    "UPDATE consents SET revoked_at = ? WHERE org_id = ? AND candidate_id = ? AND kind = ? AND revoked_at IS NULL",
                    (self.now, principal.org_id, principal.member_id, kind),
                )
                connection.execute(
                    "INSERT INTO consents (org_id, candidate_id, kind, version, granted_at, detail) VALUES (?, ?, ?, ?, ?, '{}')",
                    (principal.org_id, principal.member_id, kind, version, self.now),
                )
            self.app.audit.record(
                principal.org_id,
                "consent.grant",
                actor=principal,
                candidate_id=principal.member_id,
                detail={"kinds": kinds, "version": version},
                connection=connection,
            )
        self.app.workspaces.invalidate_snapshot(principal.org_id, principal.member_id)
        return self.consent_state(principal.org_id, principal.member_id)

    def revoke_consent(self, principal: Principal, kind: str) -> dict[str, Any]:
        if principal.role != CANDIDATE:
            raise Forbidden("Only a candidate revokes their own consent.")
        if kind not in CONSENT_KINDS:
            raise ValidationFailed(f"Unknown consent: {kind}.")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE consents SET revoked_at = ? WHERE org_id = ? AND candidate_id = ? AND kind = ? AND revoked_at IS NULL",
                (self.now, principal.org_id, principal.member_id, kind),
            )
            self.app.audit.record(
                principal.org_id,
                "consent.revoke",
                actor=principal,
                candidate_id=principal.member_id,
                detail={"kind": kind},
                connection=connection,
            )
        return self.consent_state(principal.org_id, principal.member_id)

    def consent_state(self, org_id: str, candidate_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT kind, version, granted_at FROM consents WHERE org_id = ? AND candidate_id = ? AND revoked_at IS NULL ORDER BY id",
                (org_id, candidate_id),
            )
        granted = {
            r["kind"]: {
                "version": r["version"],
                "granted_at": r["granted_at"],
                "current": r["version"] == CONSENT_VERSION,
            }
            for r in rows
        }
        required = list(REQUIRED_CONSENTS)
        if self.app.authz.is_personal(org_id, candidate_id):
            required.remove("coach_access")  # no coach can open a personal workspace
        return {
            "version": CONSENT_VERSION,
            "granted": granted,
            "required": required,
            "complete": all(k in granted and granted[k]["current"] for k in required),
        }

    # --------------------------------------------------------------------- onboarding

    def onboarding(self, org_id: str, candidate_id: str) -> dict[str, Any]:
        snapshot = self.app.workspaces.refresh(org_id, candidate_id)
        consent = self.consent_state(org_id, candidate_id)
        workspace = self.app.workspaces.row(org_id, candidate_id)
        policy = self.policy(org_id)
        with self.db.read() as connection:
            coaches = many(
                connection,
                "SELECT coach_id FROM coach_assignments WHERE org_id = ? AND candidate_id = ?",
                (org_id, candidate_id),
            )
        entitlements = self.app.entitlements.resolve(org_id)
        linkedin_expected = bool(policy["linkedin_allowed"]) and entitlements.has(
            "sources.linkedin"
        )
        items = [
            {
                "id": "consent",
                "label": "Review and accept how your organization runs applications for you",
                "done": consent["complete"],
                "link": "#/settings/privacy",
            },
            {
                "id": "profile",
                "label": "Complete your profile",
                "done": bool(snapshot["profile_ready"]),
                "link": "#/settings/profile",
            },
            {
                "id": "resume",
                "label": "Upload your resume",
                "done": bool(snapshot["resume_file"] or snapshot["resume"]),
                "link": "#/settings/resume",
            },
            {
                "id": "answers",
                "label": "Confirm your work authorization answers",
                "done": bool(snapshot["answers_confirmed"]),
                "link": "#/jobs/autopilot",
            },
            {
                "id": "plan",
                "label": "Create a search plan",
                "done": bool(snapshot["plans"]),
                "link": "#/jobs/autopilot",
            },
            {
                "id": "coach",
                "label": "A coach is assigned",
                "done": bool(coaches),
                "link": "",
                "informational": True,
            },
        ]
        if linkedin_expected:
            items.append(
                {
                    "id": "linkedin",
                    "label": "Connect LinkedIn (optional)",
                    "done": bool(workspace["linkedin_port"]),
                    "link": "#/settings/integrations",
                    "optional": True,
                }
            )
        required = [
            i for i in items if not i.get("optional") and not i.get("informational")
        ]
        return {
            "items": items,
            "done": sum(1 for i in required if i["done"]),
            "total": len(required),
            "complete": all(i["done"] for i in required),
        }

    def _system_principal(self, org_id: str) -> Principal:
        return Principal(kind="member", org_id=org_id, role=OWNER)

    # ------------------------------------------------------------------- candidate list

    def list_candidates(
        self,
        actor: Principal,
        *,
        q: str = "",
        stage: str = "",
        coach_id: str = "",
        attention: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "candidates.list")
        clauses, params = (
            ["m.org_id = ?", "m.role = 'candidate'", "m.removed_at IS NULL"],
            [actor.org_id],
        )
        if actor.role == COACH:
            assigned = self.app.authz.assigned_candidates(actor.org_id, actor.member_id)
            if not assigned:
                return []
            clauses.append(f"m.member_id IN ({','.join('?' * len(assigned))})")
            params.extend(assigned)
        if q:
            clauses.append("(m.email LIKE ? OR m.name LIKE ?)")
            params.extend([f"%{q.strip()}%"] * 2)
        if stage:
            clauses.append("w.stage = ?")
            params.append(stage)
        if coach_id:
            clauses.append(
                "m.member_id IN (SELECT candidate_id FROM coach_assignments WHERE org_id = ? AND coach_id = ?)"
            )
            params.extend([actor.org_id, coach_id])
        with self.db.read() as connection:
            rows = many(
                connection,
                f"""SELECT m.member_id, m.email, m.name, m.created_at, w.stage, w.status AS workspace_status, w.paused_at, w.paused_reason,
                w.linkedin_port, w.snapshot, w.snapshot_at, w.last_activity_at FROM members m JOIN workspaces w ON w.candidate_id = m.member_id
                WHERE {" AND ".join(clauses)} ORDER BY m.created_at LIMIT ? OFFSET ?""",
                (*params, max(1, min(limit, 500)), max(0, offset)),
            )
            coaches = many(
                connection,
                """SELECT a.candidate_id, a.coach_id, a.scope, c.name, c.email FROM coach_assignments a JOIN members c ON c.member_id = a.coach_id
                WHERE a.org_id = ?""",
                (actor.org_id,),
            )
        by_candidate: dict[str, list[dict[str, Any]]] = {}
        for row in coaches:
            by_candidate.setdefault(row["candidate_id"], []).append(
                {
                    "coach_id": row["coach_id"],
                    "name": row["name"] or row["email"],
                    "scope": row["scope"],
                }
            )
        now = self.now
        result = []
        for row in rows:
            snapshot = loads(row["snapshot"], {})
            if not snapshot or not row["snapshot_at"] or now - row["snapshot_at"] > 120:
                try:
                    snapshot = self.app.workspaces.refresh(
                        actor.org_id, row["member_id"], max_age=120
                    )
                    row["stage"] = self.app.workspaces.row(
                        actor.org_id, row["member_id"]
                    )["stage"]
                except NotFound:
                    continue
            issues = self.attention_for(
                row, snapshot, bool(by_candidate.get(row["member_id"]))
            )
            if attention and not issues:
                continue
            result.append(
                {
                    "candidate_id": row["member_id"],
                    "email": row["email"],
                    "name": row["name"],
                    "stage": row["stage"],
                    "workspace_status": row["workspace_status"],
                    "paused": bool(row["paused_at"]),
                    "paused_reason": row["paused_reason"],
                    "linkedin_connected": bool(row["linkedin_port"]),
                    "coaches": by_candidate.get(row["member_id"], []),
                    "held": snapshot.get("held", 0),
                    "ready": snapshot.get("ready", 0),
                    "submissions_30d": snapshot.get("submissions_30d", 0),
                    "interviews": snapshot.get("interviews", 0),
                    "last_activity_at": row["last_activity_at"],
                    "needs_attention": issues,
                }
            )
        return result

    def attention_for(
        self, row: dict[str, Any], snapshot: dict[str, Any], has_coach: bool
    ) -> list[dict[str, Any]]:
        """The rules behind 'Needs attention'; each carries a link to where it is fixed."""
        from aira_cloud.workspace import _to_epoch

        issues: list[dict[str, Any]] = []
        candidate_id = row.get("member_id") or row.get("candidate_id", "")
        base = f"#/org/candidates/{candidate_id}"
        oldest = _to_epoch(snapshot.get("oldest_held_at"))
        if snapshot.get("held") and oldest and self.now - oldest > 86400:
            issues.append(
                {
                    "rule": "held_questions",
                    "label": f"{snapshot['held']} held question(s) waiting more than 24 hours",
                    "link": base,
                }
            )
        if snapshot.get("failed_24h"):
            issues.append(
                {
                    "rule": "failed_runs",
                    "label": f"{snapshot['failed_24h']} failed run(s) in the last day",
                    "link": base,
                }
            )
        if not snapshot.get("resume_file") and not snapshot.get("resume"):
            issues.append(
                {"rule": "resume_missing", "label": "Resume not uploaded", "link": base}
            )
        if not has_coach:
            issues.append(
                {
                    "rule": "no_coach",
                    "label": "No coach assigned",
                    "link": "#/org/coaches",
                }
            )
        return issues

    def org_attention(
        self, org_id: str, snapshots: list[tuple[dict[str, Any], dict[str, Any], bool]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        policy = self.policy(org_id)
        linkedin_expected = bool(
            policy["linkedin_allowed"]
        ) and self.app.entitlements.resolve(org_id).has("sources.linkedin")
        for row, snapshot, has_coach in snapshots:
            issues = self.attention_for(row, snapshot, has_coach)
            if linkedin_expected and not row.get("linkedin_port"):
                issues.append(
                    {
                        "rule": "linkedin_unconnected",
                        "label": "LinkedIn is expected by the plan but not connected",
                        "link": f"#/org/candidates/{row['member_id']}",
                    }
                )
            for issue in issues:
                out.append(
                    {
                        **issue,
                        "candidate_id": row["member_id"],
                        "candidate": row.get("name") or row.get("email"),
                    }
                )
        return out

    # ---------------------------------------------------------------- coach metrics

    def median_answer_seconds(
        self, org_id: str, coach_id: str, since: int
    ) -> int | None:
        rows = self.app.audit.list(
            org_id, limit=500, action="held.answer", actor_id=coach_id, since=since
        )
        values = [
            int(r["detail"].get("seconds_to_answer"))
            for r in rows
            if isinstance(r["detail"].get("seconds_to_answer"), (int, float))
        ]
        return int(statistics.median(values)) if values else None


__all__ = [
    "Tenancy",
    "DEFAULT_POLICY",
    "CONSENT_VERSION",
    "REQUIRED_CONSENTS",
    "STAFF_ROLES",
    "SCOPE_LEVELS",
    "crypto",
    "sqlite3",
]
