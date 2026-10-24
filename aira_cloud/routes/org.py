"""The organization's own API: dashboard, members, candidates, coaches, policy, audit, integrations, data."""

from __future__ import annotations

import json
from typing import Any

from aira_cloud import hooks
from aira_cloud.db import loads, many, one
from aira_cloud.errors import CloudError, Forbidden, NotFound, ValidationFailed
from aira_cloud.rbac import CANDIDATE, Principal
from aira_cloud.routes.auth import clear_cookie, me_payload
from aira_cloud.webio import Reply, Req, cloud_route


def _body_str(req: Req, name: str) -> str:
    return str(req.body().get(name, "")).strip()


# ----------------------------------------------------------------------------- the organization


@cloud_route("GET", r"/api/org")
def org_get(req: Req):  # noqa: ANN201
    app, principal = req.app, req.principal
    org = app.tenancy.org(principal.org_id)
    entitlements = app.entitlements.resolve(principal.org_id)
    payload: dict[str, Any] = {
        "org": {
            "org_id": org["org_id"],
            "name": org["name"],
            "slug": org["slug"],
            "region": org["region"],
            "created_at": org["created_at"],
        },
        "policy": org["policy"],
        "branding": app.tenancy.branding(principal.org_id),
        "entitlements": entitlements.as_dict(),
        "paused": {"paused": bool(org["paused_at"]), "reason": org["paused_reason"]},
        "closure_requested_at": org["closure_requested_at"],
    }
    if app.authz.can(principal, "billing.manage") or app.authz.can(
        principal, "usage.read"
    ):
        payload["billing"] = app.billing.summary(principal.org_id)
    return 200, payload


@cloud_route("PATCH", r"/api/org/policy", writes=True)
def org_policy(req: Req):  # noqa: ANN201
    return 200, {"policy": req.app.tenancy.update_policy(req.principal, req.body())}


@cloud_route("PUT", r"/api/org/branding", writes=True)
def org_branding(req: Req):  # noqa: ANN201
    body = req.body()
    return 200, req.app.tenancy.set_branding(
        req.principal,
        logo_url=str(body.get("logo_url", "")),
        display_name=str(body.get("name", "")),
    )


@cloud_route("GET", r"/api/org/dashboard")
def org_dashboard(req: Req):  # noqa: ANN201
    return 200, req.app.dashboard.for_principal(req.principal)


@cloud_route("GET", r"/api/org/banners", verified=False)
def org_banners(req: Req):  # noqa: ANN201
    return 200, {"banners": req.app.dashboard.banners(req.principal)}


@cloud_route("GET", r"/api/org/usage")
def org_usage(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "usage.read")
    return 200, req.app.dashboard.plan_usage(req.principal.org_id)


@cloud_route("GET", r"/api/org/notices")
def org_notices(req: Req):  # noqa: ANN201
    app, principal = req.app, req.principal
    if not (
        app.authz.can(principal, "billing.manage")
        or app.authz.can(principal, "members.manage")
    ):
        raise Forbidden("You do not have permission to do that.")
    with app.db.read() as connection:
        rows = many(
            connection,
            "SELECT id, level, title, body, link, created_at, read_at FROM org_notices WHERE org_id = ? ORDER BY id DESC LIMIT 50",
            (principal.org_id,),
        )
    return 200, {"notices": rows, "unread": sum(1 for r in rows if not r["read_at"])}


@cloud_route("POST", r"/api/org/notices/read")
def org_notices_read(req: Req):  # noqa: ANN201
    app, principal = req.app, req.principal
    with app.db.write() as connection:
        connection.execute(
            "UPDATE org_notices SET read_at = ? WHERE org_id = ? AND read_at IS NULL",
            (app.clock.now(), principal.org_id),
        )
    return 200, {"ok": True}


# ----------------------------------------------------------------------------- members and invitations


@cloud_route("GET", r"/api/org/members")
def members_list(req: Req):  # noqa: ANN201
    members = req.app.tenancy.list_members(
        req.principal,
        role=req.q("role"),
        q=req.q("q"),
        limit=req.qint("limit", 200, 1, 500),
        offset=req.qint("offset", 0, 0, 100000),
    )
    return 200, {
        "members": members,
        "seats": req.app.tenancy.seat_usage(req.principal.org_id),
    }


@cloud_route("PATCH", r"/api/org/members/([0-9a-f]{32})", writes=True)
def member_update(req: Req):  # noqa: ANN201
    body = req.body()
    if "role" not in body:
        raise ValidationFailed("Send the new role.")
    return 200, {
        "member": req.app.tenancy.change_role(
            req.principal, req.group(), str(body["role"])
        )
    }


