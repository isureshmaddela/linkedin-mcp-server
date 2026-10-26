"""Sign-up, sign-in, sessions, MFA, passwords, invitations and "who am I"."""

from __future__ import annotations

from typing import Any

from aira_cloud.db import one
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
)
from aira_cloud.policy import LINKEDIN_NOTICE
from aira_cloud.tenancy import CONSENT_VERSION
from aira_cloud.rbac import (
    CANDIDATE,
    CANDIDATE_ONLY,
    CANDIDATE_PERMISSION_LEVEL,
    COACH,
    ORG_PERMISSIONS,
    Principal,
)
from aira_cloud.webio import (
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    Reply,
    Req,
    cloud_route,
    cookie_header,
)


def session_cookie(req: Req, token: str) -> str:
    return cookie_header(
        SESSION_COOKIE,
        token,
        max_age=req.app.config.session_ttl,
        secure=req.app.config.cookie_secure,
    )


def clear_cookie(req: Req, name: str = SESSION_COOKIE) -> str:
    return cookie_header(name, "", max_age=0, secure=req.app.config.cookie_secure)


def me_payload(app: Any, principal: Principal) -> dict[str, Any]:
    """Everything the UI needs to decide what to show: identity, organization, plan, permissions and rules."""
    if principal.is_operator:
        base: dict[str, Any] = {
            "kind": "operator",
            "email": principal.email,
            "name": principal.name,
            "operator_role": principal.operator_role,
        }
        if not principal.via_support:
            return base
        base["support"] = {
            "access_id": principal.support_access_id,
            "role": principal.role,
        }
    org = app.tenancy.org(principal.org_id)
    entitlements = app.entitlements.resolve(principal.org_id)
    policy = app.tenancy.policy(principal.org_id)
    role = principal.role
    permissions = sorted(ORG_PERMISSIONS.get(role, frozenset()))
    candidate_permissions: list[str] = []
    if role == CANDIDATE:
        candidate_permissions = sorted(set(CANDIDATE_PERMISSION_LEVEL) | CANDIDATE_ONLY)
    assigned: list[dict[str, Any]] = []
    if role == COACH:
        for row in app.tenancy.list_assignments(principal):
            assigned.append(
                {
                    "candidate_id": row["candidate_id"],
                    "name": row["candidate_name"] or row["candidate_email"],
                    "scope": row["effective_scope"],
                }
            )
    payload: dict[str, Any] = {
        "kind": "member",
        "email": principal.email,
        "name": principal.name,
        "member_id": principal.member_id,
        "user_id": principal.member_id,
        "role": role,
        "org": {
            "org_id": org["org_id"],
            "name": org["name"],
            "slug": org["slug"],
            "region": org["region"],
            "logo_url": (org["branding"] or {}).get("logo_url", "")
            if entitlements.has("branding")
            else "",
        },
        "verified": bool(principal.extra.get("email_verified", True)),
        "mfa": app.identity.mfa_status(principal),
        "plan": {
            "plan_id": entitlements.plan_id,
            "status": entitlements.status,
            "read_only": entitlements.read_only,
            "over_limits": list(entitlements.over_limits),
            "trial_end": entitlements.trial_end,
            "period_end": entitlements.period_end,
        },
        "features": sorted(entitlements.features),
        "apps": app.billing.apps_for_member(principal.org_id, entitlements),
        "coach_scopes": entitlements.coach_scopes(),
        "permissions": permissions,
        "candidate_permissions": candidate_permissions,
        "assigned_candidates": assigned,
        "auth_method": principal.auth_method,
        "policy": {
            "submission_mode": policy["submission_mode"],
            "linkedin_allowed": bool(policy["linkedin_allowed"]),
            "cover_letters": bool(policy["cover_letters"]),
        },
        "paused": bool(org["paused_at"]),
    }
    if principal.is_operator:
        payload.update(
            base
        )  # a support session: the operator's identity and the grant ride along with the granted role
    if role == CANDIDATE:
        workspace = app.workspaces.row(principal.org_id, principal.member_id)
        payload["linkedin_connected"] = bool(workspace["linkedin_port"])
        payload["workspace"] = {
            "stage": workspace["stage"],
            "paused": bool(workspace["paused_at"]),
            "status": workspace["status"],
            "linkedin_opted_in": bool(workspace["linkedin_opt_in_at"]),
        }
        payload["consent"] = app.tenancy.consent_state(
            principal.org_id, principal.member_id
        )
    elif not principal.is_operator:
        payload["personal_workspace"] = app.tenancy.personal_workspace(principal)
    return payload


