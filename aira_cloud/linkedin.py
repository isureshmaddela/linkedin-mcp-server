"""Remote LinkedIn sign-in sessions.

A candidate signs in to LinkedIn in a browser that runs on a worker host (their profile never leaves that
tenant's volume). When a remote-desktop gateway is configured (``AIRA_CLOUD_REMOTE_LOGIN_URL``, with a
``{token}`` placeholder), connecting returns a short-lived, single-candidate link to it. The gateway calls
``validate`` (signed with the agent secret) to learn which candidate's display the token opens, once, before
the token expires. Without a gateway the sign-in window opens on the worker host itself (a development or
single-machine setup).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aira_cloud import crypto
from aira_cloud.db import one
from aira_cloud.errors import Gone, NotFound, ValidationFailed

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp


class LinkedInSessions:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def create_remote_login(self, org_id: str, candidate_id: str) -> str:
        token = crypto.new_token(24)
        now = self.app.clock.now()
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE remote_login_sessions SET state = 'revoked' WHERE org_id = ? AND candidate_id = ? AND state IN ('pending', 'used', 'active')",
                (org_id, candidate_id),
            )
            connection.execute(
                "INSERT INTO remote_login_sessions (token_hash, org_id, candidate_id, state, created_at, expires_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                (
                    crypto.hash_token(token),
                    org_id,
                    candidate_id,
                    now,
                    now + self.app.config.remote_login_ttl,
                ),
            )
            self.app.audit.record(
                org_id,
                "linkedin.remote_login_issued",
                actor_type="member",
                actor_id=candidate_id,
                candidate_id=candidate_id,
                detail={"ttl": self.app.config.remote_login_ttl},
                connection=connection,
            )
        return token

    def validate(self, token: str) -> dict[str, Any]:
        """Used once by the remote-desktop gateway: which candidate's browser does this token open?"""
        digest = crypto.hash_token(token or "")
        now = self.app.clock.now()
        with self.app.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM remote_login_sessions WHERE token_hash = ?",
                (digest,),
            )
            if row is None:
                raise NotFound("This sign-in link is not valid.")
            if row["state"] != "pending" or row["used_at"]:
                raise Gone("This sign-in link was already used or replaced.")
            if row["expires_at"] <= now:
                raise Gone("This sign-in link has expired. Start again from Aira.")
            connection.execute(
                "UPDATE remote_login_sessions SET state = 'used', used_at = ? WHERE token_hash = ?",
                (now, digest),
            )
            workspace = one(
                connection,
                "SELECT linkedin_host FROM workspaces WHERE candidate_id = ? AND org_id = ?",
                (row["candidate_id"], row["org_id"]),
            )
        return {
            "org_id": row["org_id"],
            "candidate_id": row["candidate_id"],
            "agent_url": (workspace or {}).get("linkedin_host", ""),
            "profile_dir": str(
                self.app.workspaces.candidate_dir(row["org_id"], row["candidate_id"])
                / "linkedin"
                / "profile"
            ),
            "expires_at": row["expires_at"],
        }

    # -- the broker reports what it does with a validated token ---------------------------

    def remote_login_event(
        self, token: str, event: str, outcome: str = ""
    ) -> dict[str, Any]:
        """``started`` and ``finished`` are audited; ``heartbeat`` only answers whether the session may go on.

        The broker is trusted to say what happened (its requests are signed) but not who it was for: the
        organization and candidate come from the token's own row.
        """
        if event not in {"started", "heartbeat", "finished"}:
            raise ValidationFailed("Unknown event.")
        digest = crypto.hash_token(token or "")
        now = self.app.clock.now()
        with self.app.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM remote_login_sessions WHERE token_hash = ?",
                (digest,),
            )
            if row is None:
                raise NotFound("This sign-in link is not valid.")
            state = row["state"]
            if event == "started":
                if state != "used":
                    raise Gone("This sign-in was already started or replaced.")
                connection.execute(
                    "UPDATE remote_login_sessions SET state = 'active' WHERE token_hash = ?",
                    (digest,),
                )
                self._audit_remote(connection, row, "auth.remote_login_started", {})
                return {"active": True}
            if event == "heartbeat":
                return {
                    "active": state == "active"
                    and self._candidate_open(connection, row)
                }
            if state in {"pending", "finished"}:
                return {
                    "active": False
                }  # never started, or its end was already recorded
            connection.execute(
                "UPDATE remote_login_sessions SET state = 'finished' WHERE token_hash = ?",
                (digest,),
            )
            self._audit_remote(
                connection,
                row,
                "auth.remote_login_finished",
                {
                    "outcome": str(outcome)[:40] or "unknown",
                    "seconds": max(0, now - int(row["used_at"] or now)),
                },
            )
        return {"active": False}

    def _audit_remote(
        self, connection: Any, row: dict[str, Any], action: str, detail: dict[str, Any]
    ) -> None:
        self.app.audit.record(
            row["org_id"],
            action,
            actor_type="system",
            actor_label="sign-in broker",
            candidate_id=row["candidate_id"],
            target_type="candidate",
            target_id=row["candidate_id"],
            detail=detail,
            source="broker",
            connection=connection,
        )

    @staticmethod
    def _candidate_open(connection: Any, row: dict[str, Any]) -> bool:
        org = one(
            connection,
            "SELECT status FROM organizations WHERE org_id = ?",
            (row["org_id"],),
        )
        workspace = one(
            connection,
            "SELECT status FROM workspaces WHERE org_id = ? AND candidate_id = ?",
            (row["org_id"], row["candidate_id"]),
        )
        return bool(
            org
            and org["status"] not in {"suspended", "deleted"}
            and workspace
            and workspace["status"] == "active"
        )