@cloud_route("DELETE", r"/api/org/members/([0-9a-f]{32})", writes=True)
def member_remove(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.remove_member(req.principal, req.group())


@cloud_route("POST", r"/api/org/members/([0-9a-f]{32})/mfa-reset", writes=True)
def member_mfa_reset(req: Req):  # noqa: ANN201
    req.app.identity.mfa_reset(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route("GET", r"/api/org/invites")
def invites_list(req: Req):  # noqa: ANN201
    return 200, {
        "invites": req.app.identity.list_invites(
            req.principal, pending_only=req.q("all") != "1"
        )
    }


@cloud_route("POST", r"/api/org/invites", writes=True)
def invites_create(req: Req):  # noqa: ANN201
    body = req.body()
    emails = body.get("emails") or ([body["email"]] if body.get("email") else [])
    if not isinstance(emails, list) or not emails:
        raise ValidationFailed("Enter at least one email address.")
    if len(emails) > 50:
        raise ValidationFailed("Invite at most 50 people at a time.")

    def send(email: Any) -> dict[str, Any]:
        return req.app.identity.invite(
            req.principal,
            str(email),
            str(body.get("role", "")),
            coach_id=str(body.get("coach_id", "")),
            coach_scope=str(body.get("coach_scope", "")),
        )

    if len(emails) == 1:
        return 201, {
            "invites": [send(emails[0])],
            "failed": [],
        }  # a single address reports its own error and status
    created, failed = [], []
    for email in emails:
        try:
            created.append(send(email))
        except (
            CloudError
        ) as error:  # report each address; one bad address must not stop the rest
            failed.append({"email": str(email), **error.payload()})
    return 201, {"invites": created, "failed": failed}


@cloud_route("POST", r"/api/org/invites/([0-9a-f]{32})/resend", writes=True)
def invite_resend(req: Req):  # noqa: ANN201
    return 200, req.app.identity.resend_invite(req.principal, req.group())


@cloud_route("DELETE", r"/api/org/invites/([0-9a-f]{32})", writes=True)
def invite_revoke(req: Req):  # noqa: ANN201
    req.app.identity.revoke_invite(req.principal, req.group())
    return 200, {"ok": True}


# ----------------------------------------------------------------------------- candidates


@cloud_route("GET", r"/api/org/candidates")
def candidates_list(req: Req):  # noqa: ANN201
    rows = req.app.tenancy.list_candidates(
        req.principal,
        q=req.q("q"),
        stage=req.q("stage"),
        coach_id=req.q("coach"),
        attention=req.q("attention") == "1",
        limit=req.qint("limit", 100, 1, 500),
        offset=req.qint("offset", 0, 0, 100000),
    )
    return 200, {"candidates": rows}


@cloud_route("GET", r"/api/org/candidates/([0-9a-f]{32})")
def candidate_detail(req: Req):  # noqa: ANN201
    app, principal, candidate_id = req.app, req.principal, req.group()
    app.authz.require(principal, "candidates.read", candidate_id=candidate_id)
    member = app.tenancy.member(principal.org_id, candidate_id)
    workspace = app.workspaces.row(principal.org_id, candidate_id)
    snapshot = app.workspaces.refresh(principal.org_id, candidate_id, max_age=30)
    with app.db.read() as connection:
        assignments = many(
            connection,
            "SELECT a.coach_id, a.scope, c.name, c.email FROM coach_assignments a JOIN members c ON c.member_id = a.coach_id WHERE a.org_id = ? AND a.candidate_id = ?",
            (principal.org_id, candidate_id),
        )
    cap = app.entitlements.resolve(principal.org_id).coach_scopes()
    for assignment in assignments:
        assignment["effective_scope"] = (
            assignment["scope"] if assignment["scope"] in cap else cap[-1]
        )
    row = {
        "member_id": candidate_id,
        "name": member["name"],
        "email": member["email"],
        "linkedin_port": workspace["linkedin_port"],
    }
    return 200, {
        "candidate": {
            "candidate_id": candidate_id,
            "name": member["name"],
            "email": member["email"],
            "created_at": member["created_at"],
            "stage": workspace["stage"],
            "workspace_status": workspace["status"],
        },
        "paused": {
            "paused": bool(workspace["paused_at"]),
            "reason": workspace["paused_reason"],
            "since": workspace["paused_at"],
        },
        "linkedin": {
            "connected": bool(workspace["linkedin_port"]),
            "opted_in": bool(workspace["linkedin_opt_in_at"]),
            "easy_apply": bool(workspace["easy_apply_opt_in_at"]),
        },
        "snapshot": {
            k: v for k, v in snapshot.items() if k not in {"by_source", "by_resume"}
        },
        "onboarding": app.tenancy.onboarding(principal.org_id, candidate_id),
        "consent": app.tenancy.consent_state(principal.org_id, candidate_id),
        "coaches": assignments,
        "needs_attention": app.tenancy.attention_for(row, snapshot, bool(assignments)),
        "can_see_sensitive": app.authz.can_see_sensitive(principal, candidate_id),
        "queue": app.dashboard.held_queue(principal, candidate_id)
        if app.authz.can(principal, "candidates.read", candidate_id=candidate_id)
        else [],
    }


@cloud_route("POST", r"/api/org/candidates/([0-9a-f]{32})/pause", writes=True)
def candidate_pause(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.pause_candidate(
        req.principal, req.group(), _body_str(req, "reason")
    )


@cloud_route("POST", r"/api/org/candidates/([0-9a-f]{32})/resume", writes=True)
def candidate_resume(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.resume_candidate(req.principal, req.group())


@cloud_route("DELETE", r"/api/org/candidates/([0-9a-f]{32})/workspace", writes=True)
def candidate_workspace_delete(req: Req):  # noqa: ANN201
    return 200, req.app.lifecycle.delete_candidate_workspace(req.principal, req.group())


# ----------------------------------------------------------------------------- coaches


@cloud_route("GET", r"/api/org/assignments")
def assignments_list(req: Req):  # noqa: ANN201
    return 200, {
        "assignments": req.app.tenancy.list_assignments(
            req.principal, coach_id=req.q("coach"), candidate_id=req.q("candidate")
        ),
        "scopes": req.app.entitlements.resolve(req.principal.org_id).coach_scopes(),
    }


@cloud_route("PUT", r"/api/org/assignments", writes=True)
def assignments_put(req: Req):  # noqa: ANN201
    body = req.body()
    return 200, req.app.tenancy.assign_coach(
        req.principal,
        str(body.get("coach_id", "")),
        str(body.get("candidate_id", "")),
        str(body.get("scope", "")),
    )


@cloud_route(
    "DELETE", r"/api/org/assignments/([0-9a-f]{32})/([0-9a-f]{32})", writes=True
)
def assignments_delete(req: Req):  # noqa: ANN201
    req.app.tenancy.unassign_coach(req.principal, req.group(1), req.group(2))
    return 200, {"ok": True}


@cloud_route("POST", r"/api/org/assignments/reassign", writes=True)
def assignments_reassign(req: Req):  # noqa: ANN201
    body = req.body()
    return 200, req.app.tenancy.bulk_reassign(
        req.principal,
        str(body.get("from_coach", "")),
        str(body.get("to_coach", "")),
        str(body.get("scope", "")),
    )


@cloud_route("GET", r"/api/org/queue")
def held_queue(req: Req):  # noqa: ANN201
    return 200, {
        "queue": req.app.dashboard.held_queue(req.principal, req.q("candidate"))
    }


# ----------------------------------------------------------------------------- pausing


@cloud_route("POST", r"/api/org/automation/pause", writes=True)
def automation_pause(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.pause_org(req.principal, _body_str(req, "reason"))


@cloud_route("POST", r"/api/org/automation/resume", writes=True)
def automation_resume(req: Req):  # noqa: ANN201
    return 200, req.app.tenancy.resume_org(req.principal)


# ----------------------------------------------------------------------------- audit


@cloud_route("GET", r"/api/org/audit")
def audit_list(req: Req):  # noqa: ANN201
    app, principal = req.app, req.principal
    app.authz.require(principal, "audit.read")
    rows = app.audit.list(
        principal.org_id,
        limit=req.qint("limit", 100, 1, 1000),
        before_seq=req.qint("before", 0, 0, 10**12) or None,
        candidate_id=req.q("candidate"),
        action=req.q("action"),
        actor_id=req.q("actor"),
        since=req.qint("since", 0, 0, 10**12) or None,
        until=req.qint("until", 0, 0, 10**12) or None,
    )
    return 200, {
        "events": rows,
        "retention_days": app.entitlements.resolve(principal.org_id).limit(
            "audit_days"
        ),
    }


@cloud_route("GET", r"/api/org/audit/verify")
def audit_verify(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "audit.read")
    return 200, req.app.audit.verify(req.principal.org_id)


@cloud_route("GET", r"/api/org/audit/export")
def audit_export(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "audit.read")
    rows = req.app.audit.export_lines(req.principal.org_id)
    req.app.audit.record(
        req.principal.org_id,
        "data.export_audit",
        actor=req.principal,
        detail={"rows": len(rows)},
    )
    return Reply(
        200,
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows).encode(
            "utf-8"
        ),
        content_type="application/x-ndjson",
        filename="audit.jsonl",
    )


def _cef(row: dict[str, Any]) -> str:
    def esc(text: Any, header: bool = False) -> str:
        value = " ".join(str(text).splitlines()).replace("\\", "\\\\")
        return value.replace("|", "\\|") if header else value.replace("=", "\\=")

    target = esc(str(row["target_type"]) + ":" + str(row["target_id"]))
    extension = (
        f"rt={row['at'] * 1000} suser={esc(row['actor_label'])} suid={esc(row['actor_id'])} cs1Label=candidate cs1={esc(row['candidate_id'])} "
        f"cs2Label=role cs2={esc(row['actor_role'])} cs3Label=target cs3={target} externalId={row['seq']}"
    )
    return f"CEF:0|Aira|Career Copilot|1.0|{esc(row['action'], True)}|{esc(row['action'], True)}|3|{extension}"


@cloud_route("GET", r"/api/org/audit/stream")
def audit_stream(req: Req):  # noqa: ANN201
    """Poll the audit log for a SIEM: events after a cursor as JSON lines or CEF (Enterprise)."""
    app, principal = req.app, req.principal
    app.authz.require(principal, "audit.read")
    app.entitlements.require_feature(
        principal.org_id, "siem_export", label="SIEM export"
    )
    after = req.qint("after", 0, 0, 10**12)
    rows = app.audit.stream(
        principal.org_id, after_seq=after, limit=req.qint("limit", 500, 1, 5000)
    )
    fmt = req.q("format", "json")
    if fmt not in {"json", "cef"}:
        raise ValidationFailed("Format must be json or cef.")
    body = "\n".join(
        _cef(r) if fmt == "cef" else json.dumps(r, ensure_ascii=False, default=str)
        for r in rows
    )
    cursor = str(rows[-1]["seq"]) if rows else str(after)
    return Reply(
        200,
        body.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
        headers=[("X-Aira-Next-Cursor", cursor), ("X-Aira-Events", str(len(rows)))],
    )


@cloud_route("GET", r"/api/me/activity", verified=False)
def my_activity(req: Req):  # noqa: ANN201
    """What was done in a candidate's name: every action records the actor and the candidate."""
    if req.principal.role != CANDIDATE:
        raise NotFound("Activity in your name applies to candidates.")
    rows = req.app.audit.list(
        req.principal.org_id,
        limit=req.qint("limit", 100, 1, 500),
        candidate_id=req.principal.member_id,
    )
    return 200, {
        "events": [
            {
                "at": r["at"],
                "action": r["action"],
                "actor": r["actor_label"],
                "role": r["actor_role"],
                "target": r["target_id"],
                "detail": r["detail"],
            }
            for r in rows
        ]
    }


# ----------------------------------------------------------------------------- integrations


@cloud_route("GET", r"/api/org/integrations")
def integrations(req: Req):  # noqa: ANN201
    app, principal = req.app, req.principal
    app.authz.require(principal, "integrations.read")
    org_id = principal.org_id
    entitlements = app.entitlements.resolve(org_id)
    key = hooks._org_serpapi_key(app, org_id)
    policy = app.tenancy.policy(org_id)
    pooled = bool(
        policy.get("use_pooled_serpapi")
        and entitlements.has("pooled_serpapi")
        and app.config.pooled_serpapi_key
    )
    sso = app.sso.get_config(org_id)
    with app.db.read() as connection:
        scim = one(
            connection,
            "SELECT COUNT(*) AS n FROM scim_tokens WHERE org_id = ? AND revoked_at IS NULL",
            (org_id,),
        )
        keys = one(
            connection,
            "SELECT COUNT(*) AS n FROM airaapi_key_index WHERE user_id = ?",
            (org_id,),
        )
    return 200, {
        "serpapi": {
            "configured": bool(key),
            "source": "app" if key else ("pooled" if pooled else ""),
            "last4": key[-4:] if key else None,
            "available": entitlements.has("sources.google_jobs"),
            "pooled_available": entitlements.has("pooled_serpapi"),
        },
        "model": app.models.endpoint_status(org_id)
        | {"byo_available": entitlements.has("byo_model")},
        "sso": {
            "available": entitlements.has("auth.sso"),
            "configured": bool(sso),
            "protocol": sso["protocol"] if sso else "",
            "enforced": bool(sso and sso["enforced"]),
            "domains": sso["domains"] if sso else [],
        },
        "scim": {
            "available": entitlements.has("auth.scim"),
            "tokens": int(scim["n"]) if scim else 0,
        },
        "api": {
            "console": entitlements.has("api.console_read"),
            "searches": entitlements.has("api.searches"),
            "v1": entitlements.has("api.v1"),
            "keys": int(keys["n"]) if keys else 0,
            "v1_limit": entitlements.limit("api_v1_keys"),
        },
    }


@cloud_route("PUT", r"/api/org/integrations/model", writes=True)
def model_set(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "integrations.manage")
    body = req.body()
    req.app.models.set_endpoint(
        req.principal.org_id,
        url=str(body.get("url", "")),
        api_key=str(body.get("api_key", "")),
        model=str(body.get("model", "")),
    )
    req.app.audit.record(
        req.principal.org_id,
        "integrations.model_endpoint",
        actor=req.principal,
        detail={"url": str(body.get("url", ""))[:200]},
    )
    return 200, req.app.models.endpoint_status(req.principal.org_id)


@cloud_route("DELETE", r"/api/org/integrations/model", writes=True)
def model_clear(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "integrations.manage")
    req.app.models.clear_endpoint(req.principal.org_id)
    req.app.audit.record(
        req.principal.org_id, "integrations.model_endpoint_removed", actor=req.principal
    )
    return 200, req.app.models.endpoint_status(req.principal.org_id)


# ----------------------------------------------------------------------------- support access (owner)


@cloud_route("GET", r"/api/org/support")
def support_list(req: Req):  # noqa: ANN201
    return 200, {"requests": req.app.platform.list_support_requests(req.principal)}


@cloud_route("POST", r"/api/org/support/([0-9a-f]{32})/approve", writes=True)
def support_approve(req: Req):  # noqa: ANN201
    return 200, req.app.platform.decide_support(
        req.principal, req.group(), True, int(req.body().get("hours", 24) or 24)
    )


@cloud_route("POST", r"/api/org/support/([0-9a-f]{32})/deny", writes=True)
def support_deny(req: Req):  # noqa: ANN201
    return 200, req.app.platform.decide_support(req.principal, req.group(), False)


@cloud_route("POST", r"/api/org/support/([0-9a-f]{32})/revoke")
def support_revoke(req: Req):  # noqa: ANN201
    req.app.platform.revoke_support(req.principal, req.group(), "revoked by the owner")
    return 200, {"ok": True}


# ----------------------------------------------------------------------------- data


@cloud_route("GET", r"/api/org/exports")
def exports_list(req: Req):  # noqa: ANN201
    return 200, {"exports": req.app.lifecycle.list_exports(req.principal)}


@cloud_route("POST", r"/api/org/exports")
def exports_create(req: Req):  # noqa: ANN201
    candidate_id = _body_str(req, "candidate_id")
    if candidate_id:
        return 201, req.app.lifecycle.export_candidate(req.principal, candidate_id)
    if req.principal.role == CANDIDATE:
        return 201, req.app.lifecycle.export_candidate(
            req.principal, req.principal.member_id
        )
    return 201, req.app.lifecycle.export_organization(req.principal)


@cloud_route("GET", r"/api/org/exports/([0-9a-f]{32})/download")
def exports_download(req: Req):  # noqa: ANN201
    row, data = req.app.lifecycle.export_for(req.principal, req.group())
    return Reply(
        200,
        data,
        content_type="application/zip",
        filename=f"aira-export-{row['kind']}-{row['export_id'][:8]}.zip",
    )


@cloud_route("GET", r"/api/exports/download/([A-Za-z0-9_-]{20,80})", auth="public")
def exports_token_download(req: Req):  # noqa: ANN201
    row, data = req.app.lifecycle.export_by_token(req.group())
    return Reply(
        200,
        data,
        content_type="application/zip",
        filename=f"aira-my-data-{row['export_id'][:8]}.zip",
    )


@cloud_route("POST", r"/api/org/closure")
def closure_request(req: Req):  # noqa: ANN201
    return 200, req.app.lifecycle.request_closure(
        req.principal, str(req.body().get("password", ""))
    )


@cloud_route("DELETE", r"/api/org/closure")
def closure_cancel(req: Req):  # noqa: ANN201
    return 200, req.app.lifecycle.cancel_closure(req.principal)


__all__ = ["loads", "Principal", "clear_cookie", "me_payload"]