@cloud_route("GET", r"/api/auth/me", verified=False, mfa=False)
def auth_me(req: Req):  # noqa: ANN201
    return 200, me_payload(req.app, req.principal)


@cloud_route("GET", r"/api/cloud/config", auth="public")
def cloud_config(req: Req):  # noqa: ANN201
    config = req.app.config
    return 200, {
        "hosted": True,
        "region": config.region,
        "billing_provider": config.billing_provider,
        "trial_days": config.trial_days,
        "plans": req.app.billing.public_plans(),
    }


@cloud_route("POST", r"/api/auth/signup-org", auth="public")
def signup_org(req: Req):  # noqa: ANN201
    body = req.body()
    result = req.app.identity.signup_org(
        str(body.get("organization", "")),
        str(body.get("email", "")),
        str(body.get("password", "")),
        owner_name=str(body.get("name", "")),
        ip=req.request.client_ip,
        user_agent=req.request.headers.get("user-agent", ""),
    )
    return Reply(
        201,
        {"org": result["org"], "member": result["member"], "verification_sent": True},
        cookies=[session_cookie(req, result["token"])],
    )


@cloud_route("POST", r"/api/auth/login", auth="public")
def login(req: Req):  # noqa: ANN201
    body = req.body()
    email = str(body.get("email", "")).strip().lower()
    limiter = req.app.gateway.auth_limiter
    key = f"login:{req.request.client_ip}:{email}"
    result = None
    try:
        result = req.app.identity.login(
            email,
            str(body.get("password", "")),
            org=str(body.get("org", "")),
            ip=req.request.client_ip,
            user_agent=req.request.headers.get("user-agent", ""),
        )
    except Unauthorized:
        limiter.hit(key)
        raise
    limiter.reset(f"auth:{req.request.client_ip}")
    principal = req.app.identity.principal_for_token(result["token"])
    return Reply(
        200,
        {
            "ok": True,
            "mfa_required": result["mfa_required"],
            "me": me_payload(req.app, principal) if principal else None,
        },
        cookies=[session_cookie(req, result["token"])],
    )


@cloud_route("POST", r"/api/auth/logout", auth="public")
def logout(req: Req):  # noqa: ANN201
    req.app.identity.logout(req.request.cookie(SESSION_COOKIE))
    return Reply(200, {"ok": True}, cookies=[clear_cookie(req)])


@cloud_route("POST", r"/api/auth/verify-email", auth="public")
def verify_email(req: Req):  # noqa: ANN201
    return 200, req.app.identity.verify_email(str(req.body().get("token", "")))


@cloud_route("POST", r"/api/auth/resend-verification", verified=False, mfa=False)
def resend_verification(req: Req):  # noqa: ANN201
    req.app.identity.resend_verification(req.principal)
    return 200, {"sent": True}


