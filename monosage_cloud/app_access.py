"""Who may open which app: per role and per member, on top of what the organization subscribes to.

An organization subscribes to apps (``org_apps``, :mod:`monosage_cloud.entitlements`); this module decides which of
its members may open each one. Two settings in the organization's policy (:data:`monosage_cloud.tenancy.DEFAULT_POLICY`):

* ``app_roles``: ``{app_id: [roles]}``, the roles that may use an app. An app that is not listed is for every role
  ("Everyone"); an empty list is for nobody but the members named below.
* ``member_apps``: ``{member_id: {"allow": [app_id], "deny": [app_id]}}``, exceptions for one person: ``allow`` gives
  them an app their role does not have, ``deny`` takes one their role has.

A member may open an app when the organization has it (``402 app_required`` otherwise) **and** this module allows it
(``403 app_not_allowed`` otherwise): a deny wins over everything, an allow over the role list, then the role list.

Careers is each candidate's own workspace (their profile, searches and applications), so the candidate role always
keeps it: ``app_roles["careers"]`` must include ``candidate`` and a candidate cannot be denied Careers. Other roles
may lose it: staff without Careers no longer work in candidates' workspaces or keep one of their own.

What is never an app, and so never closed by these settings: the account and organization pages (sign-in, Settings,
Security, ``/api/org/*``: members, apps, policy, billing), Today itself (its cards come from the apps a member may
open), and the Business hub. An owner can therefore always come back to the Apps and Members pages and give
themselves an app again. Changing what owners may open (the owner role in ``app_roles``, or an owner's own
exceptions) needs ``members.manage_owners``, so an administrator cannot take apps away from the owners.

Operators (also acting under an approved support grant) follow the rules they always had: the role their grant
carries and :data:`monosage_cloud.rbac.NEVER_VIA_SUPPORT`; these per-member settings do not apply to them.
Background jobs and public pages have no member and are only bound by the subscription.
"""

from __future__ import annotations

from typing import Any

from aira import apps as app_catalog
from monosage_cloud.db import many
from monosage_cloud.errors import Forbidden, NotFound, ValidationFailed
from monosage_cloud.rbac import CANDIDATE, ORG_ROLES, OWNER, Principal

# Roles that always keep an app, whatever the settings say.
ALWAYS: dict[str, frozenset[str]] = {"careers": frozenset({CANDIDATE})}
ROLE_KEY, MEMBER_KEY = "app_roles", "member_apps"


# ----------------------------------------------------------------------------- the decision


def _app(app_id: str) -> str:
    return app_catalog.current_id(str(app_id or ""))


def roles_for(policy: dict[str, Any], app_id: str) -> list[str] | None:
    """The roles that may use an app, or None for every role."""
    roles = (policy.get(ROLE_KEY) or {}).get(_app(app_id))
    return None if roles is None else list(roles)


def exceptions_for(policy: dict[str, Any], member_id: str) -> dict[str, list[str]]:
    entry = (policy.get(MEMBER_KEY) or {}).get(member_id) or {}
    return {
        "allow": list(entry.get("allow") or []),
        "deny": list(entry.get("deny") or []),
    }


def decide(
    policy: dict[str, Any], *, role: str, member_id: str, app_id: str
) -> tuple[bool, str]:
    """(allowed, why): ``always``, ``deny``, ``allow``, ``everyone`` or ``role``."""
    app_id = _app(app_id)
    if role in ALWAYS.get(app_id, ()):
        return True, "always"
    exceptions = exceptions_for(policy, member_id)
    if app_id in exceptions["deny"]:
        return False, "deny"
    if app_id in exceptions["allow"]:
        return True, "allow"
    roles = roles_for(policy, app_id)
    if roles is None:
        return True, "everyone"
    return role in roles, "role"


def applies_to(principal: Principal | None) -> bool:
    """Do the per-member settings bind this caller? Members do; operators, the system and public pages do not."""
    return (
        principal is not None
        and not principal.is_operator
        and bool(principal.member_id)
    )


def may_open(policy: dict[str, Any], principal: Principal | None, app_id: str) -> bool:
    if not applies_to(principal):
        return True
    assert principal is not None
    return decide(
        policy,
        role=principal.staff_role,
        member_id=principal.member_id,
        app_id=app_id,
    )[0]


def denied_message(app_id: str) -> str:
    app = app_catalog.BY_ID.get(_app(app_id))
    name = app.name if app else app_id
    return f"Your organization has not given you {name}. Ask an owner or admin."


def require(policy: dict[str, Any], principal: Principal | None, app_id: str) -> None:
    """``403 app_not_allowed`` when the organization has the app but has not given it to this member."""
    app_id = _app(app_id)
    if app_id not in app_catalog.BY_ID or may_open(policy, principal, app_id):
        return
    raise Forbidden(
        denied_message(app_id),
        code="app_not_allowed",
        app=app_id,
        name=app_catalog.BY_ID[app_id].name,
    )


# ----------------------------------------------------------------------------- validation


