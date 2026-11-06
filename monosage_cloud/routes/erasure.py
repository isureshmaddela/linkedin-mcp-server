"""A member deletes their own account (``POST /api/me/delete-account``).

Their current password confirms it. Everything of theirs goes, as a data subject request would do it
(:meth:`monosage_cloud.lifecycle.Lifecycle._delete_member_data`): their life apps, Social, Videos, the mail and
webhook payloads that named them (:mod:`monosage_cloud.erasure_hooks`), their careers workspace and own business
space, and their membership (its row is anonymised and every session ends). The organization's last owner closes
the organization instead, and nothing is deleted while the organization is under a legal hold.
"""

from __future__ import annotations

from monosage_cloud import crypto
from monosage_cloud.db import one
from monosage_cloud.errors import Conflict, Forbidden, Unauthorized
from monosage_cloud.rbac import OWNER, Principal
from monosage_cloud.routes.auth import clear_cookie
from monosage_cloud.webio import Reply, Req, cloud_route


@cloud_route("POST", r"/api/me/delete-account", writes=True)
def delete_own_account(req: Req):  # noqa: ANN201
    principal = req.principal
    if principal.is_operator or principal.via_support:
        raise Forbidden("Only the person themselves deletes their account.")
    org_id, member_id = principal.org_id, principal.member_id
    with req.app.db.read() as connection:
        row = one(
            connection,
            "SELECT role, password_hash FROM members WHERE org_id = ? AND member_id = ? AND removed_at IS NULL",
            (org_id, member_id),
        )
    if row is None:
        raise Forbidden("This account is already closed.")
    if not row["password_hash"] or not crypto.verify_password(
        str(req.body().get("password") or ""), row["password_hash"]
    ):
        raise Unauthorized(
            "Type your current password to delete your account. If you sign in through your organization, ask an owner to remove you."
        )
    if row["role"] == OWNER and req.app.tenancy._owner_count(org_id) <= 1:
        raise Conflict(
            "You are the organization's only owner: close the organization instead, or make someone else an owner first."
        )
    if req.app.lifecycle.on_legal_hold(org_id):
        raise Conflict(
            "Your organization keeps its records under a legal hold right now. Ask an owner when it ends."
        )
    actor = Principal(
        kind="member",
        org_id=org_id,
        role=row["role"],
        member_id=member_id,
        email="self-service",
    )
    req.app.lifecycle._delete_member_data(
        actor, {"org_id": org_id, "member_id": member_id, "role": row["role"]}
    )
    req.app.lifecycle.recompute_over_limits(org_id)
    return Reply(200, {"deleted": True}, cookies=[clear_cookie(req)])
