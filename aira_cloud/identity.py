"""Identity: organization sign-up, email verification, sign-in, sessions, MFA, invitations and password reset.

Sessions are server-side rows keyed by a hashed token. A role or password change bumps the member's
``session_epoch``, which invalidates every earlier session at once (session rotation). One person can
be a member of two organizations: that is two members with two logins, and a sign-in that matches
both asks which organization to enter, after the password has been proven.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from aira_cloud import crypto
from aira_cloud.db import INTEGRITY_ERRORS, dumps, loads, many, new_id, one
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    Gone,
    Locked,
    MfaRequired,
    NotFound,
    PlanRequired,
    Unauthorized,
    ValidationFailed,
)
from aira_cloud.rbac import (
    ADMIN,
    CANDIDATE,
    COACH,
    COACH_SCOPES,
    ORG_ROLES,
    OWNER,
    Principal,
)

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.identity")

SLUG_RE = re.compile(r"[^a-z0-9]+")
DUMMY_HASH = crypto.hash_password("dummy-password-for-timing")


def slugify(name: str) -> str:
    slug = SLUG_RE.sub("-", name.lower()).strip("-")[:40]
    return slug or "org"


class Identity:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ sign-up

    def signup_org(
        self,
        name: str,
        email: str,
        password: str,
        *,
        owner_name: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        """Create an organization, its owner and a trial subscription; the owner verifies their email next."""
        name = str(name or "").strip()
        if not 2 <= len(name) <= 120:
            raise ValidationFailed("Enter the organization name (2 to 120 characters).")
        email = crypto.normalize_email(email)
        crypto.check_password_policy(password, email)
        org_id, member_id = new_id(), new_id()
        now = self.now
        with self.db.write() as connection:
            base = slugify(name)
            slug, suffix = base, 1
            while one(
                connection, "SELECT 1 AS x FROM organizations WHERE slug = ?", (slug,)
            ):
                suffix += 1
                slug = f"{base}-{suffix}"
            connection.execute(
                "INSERT INTO organizations (org_id, name, slug, region, status, policy, branding, created_at) VALUES (?, ?, ?, ?, 'active', ?, '{}', ?)",
                (
                    org_id,
                    name,
                    slug,
                    self.app.config.region,
                    dumps(self.app.tenancy.default_policy()),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO members (member_id, org_id, email, name, password_hash, role, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'owner', 'active', ?)""",
                (
                    member_id,
                    org_id,
                    email,
                    owner_name.strip()[:120],
                    crypto.hash_password(password),
                    now,
                ),
            )
            self.app.billing.create_trial(org_id, connection)
            self.app.audit.record(
                org_id,
                "org.create",
                actor_type="member",
                actor_id=member_id,
                actor_label=f"owner:{email}",
                actor_role="owner",
                target_type="organization",
                target_id=org_id,
                detail={"name": name, "slug": slug, "plan": "trial"},
                connection=connection,
            )
        self.send_verification(org_id, member_id)
        token, session = self._create_session(
            org_id,
            member_id,
            ip=ip,
            user_agent=user_agent,
            mfa_verified=True,
            auth_method="signup",
        )
        return {
            "org": {"org_id": org_id, "name": name, "slug": slug},
            "member": {"member_id": member_id, "email": email, "role": "owner"},
            "token": token,
            "session_id": session,
        }

    # ------------------------------------------------------------ email verification

    def _issue_email_token(self, org_id: str, member_id: str, kind: str) -> str:
        token = crypto.new_token()
        with self.db.write() as connection:
            connection.execute(
                "UPDATE email_tokens SET used_at = ? WHERE org_id = ? AND member_id = ? AND kind = ? AND used_at IS NULL",
                (self.now, org_id, member_id, kind),
            )
            connection.execute(
                "INSERT INTO email_tokens (token_hash, org_id, member_id, kind, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    crypto.hash_token(token),
                    org_id,
                    member_id,
                    kind,
                    self.now,
                    self.now + self.app.config.email_token_ttl,
                ),
            )
        return token

    def send_verification(self, org_id: str, member_id: str) -> None:
        member = self.app.tenancy.member(org_id, member_id)
        token = self._issue_email_token(org_id, member_id, "verify")
        org = self.app.tenancy.org(org_id)
        self.app.mail.send(
            org_id,
            member["email"],
            "verify_email",
            name=member["name"] or member["email"],
            org=org["name"],
            link=f"{self.app.config.base_url}/#/verify/{token}",
        )

    def resend_verification(self, principal: Principal) -> None:
        member = self.app.tenancy.member(principal.org_id, principal.member_id)
        if member["email_verified_at"]:
            return
        self.send_verification(principal.org_id, principal.member_id)

    def verify_email(self, token: str) -> dict[str, Any]:
        row = self._consume_email_token(token, "verify")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET email_verified_at = COALESCE(email_verified_at, ?) WHERE member_id = ? AND org_id = ?",
                (self.now, row["member_id"], row["org_id"]),
            )
            self.app.audit.record(
                row["org_id"],
                "member.verify_email",
                actor_type="member",
                actor_id=row["member_id"],
                target_type="member",
                target_id=row["member_id"],
                connection=connection,
            )
        member = self.app.tenancy.member(row["org_id"], row["member_id"])
        return {"verified": True, "email": member["email"], "org_id": row["org_id"]}

    def _consume_email_token(self, token: str, kind: str) -> dict[str, Any]:
        digest = crypto.hash_token(token or "")
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM email_tokens WHERE token_hash = ? AND kind = ?",
                (digest, kind),
            )
            if row is None:
                raise NotFound("This link is not valid.")
            if row["used_at"]:
                raise Gone("This link was already used.")
            if row["expires_at"] <= self.now:
                raise Gone("This link has expired. Request a new one.")
            connection.execute(
                "UPDATE email_tokens SET used_at = ? WHERE token_hash = ?",
                (self.now, digest),
            )
        return row

    # ---------------------------------------------------------------- sessions

    def _create_session(
        self,
        org_id: str,
        member_id: str,
        *,
        ip: str = "",
        user_agent: str = "",
        mfa_verified: bool = False,
        auth_method: str = "password",
    ) -> tuple[str, str]:
        token = crypto.new_token()
        session_id = new_id()
        with self.db.write() as connection:
            member = one(
                connection,
                "SELECT session_epoch FROM members WHERE member_id = ? AND org_id = ?",
                (member_id, org_id),
            )
            if member is None:
                raise NotFound("Member not found.")
            connection.execute(
                """INSERT INTO sessions (session_id, token_hash, kind, org_id, member_id, epoch, mfa_verified, auth_method, ip, user_agent, created_at, last_seen_at, expires_at)
                VALUES (?, ?, 'member', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    crypto.hash_token(token),
                    org_id,
                    member_id,
                    member["session_epoch"],
                    int(mfa_verified),
                    auth_method,
                    ip[:64],
                    user_agent[:200],
                    self.now,
                    self.now,
                    self.now + self.app.config.session_ttl,
                ),
            )
        return token, session_id

    def create_session_for(
        self,
        org_id: str,
        member_id: str,
        *,
        ip: str = "",
        user_agent: str = "",
        auth_method: str = "sso",
    ) -> str:
        """A session for a member already authenticated by an identity provider (it satisfies MFA)."""
        token, _ = self._create_session(
            org_id,
            member_id,
            ip=ip,
            user_agent=user_agent,
            mfa_verified=True,
            auth_method=auth_method,
        )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET last_sign_in_at = ?, failed_logins = 0 WHERE member_id = ?",
                (self.now, member_id),
            )
            self.app.audit.record(
                org_id,
                "auth.login",
                actor_type="member",
                actor_id=member_id,
                target_type="member",
                target_id=member_id,
                detail={"method": auth_method},
                connection=connection,
            )
        return token

    def principal_for_token(
        self, token: str, *, kind: str = "member"
    ) -> Principal | None:
        if not token:
            return None
        digest = crypto.hash_token(token)
        now = self.now
        with self.db.read() as connection:
            session = one(
                connection, "SELECT * FROM sessions WHERE token_hash = ?", (digest,)
            )
            if (
                session is None
                or session["revoked_at"]
                or session["expires_at"] <= now
                or session["kind"] != kind
            ):
                return None
            if kind == "member":
                member = one(
                    connection,
                    "SELECT * FROM members WHERE member_id = ? AND org_id = ?",
                    (session["member_id"], session["org_id"]),
                )
                org = one(
                    connection,
                    "SELECT status, deleted_at FROM organizations WHERE org_id = ?",
                    (session["org_id"],),
                )
                if member is None or org is None or org["deleted_at"]:
                    return None
                if (
                    member["status"] != "active"
                    or member["removed_at"]
                    or member["session_epoch"] != session["epoch"]
                ):
                    return None
                principal = Principal(
                    kind="member",
                    session_id=session["session_id"],
                    org_id=member["org_id"],
                    member_id=member["member_id"],
                    role=member["role"],
                    email=member["email"],
                    name=member["name"],
                    mfa_verified=bool(session["mfa_verified"]),
                    mfa_enabled=bool(member["mfa_enabled_at"]),
                    auth_method=session["auth_method"],
                    extra={"email_verified": bool(member["email_verified_at"])},
                )
            else:
                operator = one(
                    connection,
                    "SELECT * FROM operators WHERE operator_id = ?",
                    (session["operator_id"],),
                )
                if operator is None or operator["status"] != "active":
                    return None
                principal = Principal(
                    kind="operator",
                    session_id=session["session_id"],
                    operator_id=operator["operator_id"],
                    operator_role=operator["role"],
                    email=operator["email"],
                    name=operator["name"],
                    mfa_verified=bool(session["mfa_verified"]),
                    mfa_enabled=bool(operator["mfa_enabled_at"]),
                )
                if session["support_access_id"]:
                    grant = one(
                        connection,
                        "SELECT * FROM support_access WHERE access_id = ? AND status = 'approved' AND expires_at > ? AND ended_at IS NULL",
                        (session["support_access_id"], now),
                    )
                    if grant is not None:
                        principal.org_id = grant["org_id"]
                        principal.role = grant["role"]
                        principal.support_access_id = grant["access_id"]
        if now - session["last_seen_at"] > 60:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                    (now, session["session_id"]),
                )
        return principal

    def logout(self, token: str) -> None:
        if not token:
            return
        with self.db.write() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (self.now, crypto.hash_token(token)),
            )

    def list_sessions(self, principal: Principal) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = many(
                connection,
                """SELECT session_id, auth_method, ip, user_agent, created_at, last_seen_at, expires_at FROM sessions
                WHERE kind = 'member' AND org_id = ? AND member_id = ? AND revoked_at IS NULL AND expires_at > ? ORDER BY last_seen_at DESC""",
                (principal.org_id, principal.member_id, self.now),
            )
        for row in rows:
            row["current"] = row["session_id"] == principal.session_id
        return rows

    def revoke_session(self, principal: Principal, session_id: str) -> None:
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND org_id = ? AND member_id = ? AND revoked_at IS NULL",
                (self.now, session_id, principal.org_id, principal.member_id),
            ).rowcount
            if not changed:
                raise NotFound("Session not found.")
            self.app.audit.record(
                principal.org_id,
                "auth.session_revoke",
                actor=principal,
                target_type="session",
                target_id=session_id,
                connection=connection,
            )

    def revoke_other_sessions(self, principal: Principal) -> int:
        with self.db.write() as connection:
            count = connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND member_id = ? AND session_id != ? AND revoked_at IS NULL",
                (self.now, principal.org_id, principal.member_id, principal.session_id),
            ).rowcount
            self.app.audit.record(
                principal.org_id,
                "auth.sign_out_devices",
                actor=principal,
                detail={"sessions": count},
                connection=connection,
            )
        return count

    def end_all_sessions(
        self, org_id: str, member_id: str, connection: sqlite3.Connection | None = None
    ) -> None:
        """Invalidate every session of a member: bump the epoch and revoke the rows."""

        def apply(db_connection: sqlite3.Connection) -> None:
            db_connection.execute(
                "UPDATE members SET session_epoch = session_epoch + 1 WHERE member_id = ? AND org_id = ?",
                (member_id, org_id),
            )
            db_connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND member_id = ? AND revoked_at IS NULL",
                (self.now, org_id, member_id),
            )

        if connection is not None:
            apply(connection)
        else:
            with self.db.write() as write_connection:
                apply(write_connection)

    # ---------------------------------------------------------------- sign-in

    def login(
        self,
        email: str,
        password: str,
        *,
        org: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        email = str(email or "").strip().lower()
        with self.db.read() as connection:
            members = many(
                connection,
                """SELECT m.*, o.slug AS org_slug, o.name AS org_name, o.status AS org_status, o.deleted_at AS org_deleted_at
                FROM members m JOIN organizations o ON o.org_id = m.org_id
                WHERE m.email = ? AND m.removed_at IS NULL AND o.deleted_at IS NULL""",
                (email,),
            )
        if org:
            members = [
                m
                for m in members
                if m["org_slug"] == org.strip().lower() or m["org_id"] == org
            ]
        if not members:
            crypto.verify_password(
                password or "", DUMMY_HASH
            )  # constant-ish time for unknown emails
            raise Unauthorized("Email or password is incorrect.")
        candidates: list[dict[str, Any]] = []
        locked_until = 0
        for member in members:
            if member["locked_until"] and member["locked_until"] > self.now:
                locked_until = max(locked_until, member["locked_until"])
                continue
            if member["password_hash"] and crypto.verify_password(
                password or "", member["password_hash"]
            ):
                candidates.append(member)
            else:
                self._failed_login(member)
        if not candidates:
            if locked_until:
                raise Locked(
                    f"Too many failed sign-ins. Try again in {max(1, (locked_until - self.now) // 60 + 1)} minutes.",
                    retry_after=locked_until - self.now,
                )
            raise Unauthorized("Email or password is incorrect.")
        if len(candidates) > 1:
            raise Conflict(
                "This email belongs to more than one organization. Choose one.",
                code="org_required",
                orgs=[
                    {"slug": m["org_slug"], "name": m["org_name"]} for m in candidates
                ],
            )
        member = candidates[0]
        if member["status"] != "active":
            raise Unauthorized("This account is not active.")
        if member["org_status"] == "suspended":
            raise Forbidden(
                "This organization is suspended. Contact your administrator or Monosage support."
            )
        sso = (
            self.app.sso.enforced_for(member["org_id"])
            if hasattr(self.app, "sso")
            else False
        )
        if sso and member["role"] != OWNER:
            raise Forbidden(
                "This organization requires single sign-on. Use the SSO sign-in.",
                code="sso_required",
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET failed_logins = 0, locked_until = NULL, last_sign_in_at = ? WHERE member_id = ?",
                (self.now, member["member_id"]),
            )
        mfa_enabled = bool(member["mfa_enabled_at"])
        token, session_id = self._create_session(
            member["org_id"],
            member["member_id"],
            ip=ip,
            user_agent=user_agent,
            mfa_verified=not mfa_enabled,
            auth_method="password",
        )
        self.app.audit.record(
            member["org_id"],
            "auth.login",
            actor_type="member",
            actor_id=member["member_id"],
            actor_label=f"{member['role']}:{member['email']}",
            actor_role=member["role"],
            target_type="member",
            target_id=member["member_id"],
            detail={"method": "password", "mfa_required": mfa_enabled},
        )
        return {
            "token": token,
            "session_id": session_id,
            "member_id": member["member_id"],
            "org_id": member["org_id"],
            "mfa_required": mfa_enabled,
        }

    def _failed_login(self, member: dict[str, Any]) -> None:
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT failed_logins FROM members WHERE member_id = ?",
                (member["member_id"],),
            )
            attempts = int(row["failed_logins"]) + 1 if row else 1
            locked = (
                self.now + self.app.config.lockout_seconds
                if attempts >= self.app.config.max_failed_logins
                else None
            )
            connection.execute(
                "UPDATE members SET failed_logins = ?, locked_until = ? WHERE member_id = ?",
                (0 if locked else attempts, locked, member["member_id"]),
            )
            if locked:
                self.app.audit.record(
                    member["org_id"],
                    "auth.locked",
                    actor_type="system",
                    target_type="member",
                    target_id=member["member_id"],
                    source="system",
                    connection=connection,
                )

    # -------------------------------------------------------------------- MFA

    def mfa_required_for(self, principal: Principal) -> bool:
        """Owners and admins on plans with SSO must use MFA; an organization may require it of all staff."""
        if principal.is_operator:
            return True
        if principal.auth_method == "sso":
            return False  # the identity provider handles the second factor
        entitlements = self.app.entitlements.resolve(principal.org_id)
        policy = self.app.tenancy.policy(principal.org_id)
        if principal.role in (OWNER, ADMIN) and entitlements.has("auth.sso"):
            return True
        if policy.get("require_mfa_staff") and principal.role != CANDIDATE:
            return True
        return False

    def mfa_status(self, principal: Principal) -> dict[str, Any]:
        return {
            "enabled": principal.mfa_enabled,
            "required": self.mfa_required_for(principal),
            "verified": principal.mfa_verified,
            "can_disable": principal.mfa_enabled
            and not self.mfa_required_for(principal),
        }

    def _subject(self, principal: Principal) -> tuple[str, str, str]:
        """(table, id column, secret scope) for the person MFA belongs to."""
        if principal.is_operator:
            return "operators", "operator_id", "_platform"
        return "members", "member_id", principal.org_id

    def mfa_begin(self, principal: Principal) -> dict[str, Any]:
        if principal.mfa_enabled and not principal.mfa_verified:
            # Someone holding only the password must not be able to swap the second factor for their own.
            raise MfaRequired("Give your current second factor before changing it.")
        table, column, scope = self._subject(principal)
        subject_id = principal.actor_id
        secret = crypto.new_totp_secret()
        self.app.secrets.put(scope, f"mfa-pending:{subject_id}", secret)
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE {table} SET mfa_pending_secret = 'set' WHERE {column} = ?",
                (subject_id,),
            )
        return {
            "secret": secret,
            "otpauth_uri": crypto.provisioning_uri(
                secret, principal.email, self.app.config.mfa_issuer
            ),
        }

    def mfa_enable(self, principal: Principal, code: str) -> dict[str, Any]:
        if principal.mfa_enabled and not principal.mfa_verified:
            # Someone holding only the password must not be able to swap the second factor for their own.
            raise MfaRequired("Give your current second factor before changing it.")
        table, column, scope = self._subject(principal)
        subject_id = principal.actor_id
        secret = self.app.secrets.get(scope, f"mfa-pending:{subject_id}")
        if not secret:
            raise ValidationFailed("Start MFA setup first.")
        step = crypto.verify_totp(secret, code, self.now)
        if step is None:
            raise ValidationFailed(
                "That code is not correct. Check the time on your device and try again."
            )
        codes, hashed = crypto.new_recovery_codes()
        self.app.secrets.put(scope, f"mfa:{subject_id}", secret)
        self.app.secrets.delete(scope, f"mfa-pending:{subject_id}")
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE {table} SET mfa_secret = 'set', mfa_pending_secret = '', mfa_enabled_at = ?, mfa_last_step = ?, recovery_codes = ? WHERE {column} = ?",
                (self.now, step, dumps(hashed), subject_id),
            )
            connection.execute(
                "UPDATE sessions SET mfa_verified = 1 WHERE session_id = ?",
                (principal.session_id,),
            )
            if principal.is_operator:
                self.app.platform_audit.record(
                    "operator.mfa_enable", operator=principal, connection=connection
                )
            else:
                self.app.audit.record(
                    principal.org_id,
                    "auth.mfa_enable",
                    actor=principal,
                    target_type="member",
                    target_id=subject_id,
                    connection=connection,
                )
        return {"enabled": True, "recovery_codes": codes}

    def mfa_verify(self, principal: Principal, code: str) -> dict[str, Any]:
        table, column, scope = self._subject(principal)
        subject_id = principal.actor_id
        with self.db.read() as connection:
            row = one(
                connection,
                f"SELECT mfa_last_step, recovery_codes, mfa_enabled_at FROM {table} WHERE {column} = ?",
                (subject_id,),
            )
        if row is None or not row["mfa_enabled_at"]:
            raise ValidationFailed("MFA is not set up.")
        secret = self.app.secrets.get(scope, f"mfa:{subject_id}") or ""
        step = (
            crypto.verify_totp(secret, code, self.now, int(row["mfa_last_step"]))
            if secret
            else None
        )
        used_recovery = False
        if step is None:
            digest = crypto.hash_token(re.sub(r"\s", "", str(code or "")).lower())
            remaining = loads(row["recovery_codes"], [])
            if digest in remaining:
                remaining.remove(digest)
                used_recovery = True
                with self.db.write() as connection:
                    connection.execute(
                        f"UPDATE {table} SET recovery_codes = ? WHERE {column} = ?",
                        (dumps(remaining), subject_id),
                    )
            else:
                self._record_mfa_failure(principal)
                raise Unauthorized("That code is not correct.")
        else:
            with self.db.write() as connection:
                connection.execute(
                    f"UPDATE {table} SET mfa_last_step = ? WHERE {column} = ?",
                    (step, subject_id),
                )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE sessions SET mfa_verified = 1 WHERE session_id = ?",
                (principal.session_id,),
            )
            if not principal.is_operator:
                self.app.audit.record(
                    principal.org_id,
                    "auth.mfa_verify",
                    actor=principal,
                    detail={"recovery_code": used_recovery},
                    connection=connection,
                )
        return {"verified": True, "recovery_code_used": used_recovery}

    def _record_mfa_failure(self, principal: Principal) -> None:
        if principal.is_operator:
            table, column = "operators", "operator_id"
        else:
            table, column = "members", "member_id"
        with self.db.write() as connection:
            row = one(
                connection,
                f"SELECT failed_logins FROM {table} WHERE {column} = ?",
                (principal.actor_id,),
            )
            attempts = int(row["failed_logins"]) + 1 if row else 1
            if attempts >= self.app.config.max_failed_logins:
                connection.execute(
                    f"UPDATE {table} SET failed_logins = 0, locked_until = ? WHERE {column} = ?",
                    (self.now + self.app.config.lockout_seconds, principal.actor_id),
                )
                connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE session_id = ?",
                    (self.now, principal.session_id),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET failed_logins = ? WHERE {column} = ?",
                    (attempts, principal.actor_id),
                )

    def mfa_disable(self, principal: Principal, password: str, code: str) -> None:
        if self.mfa_required_for(principal):
            raise Forbidden(
                "MFA is required for your role on this plan and cannot be turned off.",
                code="mfa_required_by_plan",
            )
        table, column, scope = self._subject(principal)
        with self.db.read() as connection:
            row = one(
                connection,
                f"SELECT password_hash FROM {table} WHERE {column} = ?",
                (principal.actor_id,),
            )
        if row is None or not crypto.verify_password(
            password or "", row["password_hash"]
        ):
            raise Unauthorized("The password is not correct.")
        self.mfa_verify(principal, code)
        self.app.secrets.delete(scope, f"mfa:{principal.actor_id}")
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE {table} SET mfa_secret = '', mfa_enabled_at = NULL, recovery_codes = '[]', mfa_last_step = 0 WHERE {column} = ?",
                (principal.actor_id,),
            )
            self.app.audit.record(
                principal.org_id,
                "auth.mfa_disable",
                actor=principal,
                connection=connection,
            )

    def mfa_reset(self, actor: Principal, member_id: str) -> None:
        """An owner or admin clears a member's MFA after a lost device; their sessions end."""
        self.app.authz.require(actor, "members.manage")
        member = self.app.tenancy.member(actor.org_id, member_id)
        if member["role"] == OWNER and actor.role != OWNER:
            raise Forbidden("Only an owner can reset an owner's MFA.")
        self.app.secrets.delete(actor.org_id, f"mfa:{member_id}")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET mfa_secret = '', mfa_enabled_at = NULL, recovery_codes = '[]', mfa_last_step = 0 WHERE member_id = ? AND org_id = ?",
                (member_id, actor.org_id),
            )
            self.end_all_sessions(actor.org_id, member_id, connection)
            self.app.audit.record(
                actor.org_id,
                "auth.mfa_reset",
                actor=actor,
                target_type="member",
                target_id=member_id,
                connection=connection,
            )

    # ---------------------------------------------------------------- passwords

    def change_password(self, principal: Principal, current: str, new: str) -> str:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT password_hash, email FROM members WHERE member_id = ? AND org_id = ?",
                (principal.member_id, principal.org_id),
            )
        if row is None or not crypto.verify_password(
            current or "", row["password_hash"]
        ):
            raise Unauthorized("The current password is not correct.")
        crypto.check_password_policy(new, row["email"])
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET password_hash = ? WHERE member_id = ? AND org_id = ?",
                (crypto.hash_password(new), principal.member_id, principal.org_id),
            )
            self.end_all_sessions(principal.org_id, principal.member_id, connection)
            self.app.audit.record(
                principal.org_id,
                "auth.password_change",
                actor=principal,
                target_type="member",
                target_id=principal.member_id,
                connection=connection,
            )
        token, _ = self._create_session(
            principal.org_id,
            principal.member_id,
            mfa_verified=True,
            auth_method="password",
        )
        return token

    def request_password_reset(self, email: str, org: str = "") -> None:
        """Always answers the same, whether or not the email exists."""
        email = str(email or "").strip().lower()
        with self.db.read() as connection:
            members = many(
                connection,
                """SELECT m.member_id, m.org_id, m.email, m.name, o.name AS org_name, o.slug AS org_slug FROM members m
                JOIN organizations o ON o.org_id = m.org_id WHERE m.email = ? AND m.removed_at IS NULL AND m.status = 'active' AND o.deleted_at IS NULL""",
                (email,),
            )
        for member in members:
            if org and member["org_slug"] != org.strip().lower():
                continue
            token = self._issue_email_token(
                member["org_id"], member["member_id"], "reset"
            )
            self.app.mail.send(
                member["org_id"],
                member["email"],
                "password_reset",
                name=member["name"] or member["email"],
                org=member["org_name"],
                link=f"{self.app.config.base_url}/#/reset/{token}",
            )

    def reset_password(self, token: str, new_password: str) -> dict[str, Any]:
        digest = crypto.hash_token(token or "")
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM email_tokens WHERE token_hash = ? AND kind = 'reset'",
                (digest,),
            )
            member = (
                one(
                    connection,
                    "SELECT email FROM members WHERE member_id = ?",
                    (row["member_id"],),
                )
                if row
                else None
            )
        if row is None or member is None:
            raise NotFound("This link is not valid.")
        crypto.check_password_policy(new_password, member["email"])
        self._consume_email_token(token, "reset")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE members SET password_hash = ?, failed_logins = 0, locked_until = NULL, email_verified_at = COALESCE(email_verified_at, ?) WHERE member_id = ? AND org_id = ?",
                (
                    crypto.hash_password(new_password),
                    self.now,
                    row["member_id"],
                    row["org_id"],
                ),
            )
            self.end_all_sessions(row["org_id"], row["member_id"], connection)
            self.app.audit.record(
                row["org_id"],
                "auth.password_reset",
                actor_type="member",
                actor_id=row["member_id"],
                target_type="member",
                target_id=row["member_id"],
                connection=connection,
            )
        return {"reset": True}

    # ---------------------------------------------------------------- invitations

    def invite(
        self,
        actor: Principal,
        email: str,
        role: str,
        *,
        coach_id: str = "",
        coach_scope: str = "",
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "members.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        email = crypto.normalize_email(email)
        if role not in ORG_ROLES:
            raise ValidationFailed(
                "Choose a role: owner, admin, coach, candidate, billing contact or auditor."
            )
        if role == OWNER:
            self.app.authz.require(actor, "members.manage_owners")
        if coach_id:
            coach = self.app.tenancy.member(actor.org_id, coach_id)
            if coach["role"] != COACH:
                raise ValidationFailed("The coach for an invitation must be a coach.")
            if role != CANDIDATE:
                raise ValidationFailed("Only a candidate can be assigned a coach.")
            if (coach_scope or "view") not in COACH_SCOPES:
                raise ValidationFailed("Coach scope must be view, act or approve.")
        with self.db.read() as connection:
            if one(
                connection,
                "SELECT 1 AS x FROM members WHERE org_id = ? AND email = ? AND removed_at IS NULL",
                (actor.org_id, email),
            ):
                raise Conflict(
                    "This email already belongs to a member of the organization."
                )
        self.app.tenancy.require_seat(actor.org_id, role, replacing_invite_email=email)
        token = crypto.new_token()
        invite_id = new_id()
        expires = self.now + self.app.config.invite_ttl
        with self.db.write() as connection:
            connection.execute(
                "UPDATE invites SET revoked_at = ? WHERE org_id = ? AND email = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                (self.now, actor.org_id, email),
            )
            connection.execute(
                """INSERT INTO invites (invite_id, org_id, email, role, token_hash, coach_id, coach_scope, invited_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invite_id,
                    actor.org_id,
                    email,
                    role,
                    crypto.hash_token(token),
                    coach_id,
                    coach_scope or ("view" if coach_id else ""),
                    actor.actor_id,
                    self.now,
                    expires,
                ),
            )
            self.app.audit.record(
                actor.org_id,
                "member.invite",
                actor=actor,
                target_type="invite",
                target_id=invite_id,
                detail={"email": email, "role": role},
                connection=connection,
            )
        self._send_invite(
            actor.org_id, email, role, token, expires, actor.name or actor.email
        )
        return {
            "invite_id": invite_id,
            "email": email,
            "role": role,
            "expires_at": expires,
        }

    def _send_invite(
        self, org_id: str, email: str, role: str, token: str, expires: int, inviter: str
    ) -> None:
        import time

        self.app.mail.send(
            org_id,
            email,
            "invite",
            inviter=inviter,
            org=self.app.tenancy.org(org_id)["name"],
            role=role,
            link=f"{self.app.config.base_url}/#/invite/{quote(token)}",
            expires=time.strftime("%Y-%m-%d", time.gmtime(expires)),
        )

    def list_invites(
        self, actor: Principal, *, pending_only: bool = True
    ) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "members.manage")
        clause = (
            "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?"
            if pending_only
            else "AND ? IS NOT NULL"
        )
        with self.db.read() as connection:
            return many(
                connection,
                f"""SELECT invite_id, email, role, coach_id, coach_scope, invited_by, created_at, expires_at, accepted_at, revoked_at, resent_count
                FROM invites WHERE org_id = ? {clause} ORDER BY created_at DESC""",
                (actor.org_id, self.now),
            )

    def resend_invite(self, actor: Principal, invite_id: str) -> dict[str, Any]:
        self.app.authz.require(actor, "members.manage")
        with self.db.read() as connection:
            invite = one(
                connection,
                "SELECT * FROM invites WHERE invite_id = ? AND org_id = ?",
                (invite_id, actor.org_id),
            )
        if invite is None or invite["accepted_at"] or invite["revoked_at"]:
            raise NotFound("Invitation not found.")
        token = crypto.new_token()
        expires = self.now + self.app.config.invite_ttl
        with self.db.write() as connection:
            connection.execute(
                "UPDATE invites SET token_hash = ?, expires_at = ?, resent_count = resent_count + 1 WHERE invite_id = ? AND org_id = ?",
                (crypto.hash_token(token), expires, invite_id, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "member.invite_resend",
                actor=actor,
                target_type="invite",
                target_id=invite_id,
                connection=connection,
            )
        self._send_invite(
            actor.org_id,
            invite["email"],
            invite["role"],
            token,
            expires,
            actor.name or actor.email,
        )
        return {"invite_id": invite_id, "expires_at": expires}

    def revoke_invite(self, actor: Principal, invite_id: str) -> None:
        self.app.authz.require(actor, "members.manage")
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE invites SET revoked_at = ? WHERE invite_id = ? AND org_id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                (self.now, invite_id, actor.org_id),
            ).rowcount
            if not changed:
                raise NotFound("Invitation not found.")
            self.app.audit.record(
                actor.org_id,
                "member.invite_revoke",
                actor=actor,
                target_type="invite",
                target_id=invite_id,
                connection=connection,
            )

    def invite_info(self, token: str) -> dict[str, Any]:
        invite = self._invite_by_token(token)
        org = self.app.tenancy.org(invite["org_id"])
        return {
            "email": invite["email"],
            "role": invite["role"],
            "org": {"name": org["name"], "slug": org["slug"]},
            "expires_at": invite["expires_at"],
        }

    def _invite_by_token(self, token: str) -> dict[str, Any]:
        with self.db.read() as connection:
            invite = one(
                connection,
                "SELECT * FROM invites WHERE token_hash = ?",
                (crypto.hash_token(token or ""),),
            )
        if invite is None:
            raise NotFound("This invitation is not valid.")
        if invite["accepted_at"]:
            raise Gone("This invitation was already used.")
        if invite["revoked_at"]:
            raise Gone("This invitation was withdrawn.")
        if invite["expires_at"] <= self.now:
            raise Gone("This invitation has expired. Ask for a new one.")
        return invite

    def accept_invite(
        self,
        token: str,
        password: str,
        *,
        name: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        invite = self._invite_by_token(token)
        crypto.check_password_policy(password, invite["email"])
        org_id = invite["org_id"]
        self.app.entitlements.require_state(org_id, write=True)
        self.app.tenancy.require_seat(
            org_id, invite["role"], accepting_invite=invite["invite_id"]
        )
        member_id = new_id()
        with self.db.write() as connection:
            claimed = connection.execute(
                "UPDATE invites SET accepted_at = ?, accepted_member_id = ? WHERE invite_id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                (self.now, member_id, invite["invite_id"]),
            ).rowcount
            if not claimed:
                raise Gone("This invitation was already used.")
            try:
                connection.execute(
                    """INSERT INTO members (member_id, org_id, email, name, password_hash, role, status, email_verified_at, invited_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                    (
                        member_id,
                        org_id,
                        invite["email"],
                        name.strip()[:120],
                        crypto.hash_password(password),
                        invite["role"],
                        self.now,
                        invite["invited_by"],
                        self.now,
                    ),
                )
            except INTEGRITY_ERRORS:
                raise Conflict(
                    "This email already belongs to a member of the organization."
                ) from None
            if invite["role"] == CANDIDATE:
                self.app.workspaces.create(org_id, member_id, connection=connection)
            if invite["coach_id"]:
                connection.execute(
                    "INSERT OR IGNORE INTO coach_assignments (org_id, coach_id, candidate_id, scope, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        org_id,
                        invite["coach_id"],
                        member_id,
                        invite["coach_scope"] or "view",
                        invite["invited_by"],
                        self.now,
                    ),
                )
            self.app.audit.record(
                org_id,
                "member.join",
                actor_type="member",
                actor_id=member_id,
                actor_label=f"{invite['role']}:{invite['email']}",
                actor_role=invite["role"],
                target_type="member",
                target_id=member_id,
                detail={"invite_id": invite["invite_id"]},
                connection=connection,
            )
        session_token, session_id = self._create_session(
            org_id,
            member_id,
            ip=ip,
            user_agent=user_agent,
            mfa_verified=True,
            auth_method="invite",
        )
        return {
            "token": session_token,
            "session_id": session_id,
            "member_id": member_id,
            "org_id": org_id,
            "role": invite["role"],
        }

    # ---------------------------------------------------------------- operators

    def bootstrap_operator(
        self, email: str, password: str, *, name: str = "Platform admin"
    ) -> str:
        """Create the first platform admin (from configuration) if no operator exists."""
        with self.db.read() as connection:
            if one(connection, "SELECT 1 AS x FROM operators LIMIT 1"):
                return ""
        return self.create_operator(
            email, password, role="platform_admin", name=name, by="bootstrap"
        )

    def create_operator(
        self, email: str, password: str, *, role: str, name: str = "", by: str = ""
    ) -> str:
        email = crypto.normalize_email(email)
        crypto.check_password_policy(password, email)
        if role not in ("support", "platform_admin"):
            raise ValidationFailed("Operator role must be support or platform_admin.")
        operator_id = new_id()
        with self.db.write() as connection:
            if one(
                connection, "SELECT 1 AS x FROM operators WHERE email = ?", (email,)
            ):
                raise Conflict("An operator with this email exists.")
            connection.execute(
                "INSERT INTO operators (operator_id, email, name, password_hash, role, status, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?)",
                (
                    operator_id,
                    email,
                    name[:120],
                    crypto.hash_password(password),
                    role,
                    self.now,
                ),
            )
            self.app.platform_audit.record(
                "operator.create",
                operator_label=by or "system",
                target_type="operator",
                target_id=operator_id,
                detail={"role": role, "email": email},
                connection=connection,
            )
        return operator_id

    def operator_login(
        self, email: str, password: str, *, ip: str = "", user_agent: str = ""
    ) -> dict[str, Any]:
        email = str(email or "").strip().lower()
        with self.db.read() as connection:
            operator = one(
                connection, "SELECT * FROM operators WHERE email = ?", (email,)
            )
        if operator is None or operator["status"] != "active":
            crypto.verify_password(password or "", DUMMY_HASH)
            raise Unauthorized("Email or password is incorrect.")
        if operator["locked_until"] and operator["locked_until"] > self.now:
            raise Locked(
                "Too many failed sign-ins. Try again later.",
                retry_after=operator["locked_until"] - self.now,
            )
        if not crypto.verify_password(password or "", operator["password_hash"]):
            with self.db.write() as connection:
                attempts = operator["failed_logins"] + 1
                locked = (
                    self.now + self.app.config.lockout_seconds
                    if attempts >= self.app.config.max_failed_logins
                    else None
                )
                connection.execute(
                    "UPDATE operators SET failed_logins = ?, locked_until = ? WHERE operator_id = ?",
                    (0 if locked else attempts, locked, operator["operator_id"]),
                )
            raise Unauthorized("Email or password is incorrect.")
        token = crypto.new_token()
        with self.db.write() as connection:
            connection.execute(
                "UPDATE operators SET failed_logins = 0, locked_until = NULL, last_sign_in_at = ? WHERE operator_id = ?",
                (self.now, operator["operator_id"]),
            )
            connection.execute(
                """INSERT INTO sessions (session_id, token_hash, kind, operator_id, mfa_verified, auth_method, ip, user_agent, created_at, last_seen_at, expires_at)
                VALUES (?, ?, 'operator', ?, 0, 'password', ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    crypto.hash_token(token),
                    operator["operator_id"],
                    ip[:64],
                    user_agent[:200],
                    self.now,
                    self.now,
                    self.now + self.app.config.operator_session_ttl,
                ),
            )
            self.app.platform_audit.record(
                "operator.login",
                operator_label=f"operator:{operator['email']}",
                target_type="operator",
                target_id=operator["operator_id"],
                connection=connection,
            )
        return {
            "token": token,
            "mfa_enrolled": bool(operator["mfa_enabled_at"]),
            "operator_id": operator["operator_id"],
        }


__all__ = ["Identity", "slugify", "MfaRequired", "PlanRequired"]