def active_members(db: Any, org_id: str) -> dict[str, dict[str, Any]]:
    with db.read() as connection:
        rows = many(
            connection,
            "SELECT member_id, role, name, email FROM members WHERE org_id = ? AND removed_at IS NULL AND status = 'active'",
            (org_id,),
        )
    return {row["member_id"]: row for row in rows}


def _app_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValidationFailed(f"{what} must be a list of app ids.")
    out: list[str] = []
    for raw in value:
        app_id = _app(raw.strip())
        if app_id not in app_catalog.BY_ID:
            raise ValidationFailed(f"Unknown app: {raw}.")
        if app_id not in out:
            out.append(app_id)
    return sorted(out, key=app_catalog.APP_IDS.index)


def clean_roles(app_id: str, value: Any) -> list[str] | None:
    """One app's roles: None (or every role) is Everyone; otherwise the roles, in the matrix's order."""
    app_id = _app(app_id)
    if app_id not in app_catalog.BY_ID:
        raise ValidationFailed(f"Unknown app: {app_id}.")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValidationFailed("Who can use an app must be a list of roles.")
    unknown = sorted({v for v in value if v not in ORG_ROLES})
    if unknown:
        raise ValidationFailed(
            f"Unknown role: {', '.join(unknown)}. Choose from {', '.join(ORG_ROLES)}."
        )
    missing = ALWAYS.get(app_id, frozenset()) - set(value)
    if missing:
        raise ValidationFailed(
            f"{app_catalog.BY_ID[app_id].name} is each candidate's own workspace: the candidate role always keeps it."
        )
    roles = [role for role in ORG_ROLES if role in value]
    return None if len(roles) == len(ORG_ROLES) else roles


