"""Launch tokens: open a local installation of the apps as the account that is signed in here.

A candidate who is signed in to the hosted account asks ``GET /api/launch/local?return=<local app>`` and is
sent to the local installation with a one-time token in the fragment. The local installation validates the
token with ``POST /api/internal/launch/validate`` (signed with ``AIRA_CLOUD_LAUNCH_SECRET``, the purpose
label ``launch``), learns who the person is, and signs them in there. The token lives 60 seconds, is stored
hashed, is bound to the organization, the member and the exact return address, and can be used once.

Both settings must be present for the feature to exist: ``AIRA_CLOUD_LOCAL_APP_URL`` (where the local
installation is) and ``AIRA_CLOUD_LAUNCH_SECRET`` (shared with it as ``AIRA_ACCOUNT_SECRET``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from aira import apps as app_catalog
from aira.security import RateLimiter
from aira_cloud import crypto
from aira_cloud.db import one
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    Gone,
    NotFound,
    TooManyRequests,
    ValidationFailed,
)
from aira_cloud.rbac import CANDIDATE, Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

APP_ID = "local"
ISSUE_LIMIT = 5  # tokens a member may ask for per minute
MIN_SECRET_CHARS = 32


def origin_of(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    port = parts.port
    default = 443 if parts.scheme == "https" else 80
    host = parts.hostname.lower()
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else "")


class LaunchTokens:
    def __init__(self, app: CloudApp) -> None:
        self.app = app
        self._limiter = RateLimiter(limit=ISSUE_LIMIT, window_seconds=60)

    # ------------------------------------------------------------------ configuration

    @property
    def enabled(self) -> bool:
        config = self.app.config
        return (
            bool(config.local_app_url)
            and len(config.launch_secret or "") >= MIN_SECRET_CHARS
        )

    def local_url(self) -> str:
        return str(self.app.config.local_app_url or "").rstrip("/")

    # ------------------------------------------------------------------ issuing

    def check_return(self, return_url: str) -> str:
        """The return address must be on the local installation's origin, with a plain path and nothing else."""
        text = str(return_url or "").strip()
        if not text:
            text = self.local_url() + "/"
        parts = urlsplit(text)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValidationFailed(
                "The return address must be a plain page address on the local installation."
            )
        expected = origin_of(self.local_url())
        if not expected or origin_of(text) != expected:
            raise ValidationFailed(
                "The return address is not the local installation this account knows."
            )
        path = parts.path or "/"
        if not path.startswith("/") or ".." in path:
            raise ValidationFailed(
                "The return address must be a plain page address on the local installation."
            )
        return f"{expected}{path}"

    def issue(self, principal: Principal, return_url: str) -> dict[str, Any]:
        """A one-time token for ``principal`` to open the local installation; returns the token and the redirect."""
        if not self.enabled:
            raise Conflict(
                "Opening a local installation is not set up on this account (AIRA_CLOUD_LOCAL_APP_URL and "
                "AIRA_CLOUD_LAUNCH_SECRET).",
                code="not_configured",
            )
        if principal.is_operator or principal.kind != "member":
            raise Forbidden(
                "Only a member of the organization can open the local installation."
            )
        if principal.role != CANDIDATE:
            raise Forbidden(
                "Only a candidate opens their own local installation; staff cannot open it for someone else."
            )
        self.app.entitlements.require_state(principal.org_id, write=True)
        target = self.check_return(return_url)
        key = f"launch:{principal.org_id}:{principal.member_id}"
        allowed, retry = self._limiter.check(key)
        if not allowed:
            raise TooManyRequests(
                f"Too many launch requests. Try again in {retry} seconds.",
                retry_after=retry,
            )
        self._limiter.hit(key)
        token = crypto.new_token(32)
        now = self.app.clock.now()
        ttl = int(self.app.config.launch_ttl)
        with self.app.db.write() as connection:
            connection.execute(
                "DELETE FROM launch_tokens WHERE org_id = ? AND member_id = ? AND (used_at IS NOT NULL OR expires_at <= ?)",
                (principal.org_id, principal.member_id, now),
            )
            connection.execute(
                """INSERT INTO launch_tokens (token_hash, org_id, member_id, app, return_url, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    crypto.hash_token(token),
                    principal.org_id,
                    principal.member_id,
                    APP_ID,
                    target,
                    now,
                    now + ttl,
                ),
            )
            self.app.audit.record(
                principal.org_id,
                "auth.launch_issued",
                actor=principal,
                candidate_id=principal.member_id,
                target_type="app",
                target_id=APP_ID,
                detail={"return": target, "ttl": ttl},
                connection=connection,
            )
        separator = "" if target.endswith("/") else "/"
        return {
            "token": token,
            "expires_in": ttl,
            "redirect": f"{target}{separator}#/launch?token={quote(token, safe='')}",
        }

    # ------------------------------------------------------------------ validating

    def validate(self, token: str) -> dict[str, Any]:
        """Used once by the local installation: who does this token sign in, and to what?"""
        if not self.enabled:
            raise NotFound(
                "Opening a local installation is not set up on this account."
            )
        digest = crypto.hash_token(str(token or ""))
        now = self.app.clock.now()
        with self.app.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM launch_tokens WHERE token_hash = ?",
                (digest,),
            )
            if row is None:
                raise NotFound("This launch link is not valid.")
            if row["used_at"]:
                raise Gone(
                    "This launch link was already used. Start again from your account."
                )
            if row["expires_at"] <= now:
                raise Gone(
                    "This launch link has expired. Start again from your account."
                )
            connection.execute(
                "UPDATE launch_tokens SET used_at = ? WHERE token_hash = ?",
                (now, digest),
            )
            member = one(
                connection,
                "SELECT member_id, org_id, email, name, role FROM members WHERE member_id = ? AND org_id = ? AND removed_at IS NULL AND status = 'active'",
                (row["member_id"], row["org_id"]),
            )
            org = one(
                connection,
                "SELECT org_id, name, slug FROM organizations WHERE org_id = ? AND deleted_at IS NULL",
                (row["org_id"],),
            )
            if member is None or org is None:
                raise Gone("This account is no longer a member of the organization.")
            self.app.audit.record(
                row["org_id"],
                "auth.launch_local",
                actor_type="member",
                actor_id=member["member_id"],
                actor_label=member["email"],
                actor_role=member["role"],
                candidate_id=member["member_id"],
                target_type="app",
                target_id=row["app"],
                detail={"return": row["return_url"]},
                source="launch",
                connection=connection,
            )
        self.app.entitlements.require_state(row["org_id"], write=True)
        return {
            "email": member["email"],
            "name": member["name"] or "",
            "org_id": org["org_id"],
            "org_name": org["name"],
            "org_slug": org["slug"],
            "member_id": member["member_id"],
            "role": member["role"],
            "app": row["app"],
            "apps": self._apps(row["org_id"]),
        }

    def _apps(self, org_id: str) -> list[str]:
        """The apps this organization may open (the plan's features, or the subscriptions when they exist)."""
        entitlements = self.app.entitlements.resolve(org_id)
        subscribed = getattr(entitlements, "apps", None)
        if isinstance(subscribed, list | tuple | set | frozenset):
            return [app.id for app in app_catalog.APPS if app.id in subscribed]
        return [
            app.id
            for app in app_catalog.APPS
            if not app.feature or entitlements.has(app.feature)
        ]