@cloud_route("GET", r"/api/auth/mfa", verified=False, mfa=False)
def mfa_status(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_status(req.principal)


@cloud_route("POST", r"/api/auth/mfa/begin", verified=False, mfa=False)
def mfa_begin(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_begin(req.principal)


@cloud_route("POST", r"/api/auth/mfa/enable", verified=False, mfa=False)
def mfa_enable(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_enable(
        req.principal, str(req.body().get("code", ""))
    )


@cloud_route("POST", r"/api/auth/mfa/verify", verified=False, mfa=False)
def mfa_verify(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_verify(
        req.principal, str(req.body().get("code", ""))
    )


@cloud_route("POST", r"/api/auth/mfa/disable")
def mfa_disable(req: Req):  # noqa: ANN201
    body = req.body()
    req.app.identity.mfa_disable(
        req.principal, str(body.get("password", "")), str(body.get("code", ""))
    )
    return 200, {"enabled": False}


@cloud_route("POST", r"/api/auth/password/forgot", auth="public")
def password_forgot(req: Req):  # noqa: ANN201
    body = req.body()
    req.app.identity.request_password_reset(
        str(body.get("email", "")), str(body.get("org", ""))
    )
    return 200, {
        "ok": True,
        "message": "If that address belongs to an account, a reset link is on its way.",
    }


@cloud_route("POST", r"/api/auth/password/reset", auth="public")
def password_reset(req: Req):  # noqa: ANN201
    body = req.body()
    return 200, req.app.identity.reset_password(
        str(body.get("token", "")), str(body.get("password", ""))
    )


@cloud_route("POST", r"/api/auth/password/change", verified=False)
def password_change(req: Req):  # noqa: ANN201
    body = req.body()
    token = req.app.identity.change_password(
        req.principal, str(body.get("current", "")), str(body.get("password", ""))
    )
    return Reply(200, {"changed": True}, cookies=[session_cookie(req, token)])


@cloud_route("GET", r"/api/auth/sessions", verified=False)
def sessions(req: Req):  # noqa: ANN201
    return 200, {"sessions": req.app.identity.list_sessions(req.principal)}


@cloud_route("DELETE", r"/api/auth/sessions/([0-9a-f]{32})", verified=False)
def session_revoke(req: Req):  # noqa: ANN201
    req.app.identity.revoke_session(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route("POST", r"/api/auth/sessions/sign-out-others", verified=False)
def sign_out_others(req: Req):  # noqa: ANN201
    return 200, {"signed_out": req.app.identity.revoke_other_sessions(req.principal)}


@cloud_route("PATCH", r"/api/auth/profile", verified=False)
def update_profile(req: Req):  # noqa: ANN201
    req.app.tenancy.update_name(req.principal, str(req.body().get("name", "")))
    return 200, me_payload(
        req.app,
        req.app.identity.principal_for_token(req.request.cookie(SESSION_COOKIE)),
    )


@cloud_route("GET", r"/api/invites/([A-Za-z0-9_-]{20,80})", auth="public")
def invite_info(req: Req):  # noqa: ANN201
    return 200, req.app.identity.invite_info(req.group())


@cloud_route("POST", r"/api/invites/([A-Za-z0-9_-]{20,80})/accept", auth="public")
def invite_accept(req: Req):  # noqa: ANN201
    body = req.body()
    result = req.app.identity.accept_invite(
        req.group(),
        str(body.get("password", "")),
        name=str(body.get("name", "")),
        ip=req.request.client_ip,
        user_agent=req.request.headers.get("user-agent", ""),
    )
    principal = req.app.identity.principal_for_token(result["token"])
    return Reply(
        201,
        {"ok": True, "role": result["role"], "me": me_payload(req.app, principal)},
        cookies=[session_cookie(req, result["token"])],
    )


# ----------------------------------------------------------------------------- an owner's or admin's own workspace


@cloud_route("GET", r"/api/workspace/own")
def own_workspace(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.personal_workspace(req.principal)


@cloud_route("POST", r"/api/workspace/own", writes=True)
def own_workspace_start(req: Req):  # noqa: ANN201
    """An owner or admin starts using the apps themselves (one candidate seat)."""
    return 201, req.app.tenancy.start_personal_workspace(req.principal)


@cloud_route("DELETE", r"/api/workspace/own", writes=True)
def own_workspace_end(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.end_personal_workspace(req.principal)


# ----------------------------------------------------------------------------- candidate self-service


@cloud_route("GET", r"/api/me/consent", verified=False)
def consent_get(req: Req):  # noqa: ANN201
    if req.principal.role != CANDIDATE:
        raise NotFound("Consent applies to candidates.")
    return 200, req.app.tenancy.consent_state(
        req.principal.org_id, req.principal.member_id
    )


@cloud_route("POST", r"/api/me/consent", writes=True)
def consent_post(req: Req):  # noqa: ANN201
    kinds = req.body().get("kinds")
    if not isinstance(kinds, list):
        raise Conflict("Send the consents to give as kinds: [...].")
    return 200, req.app.tenancy.record_consent(
        req.principal,
        [str(k) for k in kinds],
        str(req.body().get("version") or "") or CONSENT_VERSION,
    )


@cloud_route("DELETE", r"/api/me/consent/([a-z_]{3,30})", writes=True)
def consent_revoke(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.revoke_consent(req.principal, req.group())


@cloud_route("GET", r"/api/me/onboarding")
def onboarding(req: Req):  # noqa: ANN201
    if req.principal.role != CANDIDATE:
        raise NotFound("Onboarding applies to candidates.")
    return 200, req.app.tenancy.onboarding(
        req.principal.org_id, req.principal.member_id
    )


@cloud_route("POST", r"/api/me/easy-apply", writes=True)
def easy_apply_opt_in(req: Req):  # noqa: ANN201
    """The candidate turns Easy Apply on for themselves after the User Agreement warning."""
    principal = req.principal
    if principal.role != CANDIDATE:
        raise NotFound("Easy Apply is the candidate's own switch.")
    app = req.app
    body = req.body()
    enable = bool(body.get("enable", True))
    if enable:
        if body.get("acknowledge") is not True:
            raise ValidationFailed(LINKEDIN_NOTICE, code="acknowledgement_required")
        app.entitlements.require_feature(
            principal.org_id, "sources.linkedin", audience="candidate", label="LinkedIn"
        )
        if not app.tenancy.policy(principal.org_id).get("easy_apply_allowed"):
            raise Forbidden(
                "LinkedIn Easy Apply is off in your organization's policy.",
                code="easy_apply_off",
            )
        if app.flags.killed("linkedin.easy_apply"):
            raise Forbidden(
                "LinkedIn Easy Apply is switched off for every organization right now.",
                code="easy_apply_off",
            )
    now = app.clock.now()
    with app.db.write() as connection:
        connection.execute(
            "UPDATE workspaces SET easy_apply_opt_in_at = ? WHERE candidate_id = ?",
            (now if enable else None, principal.member_id),
        )
        app.audit.record(
            principal.org_id,
            "linkedin.easy_apply_opt_in" if enable else "linkedin.easy_apply_opt_out",
            actor=principal,
            candidate_id=principal.member_id,
            connection=connection,
        )
    return 200, {"easy_apply": enable}


# --------------------------------------------------------------------------- opening a local installation


@cloud_route("GET", r"/api/launch/local")
def launch_local(req: Req):  # noqa: ANN201
    """Send the signed-in candidate to the local installation with a one-time launch token (302; JSON when asked)."""
    issued = req.app.launch.issue(req.principal, req.q("return"))
    accept = req.request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return 200, {"redirect": issued["redirect"], "expires_in": issued["expires_in"]}
    return Reply(
        302,
        b"",
        content_type="text/plain; charset=utf-8",
        headers=[("Location", issued["redirect"]), ("Cache-Control", "no-store")],
    )


@cloud_route("POST", r"/api/internal/launch/validate", auth="public", csrf=False)
def launch_validate(req: Req):  # noqa: ANN201
    """The local installation asks who a launch token signs in (signed with the launch secret; once)."""
    try:
        req.app.signed_requests.verify(
            req.request.headers_object(), req.request.body, purpose="launch"
        )
    except PermissionError as error:
        return 401, {"error": str(error), "code": "bad_signature"}
    return 200, req.app.launch.validate(str(req.body().get("token", "")))


__all__ = ["me_payload", "one", "OPERATOR_COOKIE"]