def clean_app_roles(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValidationFailed(
            "Who can use each app must be an object of app ids and roles."
        )
    cleaned: dict[str, list[str]] = {}
    for app_id, roles in value.items():
        roles = clean_roles(str(app_id), roles)
        if roles is not None:
            cleaned[_app(str(app_id))] = roles
    return cleaned


def clean_exceptions(member: dict[str, Any], value: Any) -> dict[str, list[str]]:
    """One member's allow and deny lists."""
    if not isinstance(value, dict) or set(value) - {"allow", "deny"}:
        raise ValidationFailed(
            "Each member's apps must be allow and deny lists of app ids."
        )
    allow = _app_list(value.get("allow") or [], "Allowed apps")
    deny = _app_list(value.get("deny") or [], "Denied apps")
    both = [a for a in allow if a in deny]
    if both:
        raise ValidationFailed(
            f"{app_catalog.BY_ID[both[0]].name} cannot be both allowed and denied."
        )
    for app_id in deny:
        if member["role"] in ALWAYS.get(app_id, ()):
            raise ValidationFailed(
                f"{app_catalog.BY_ID[app_id].name} is a candidate's own workspace: it cannot be taken away from them."
            )
    return {"allow": allow, "deny": deny}


def clean_member_apps(
    value: Any, members: dict[str, dict[str, Any]]
) -> dict[str, dict[str, list[str]]]:
    if not isinstance(value, dict):
        raise ValidationFailed("Each member's apps must be an object of member ids.")
    cleaned: dict[str, dict[str, list[str]]] = {}
    for member_id, entry in value.items():
        member = members.get(str(member_id))
        if member is None:
            raise ValidationFailed("Choose members of this organization.")
        exceptions = clean_exceptions(member, entry)
        if exceptions["allow"] or exceptions["deny"]:
            cleaned[str(member_id)] = exceptions
    return cleaned


def guard_policy_patch(app: Any, actor: Principal, patch: dict[str, Any]) -> None:
    """A policy change through ``PATCH /api/org/policy`` that changes what owners may open needs an owner."""
    if ROLE_KEY not in patch and MEMBER_KEY not in patch:
        return
    if app.authz.can(actor, "members.manage_owners"):
        return
    current = app.tenancy.org(actor.org_id)["policy"]
    owners = {
        m
        for m, row in active_members(app.db, actor.org_id).items()
        if row["role"] == OWNER
    }
    if ROLE_KEY in patch and isinstance(patch[ROLE_KEY], dict):
        for app_id in {*(current.get(ROLE_KEY) or {}), *patch[ROLE_KEY]}:
            before = (current.get(ROLE_KEY) or {}).get(app_id)
            after = patch[ROLE_KEY].get(app_id)
            if (before is None or OWNER in before) != (
                after is None or OWNER in (after or [])
            ):
                raise Forbidden("Only an owner can change which apps owners may use.")
    if MEMBER_KEY in patch and isinstance(patch[MEMBER_KEY], dict):
        for member_id in owners:
            if (current.get(MEMBER_KEY) or {}).get(member_id) != patch[MEMBER_KEY].get(
                member_id
            ):
                raise Forbidden("Only an owner can change which apps owners may use.")


# ----------------------------------------------------------------------------- reading and changing


def _write(app: Any, actor: Principal, key: str, update: Any) -> dict[str, Any]:
    """Change one policy key inside one write (read, change, store), so two admins cannot undo each other."""
    from monosage_cloud.db import dumps, loads, one
    from monosage_cloud.tenancy import DEFAULT_POLICY

    with app.db.write() as connection:
        row = one(
            connection,
            "SELECT policy FROM organizations WHERE org_id = ? AND deleted_at IS NULL",
            (actor.org_id,),
        )
        if row is None:
            raise NotFound("Organization not found.")
        policy = {**DEFAULT_POLICY, **loads(row["policy"], {})}
        before = policy.get(key) or {}
        after = update(dict(before))
        policy[key] = after
        connection.execute(
            "UPDATE organizations SET policy = ? WHERE org_id = ?",
            (dumps(policy), actor.org_id),
        )
    app.tenancy.invalidate_policy(actor.org_id)
    return policy


def app_rows(policy: dict[str, Any], entitlements: Any) -> list[dict[str, Any]]:
    """Each app's roles for the Apps page."""
    return [
        {
            "id": entry.id,
            "roles": roles_for(policy, entry.id),
            "locked_roles": sorted(ALWAYS.get(entry.id, ())),
            "subscribed": entitlements.has_app(entry.id),
        }
        for entry in app_catalog.APPS
    ]


def set_app_roles(
    app: Any, actor: Principal, app_id: str, roles: Any
) -> dict[str, Any]:
    """``PUT /api/org/apps/<id>/access``: the roles that may use one app (None or every role: Everyone)."""
    app.authz.require(actor, "org.settings")
    app.entitlements.require_state(actor.org_id, write=True)
    app_id = _app(app_id)
    if app_id not in app_catalog.BY_ID:
        raise NotFound("App not found.")
    cleaned = clean_roles(app_id, roles)
    before = roles_for(app.tenancy.policy(actor.org_id), app_id)
    touches_owner = (before is None or OWNER in before) != (
        cleaned is None or OWNER in cleaned
    )
    if touches_owner:
        app.authz.require(actor, "members.manage_owners")

    def update(current: dict[str, Any]) -> dict[str, Any]:
        current.pop(app_id, None)
        if cleaned is not None:
            current[app_id] = cleaned
        return current

    policy = _write(app, actor, ROLE_KEY, update)
    if before != cleaned:
        app.audit.record(
            actor.org_id,
            "org.app_access",
            actor=actor,
            target_type="app",
            target_id=app_id,
            detail={"app": app_id, "from": before, "to": cleaned},
        )
    return {
        "app": app_id,
        "roles": roles_for(policy, app_id),
        "everyone": cleaned is None,
    }


def member_view(app: Any, actor: Principal, member_id: str) -> dict[str, Any]:
    """``GET /api/org/members/<id>/apps``: every app with whether this member may open it, and why."""
    app.authz.require(actor, "members.manage")
    member = app.tenancy.member(actor.org_id, member_id)
    policy = app.tenancy.policy(actor.org_id)
    entitlements = app.entitlements.resolve(actor.org_id)
    exceptions = exceptions_for(policy, member_id)
    rows = []
    for entry in app_catalog.APPS:
        allowed, why = decide(
            policy, role=member["role"], member_id=member_id, app_id=entry.id
        )
        role_allowed = decide(
            policy, role=member["role"], member_id="", app_id=entry.id
        )[0]
        rows.append(
            {
                "id": entry.id,
                "name": entry.name,
                "category": entry.category,
                "subscribed": entitlements.has_app(entry.id),
                "allowed": allowed,
                "by_role": role_allowed,
                "why": why,
                "setting": "deny"
                if entry.id in exceptions["deny"]
                else "allow"
                if entry.id in exceptions["allow"]
                else "role",
                "locked": member["role"] in ALWAYS.get(entry.id, ()),
            }
        )
    return {
        "member": app.tenancy.public_member(member),
        "allow": exceptions["allow"],
        "deny": exceptions["deny"],
        "apps": rows,
        "can_change": member["role"] != OWNER
        or app.authz.can(actor, "members.manage_owners"),
    }


def set_member_apps(
    app: Any, actor: Principal, member_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """``PUT /api/org/members/<id>/apps``: this member's allow and deny lists (empty lists: just their role)."""
    app.authz.require(actor, "members.manage")
    app.entitlements.require_state(actor.org_id, write=True)
    member = app.tenancy.member(
        actor.org_id, member_id
    )  # another organization's member is a 404
    if member["status"] != "active":
        raise ValidationFailed("Choose members of this organization.")
    if member["role"] == OWNER:
        app.authz.require(actor, "members.manage_owners")
    cleaned = clean_exceptions(
        member, {"allow": body.get("allow") or [], "deny": body.get("deny") or []}
    )
    before = exceptions_for(app.tenancy.policy(actor.org_id), member_id)

    def update(current: dict[str, Any]) -> dict[str, Any]:
        current.pop(member_id, None)
        if cleaned["allow"] or cleaned["deny"]:
            current[member_id] = cleaned
        return current

    _write(app, actor, MEMBER_KEY, update)
    if before != cleaned:
        app.audit.record(
            actor.org_id,
            "member.app_access",
            actor=actor,
            target_type="member",
            target_id=member_id,
            detail={"member": member_id, "from": before, "to": cleaned},
        )
    return member_view(app, actor, member_id)
