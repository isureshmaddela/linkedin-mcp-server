"""Tenant isolation, route by route.

Two organizations with real data. Tenant A's people call every route of the hosted API with tenant B's
identifiers (in the path, the candidate header, the query string and the body) and must never get B's
data or change B's state. The sweep covers 100% of the routes: each one is exercised, or sits in a named
category with the reason it needs no identifier or is covered by its own test.
"""

from __future__ import annotations

import os
import re
from typing import Any

import pytest
from cloud_support import (
    Browser,
    add_member,
    build_org,
    seed_workspace,
    sign_in,
)  # noqa: F401

import auto_apply_assistant as local
from aira import autopilot
from aira.interviews import create_interview
from aira.crm import create_recruiter
from aira.audit import create_task
from aira_cloud.policy import POLICY
from aira_cloud.webio import CLOUD_ROUTES

import aira_cloud.routes  # noqa: F401  (registers the control-plane routes)

MARKERS = ("beta.test", "Beta Secret", "BETA-SECRET")
SAFE = {
    400,
    401,
    402,
    403,
    404,
    405,
    409,
    410,
    422,
    429,
    502,
}  # anything but success: nothing was read or changed

# Routes whose path identifiers are not tenant-owned objects, with the reason each needs no isolation sweep.
EXEMPT_GROUPS = {
    r"[a-z][a-z0-9_]{1,60}": "standard-answer field names are a global vocabulary",
    r"[a-z][a-z0-9_]{0,63}": "API engine names are a global catalog",
    r"[A-Za-z0-9_+=-]{1,128}": "conversation ids are LinkedIn thread ids, looked up in the caller's own cached inbox",
    r"[A-Za-z0-9_.-]+": "static file names",
    r"[A-Za-z0-9_-]{20,80}": "bearer tokens: possession is the credential (invitations, downloads)",
    r"[a-z]{3,20}": "provider and plan names are global",
    r"[a-z_]{3,30}": "consent kinds are a fixed vocabulary",
    r"[a-z0-9_-]{1,40}": "source names are a fixed vocabulary",
    r"company-intel|salary|trends|interview-prep|travel|learn": "report kinds are a fixed vocabulary",
    r"[A-Za-z0-9_-]{1,80}": "SerpApi search ids resolved inside the caller's own workspace",
    r"[a-z0-9%]{1,720}": "custom-answer keys are labels the caller saved in their own workspace",
    r"[a-z-]{3,40}": "legal document names are a fixed, public list",
    r"[a-z0-9_-]+": "app ids are a fixed catalog (careers, leads, api)",
    r"[a-z_]{2,40}": "public page action names are a fixed vocabulary per page kind",
    r"[a-z]{2}": "language codes of the screens' translations are a fixed public list",
    r"[a-z]{2,20}": "AI agent ids are a fixed catalog (life, planner, ...); their settings are the caller's own",
    r"[A-Za-z0-9_-]{40,64}": "public link tokens: possession is the credential (aira.suite.public), and each resolves to its own organization",
}
# Routes with no tenant selector in the path; mutating ones are covered by body-identifier tests below.
PUBLIC_OR_OWN_SESSION = {
    "ui_index",
    "ui_app_page",
    "ui_static",
    "api_health",
    "api_ready",
    "api_metrics",
}


class World:
    """Tenant A (the caller) and tenant B (whose identifiers are used), with data in both."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.a = build_org(app, "Alpha Org", slug_email="alpha.test", apps="all")
        self.b = build_org(app, "Beta Secret Org", slug_email="beta.test", apps="all")
        for org in (self.a, self.b):
            for who in ("candidate", "other"):
                seed_workspace(
                    app,
                    org.org_id,
                    getattr(org, who).member_id,
                    job_title="BETA-SECRET-JOB" if org is self.b else "Alpha Job",
                )
        app.tenancy.assign_coach(
            self.a.owner, self.a.coach.member_id, self.a.candidate.member_id, "approve"
        )
        app.tenancy.assign_coach(
            self.b.owner, self.b.coach.member_id, self.b.candidate.member_id, "approve"
        )
        app.tenancy.record_consent(
            self.a.candidate, ["automation", "coach_access", "sensitive_policy"]
        )
        app.tenancy.record_consent(
            self.b.candidate, ["automation", "coach_access", "sensitive_policy"]
        )
        self.b_ids = self._b_objects()

    def _b_objects(self) -> dict[str, Any]:
        app, b = self.app, self.b
        ids: dict[str, Any] = {}
        invite = app.identity.invite(b.owner, "newcomer@beta.test", "coach")
        ids["invite"] = invite["invite_id"]
        export = app.lifecycle.export_candidate(b.owner, b.candidate.member_id)
        ids["export"] = export["export_id"]
        with app.workspaces.bind(
            b.org_id, b.candidate.member_id, kind="system", source="test"
        ):
            with local._connect() as connection:
                plan = autopilot.create_plan(
                    connection,
                    {
                        "name": "Beta plan",
                        "positions": ["BETA-SECRET-JOB"],
                        "sources": ["aira_jobs"],
                        "boards": [],
                        "mode": "review",
                        "min_score": 50,
                        "max_per_run": 1,
                        "interval_minutes": 240,
                    },
                )
                ids["plan"] = plan["plan_id"]
                job = connection.execute("SELECT job_id FROM jobs LIMIT 1").fetchone()[
                    "job_id"
                ]
                ids["job"] = job
                run = autopilot.create_run(
                    connection,
                    job={"job_id": job, "match_score": 70},
                    mode="review",
                    trigger="user",
                    plan_id=plan["plan_id"],
                )
                ids["run"] = run["run_id"]
                interview = create_interview(
                    connection,
                    {
                        "company": "Beta Secret Org",
                        "role": "BETA-SECRET-JOB",
                        "interview_type": "recruiter_screen",
                        "start_at": "2027-01-10T15:00:00+00:00",
                        "timezone": "UTC",
                    },
                )
                ids["interview"] = interview["interview_id"]
                recruiter = create_recruiter(
                    connection,
                    {
                        "name": "Beta Recruiter",
                        "company": "Beta Secret Org",
                        "email": "r@beta.test",
                    },
                )
                ids["recruiter"] = recruiter["recruiter_id"]
                task = create_task(
                    connection,
                    title="BETA-SECRET-TASK",
                    kind="reminder",
                    due_at=None,
                    job_id=None,
                    interview_id=None,
                    recruiter_id=None,
                    source="test",
                )
                ids["task"] = task["task_id"]
                connection.execute(
                    "INSERT INTO schedules (kind, keywords, location, interval_minutes, max_items, min_fit, enabled, next_run_at, last_run_at, last_status) VALUES ('jobs', 'BETA-SECRET', '', 60, 3, 50, 0, 9999999999, 0, '')"
                )
                ids["schedule"] = connection.execute(
                    "SELECT MAX(id) FROM schedules"
                ).fetchone()[0]
                ids["document"] = connection.execute(
                    "SELECT document_id FROM documents LIMIT 1"
                ).fetchone()["document_id"]
        ids["members"] = [
            getattr(b, who).member_id
            for who in (
                "owner",
                "admin",
                "coach",
                "candidate",
                "other",
                "billing",
                "auditor",
            )
        ]
        # Developer objects (OAuth app, access token, webhook): their ids are tokens, not hex.
        made = app.oauth.create_client(
            b.owner,
            {
                "name": "Beta partner",
                "redirect_uris": ["https://partner.beta.test/cb"],
                "scopes": ["hub:read"],
            },
        )
        pat = app.oauth.create_personal_token(
            b.owner, {"name": "Beta script", "scopes": ["hub:read"]}
        )
        previous = os.environ.get("AIRA_WEBHOOKS_ALLOW_LOCAL")
        os.environ["AIRA_WEBHOOKS_ALLOW_LOCAL"] = "1"
        try:
            hook = app.webhooks.create(
                b.owner, {"url": "http://127.0.0.1:9/beta-hook", "events": ["*"]}
            )
        finally:
            if previous is None:
                os.environ.pop("AIRA_WEBHOOKS_ALLOW_LOCAL", None)
            else:
                os.environ["AIRA_WEBHOOKS_ALLOW_LOCAL"] = previous
        ids["developer"] = [made["client_id"], pat["id"], hook["endpoint_id"]]
        ids["candidate"] = b.candidate.member_id
        ids["coach"] = b.coach.member_id
        ids["slug"] = app.tenancy.org(b.org_id)["slug"]
        pool = [
            ids["invite"],
            ids["export"],
            ids["plan"],
            ids["run"],
            ids["interview"],
            ids["recruiter"],
            ids["task"],
            ids["document"],
            *ids["members"],
        ]
        ids["hex32"] = [
            value
            for value in dict.fromkeys(pool)
            if re.fullmatch(r"[0-9a-f]{32}", str(value))
        ]
        return ids


@pytest.fixture
def world(cloud):  # noqa: F811
    return World(cloud)


# ---------------------------------------------------------------------------- building requests


GROUP_RE = re.compile(r"(?<!\\)\((?!\?)")


def groups_of(pattern: str) -> list[str]:
    """The inner text of each top-level capture group of a route pattern."""
    inner, depth, start, found = [], 0, 0, []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            if depth == 0 and not pattern.startswith("(?", i):
                start = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start:
                found.append(pattern[start:i])
                start = 0
        i += 1
    del inner
    return found


def fill(pattern: str, values: list[str]) -> str:
    out, index, depth, i = [], 0, 0, 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            out.append(pattern[i + 1] if pattern[i + 1] in "./-_" else "")
            i += 2
            continue
        if ch == "(" and depth == 0 and not pattern.startswith("(?", i):
            out.append(values[index])
            index += 1
            depth = 1
            i += 1
            while depth:
                if pattern[i] == "\\":
                    i += 2
                    continue
                depth += {"(": 1, ")": -1}.get(pattern[i], 0)
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def candidates_for(group: str, ids: dict[str, Any]) -> list[str] | None:
    if group in EXEMPT_GROUPS:
        return None
    if group == r"[0-9a-f]{32}":
        return list(ids["hex32"])
    if (
        group == r"[a-z0-9-]{1,60}"
    ):  # an organization slug (public: it names the sign-in page); tenant B has no SSO configured
        return [ids.get("slug", "beta-secret-org")]
    if group == r"[A-Za-z0-9_-]{8,80}":  # OAuth apps, access tokens and webhooks
        return list(ids["developer"])
    if group == r"[0-9a-f]{16}":
        return [ids["hex32"][0][:16], "f" * 16]
    if group.startswith("[A-Za-z0-9][A-Za-z0-9_.:-]"):
        return [ids["job"]]
    if group == r"\d+":
        return [str(ids["schedule"])]
    if group.startswith("cs_standin_"):
        return ["cs_standin_" + "a" * 20]
    if group.startswith("cus_standin_"):
        return ["cus_standin_" + "a" * 12]
    raise AssertionError(
        f"No identifier rule for the path group {group!r}; add one to EXEMPT_GROUPS (with a reason) or to candidates_for"
    )


def body_for(world: World) -> dict[str, Any]:
    b = world.b_ids
    return {
        "role": "coach",
        "reason": "isolation test",
        "scope": "view",
        "coach_id": b["coach"],
        "candidate_id": b["candidate"],
        "from_coach": b["coach"],
        "to_coach": b["coach"],
        "emails": [],
        "plan": "team",
        "seats": 30,
        "password": "x" * 14,
        "kinds": ["automation"],
        "token": "x",
        "code": "000000",
        "stage": "saved",
        "mode": "review",
        "answers": {"x": "y"},
        "canonical_field": "full_name",
        "value": "x",
        "status": "active",
        "days": 1,
        "hours": 1,
        "per_minute": 5,
        "invoice_id": b["invite"],
        "title": "t",
        "name": "n",
        "url": "https://boards.greenhouse.io/x/jobs/1",
    }


def route_table() -> list[tuple[str, str, str, str]]:
    """(kind, method, pattern, name) for every route of the hosted API."""
    table = [("cloud", r.method, r.pattern.pattern, r.name) for r in CLOUD_ROUTES]
    cloud_keys = {(m, p) for _k, m, p, _n in table}
    for method, pattern, name, _auth in local.ROUTES:
        table.append(("local", method, pattern.pattern, name))
    del cloud_keys
    return table


# ---------------------------------------------------------------------------- the sweep


def leak(body: Any) -> str | None:
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    for marker in MARKERS:
        if marker in text:
            return marker
    return None


def call(
    browser: Browser, method: str, path: str, payload: dict[str, Any], **kw: Any
) -> tuple[int, Any]:
    body = payload if method in {"POST", "PATCH", "PUT"} else None
    return browser.call(method, path, body, raw=False, **kw)


def test_every_route_with_another_tenants_ids_is_refused_and_leaks_nothing(world):
    app = world.app
    owner = sign_in(app, world.a.owner)
    admin = sign_in(app, world.a.admin)
    candidate = sign_in(app, world.a.candidate)
    coach = sign_in(app, world.a.coach)
    payload = body_for(world)
    own_candidate = world.a.candidate.member_id
    exercised: set[tuple[str, str]] = set()
    failures: list[str] = []
    tried = 0

    for kind, method, pattern, name in route_table():
        rule = POLICY.get(name)
        groups = groups_of(pattern)
        if (
            kind == "local"
            and rule is not None
            and rule.scope in {"apikey", "public", "deny", "operator"}
        ):
            exercised.add((method, pattern))
            continue
        if not groups:
            continue
        pools = [candidates_for(g, world.b_ids) for g in groups]
        if any(pool is None for pool in pools):
            exercised.add((method, pattern))
            continue
        # org-level and /api/me routes: the owner (and the candidate for /api/me); workspace routes: the candidate and an approving coach acting for A's candidate
        if kind == "cloud":
            actors = [(owner, {}), (candidate, {})]
        elif rule is not None and rule.scope == "org_api":
            actors = [(owner, {}), (admin, {})]
        else:
            actors = [(candidate, {}), (coach, {"as_candidate": own_candidate})]
        combos = [
            [
                pool[(offset + position) % len(pool)]
                for position, pool in enumerate(pools)
            ]
            for offset in range(len(pools[0]))
        ]
        for actor, extra in actors:
            for values in combos:
                path = fill(pattern, values)
                status, body = call(actor, method, path, payload, **extra)
                tried += 1
                reason = None
                failed_sso_redirect = status == 302 and "sso_error" in actor.header(
                    "Location"
                )
                # the public sign-in page answers any slug with {"branded": false}
                empty_branding = (
                    status == 200
                    and path.endswith("/branding")
                    and body == {"branded": False}
                )
                if (
                    status not in SAFE
                    and not failed_sso_redirect
                    and not empty_branding
                ):
                    reason = f"status {status}"
                elif marker := leak(body):
                    reason = f"leaked {marker!r}"
                if reason:
                    failures.append(
                        f"{method} {path} as {'owner' if actor is owner else 'candidate' if actor is candidate else 'actor'}: {reason}"
                    )
        exercised.add((method, pattern))
        # re-establish sessions a route may have ended (sign-out routes take ids too)
        for browser, who in (
            (owner, world.a.owner),
            (candidate, world.a.candidate),
            (coach, world.a.coach),
            (admin, world.a.admin),
        ):
            if not browser.cookies:
                browser.login(who.email)
                if browser is owner or browser is admin:
                    sign_in_mfa(app, browser, who)
    assert not failures, "\n".join(failures[:25])
    assert tried > 400, f"the sweep only made {tried} requests"


def sign_in_mfa(app: Any, browser: Browser, who: Any) -> None:
    from aira_cloud import crypto

    secret = app.__dict__.get("_test_totp", {}).get(who.email)
    if secret:
        app.clock.advance(seconds=30)
        browser.call(
            "POST",
            "/api/auth/mfa/verify",
            {"code": crypto.totp_code(secret, app.clock.now())},
        )


def test_cross_tenant_reads_with_no_identifier_do_not_leak(world):
    """Every GET route, as every role of tenant A: nothing of tenant B shows up."""
    app = world.app
    browsers = {
        "owner": sign_in(app, world.a.owner),
        "admin": sign_in(app, world.a.admin),
        "auditor": sign_in(app, world.a.auditor),
        "billing": sign_in(app, world.a.billing),
        "coach": sign_in(app, world.a.coach),
        "candidate": sign_in(app, world.a.candidate),
    }
    own = world.a.candidate.member_id
    checked = 0
    for kind, method, pattern, name in route_table():
        if method != "GET" or groups_of(pattern):
            continue
        rule = POLICY.get(name)
        if (
            kind == "local"
            and rule is not None
            and rule.scope in {"apikey", "operator", "deny"}
        ):
            continue
        path = fill(pattern, []) if "(" not in pattern else None
        if (
            path is None
            or path in {"/", "/api/auth/logout"}
            or path.startswith("/api/platform")
            or path.startswith("/scim")
        ):
            continue
        path = path.replace("(?:/v1)?", "")
        for who, browser in browsers.items():
            for extra in ({}, {"as_candidate": own}):
                status, body = browser.call("GET", path, None, raw=True, **extra)
                checked += 1
                assert leak(body) is None, f"{who} GET {path} leaked tenant B data"
    assert checked > 300


def test_a_candidate_header_naming_another_tenants_candidate_is_a_404(world):
    app = world.app
    foreign = world.b.candidate.member_id
    for who, org_member in (
        ("owner", world.a.owner),
        ("admin", world.a.admin),
        ("coach", world.a.coach),
        ("auditor", world.a.auditor),
        ("candidate", world.a.candidate),
    ):
        browser = sign_in(app, org_member)
        for path in (
            "/api/jobs",
            "/api/profile",
            "/api/documents",
            "/api/interviews",
            "/api/autopilot",
            "/api/analytics",
        ):
            status, body = browser.call("GET", path, as_candidate=foreign)
            assert status == 404, f"{who} {path}: {status} {body}"
        status, body = browser.call("GET", f"/api/jobs?candidate={foreign}")
        assert status == 404
        status, _ = browser.call("GET", f"/api/org/candidates/{foreign}")
        assert status == 404


def test_body_identifiers_from_another_tenant_are_not_found(world):
    app = world.app
    owner = sign_in(app, world.a.owner)
    b = world.b_ids
    other_coach = add_member(app, world.a.owner, "second.coach@alpha.test", "coach")
    attempts = [
        (
            "PUT",
            "/api/org/assignments",
            {
                "coach_id": b["coach"],
                "candidate_id": world.a.candidate.member_id,
                "scope": "view",
            },
        ),
        (
            "PUT",
            "/api/org/assignments",
            {
                "coach_id": other_coach.member_id,
                "candidate_id": b["candidate"],
                "scope": "view",
            },
        ),
        (
            "POST",
            "/api/org/assignments/reassign",
            {"from_coach": b["coach"], "to_coach": other_coach.member_id},
        ),
        (
            "POST",
            "/api/org/assignments/reassign",
            {"from_coach": other_coach.member_id, "to_coach": b["coach"]},
        ),
        (
            "POST",
            "/api/org/invites",
            {"email": "x@alpha.test", "role": "candidate", "coach_id": b["coach"]},
        ),
        ("POST", "/api/org/exports", {"candidate_id": b["candidate"]}),
    ]
    for method, path, body in attempts:
        status, data = owner.call(method, path, body)
        assert status == 404, f"{method} {path} {body}: {status} {data}"
        assert leak(data) is None
    # nothing of B moved
    assert app.tenancy.list_assignments(world.b.owner)


def test_tenant_b_state_is_unchanged_after_the_sweep_style_attacks(world):
    app = world.app
    before = app.audit.count(world.b.org_id)
    owner = sign_in(app, world.a.owner)
    for member_id in world.b_ids["members"]:
        owner.call("DELETE", f"/api/org/members/{member_id}")
        owner.call("PATCH", f"/api/org/members/{member_id}", {"role": "auditor"})
        owner.call("POST", f"/api/org/members/{member_id}/mfa-reset")
        owner.call("DELETE", f"/api/org/candidates/{member_id}/workspace")
        owner.call(
            "POST", f"/api/org/candidates/{member_id}/pause", {"reason": "attack"}
        )
    owner.call("DELETE", f"/api/org/invites/{world.b_ids['invite']}")
    owner.call("POST", f"/api/org/invites/{world.b_ids['invite']}/resend")
    assert app.audit.count(world.b.org_id) == before, (
        "an attack on another tenant must not even write to its audit log"
    )
    assert len(app.tenancy.list_members(world.b.owner)) == 7
    assert len(app.identity.list_invites(world.b.owner)) == 1
    assert (
        app.workspaces.row(world.b.org_id, world.b.candidate.member_id)["paused_at"]
        is None
    )
    assert app.workspaces.data_path(
        world.b.org_id, world.b.candidate.member_id
    ).exists()


def test_each_tenants_workspace_lives_in_its_own_directory_tree(world):
    app = world.app
    a_dir, b_dir = (
        app.workspaces.org_dir(world.a.org_id),
        app.workspaces.org_dir(world.b.org_id),
    )
    assert a_dir != b_dir and a_dir.parent == b_dir.parent
    for org in (world.a, world.b):
        path = app.workspaces.data_path(org.org_id, org.candidate.member_id)
        assert org.org_id in str(path) and path.exists()
    # one tenant's session cannot be replayed against another's workspace by swapping the candidate header or id
    owner_b = sign_in(app, world.b.owner)
    status, data = owner_b.call(
        "GET", f"/api/org/candidates/{world.a.candidate.member_id}"
    )
    assert status == 404


def test_positive_controls_the_substituted_ids_reach_real_handlers(world):
    """If the sweep's ids never matched anything, it would pass for the wrong reason: prove they do match B's own tenant."""
    app = world.app
    b_owner = sign_in(app, world.b.owner)
    b_candidate = sign_in(app, world.b.candidate)
    ids = world.b_ids
    assert b_owner.call("GET", f"/api/org/candidates/{ids['candidate']}")[0] == 200
    assert b_candidate.call("GET", f"/api/jobs/{ids['job']}")[0] == 200
    assert b_candidate.call("GET", f"/api/interviews/{ids['interview']}")[0] == 200
    assert b_candidate.call("GET", f"/api/recruiters/{ids['recruiter']}")[0] == 200
    assert b_candidate.call("GET", f"/api/autopilot/runs/{ids['run']}")[0] == 200
    assert (
        b_candidate.call("GET", f"/api/documents/{ids['document']}/versions")[0] == 200
    )
    status, data = b_owner.call(
        "GET", f"/api/org/exports/{ids['export']}/download", raw=True
    )
    assert status in (
        200,
        403,
    )  # an owner may not download a candidate's export, but the id is real
    a_candidate = sign_in(app, world.a.candidate)
    for path in (
        f"/api/jobs/{ids['job']}",
        f"/api/interviews/{ids['interview']}",
        f"/api/recruiters/{ids['recruiter']}",
        f"/api/autopilot/runs/{ids['run']}",
        f"/api/documents/{ids['document']}/versions",
    ):
        assert a_candidate.call("GET", path)[0] == 404, path


BODY_IDENTIFIER_ROUTES = {
    "assignments_put",
    "assignments_reassign",
    "invites_create",
    "exports_create",
}  # tested in test_body_identifiers_from_another_tenant_are_not_found
OPERATOR_AND_SCIM_ROUTES_REASON = "operator routes need an operator session and SCIM routes a bearer token; a tenant member's cookie is refused (401)"


def test_every_route_is_accounted_for_in_the_sweep():
    """No route may be added without a decision.

    * a path with identifiers is swept (or its identifier kind is named in EXEMPT_GROUPS with a reason);
    * a GET with no identifier is swept for leaks;
    * a mutating route with no path identifier either takes nothing from the request but the caller's own session, or reads
      identifiers from its body and is then listed in BODY_IDENTIFIER_ROUTES and covered by its own test.
    """
    import inspect

    table = route_table()
    for _kind, _method, pattern, _name in table:
        for group in groups_of(pattern):
            candidates_for(
                group,
                {
                    "hex32": ["0" * 32],
                    "job": "j",
                    "schedule": 1,
                    "developer": ["d" * 8],
                },
            )  # raises when a group has no rule
    assert [n for k, m, p, n in table if k == "local" and n not in POLICY] == []
    reads_ids = re.compile(
        r"""(?:body|payload)\.get\(\s*["']((?!\w*(?:client_id|entity_id)\b)\w*_id|from_coach|to_coach)["']|_body_str\(req,\s*["']((?!\w*(?:client_id|entity_id)\b)\w*_id)["']\)|body\[["']((?!\w*(?:client_id|entity_id)\b)\w*_id)["']\]"""
    )
    detected = set()
    for route in CLOUD_ROUTES:
        if (
            route.pattern.groups == 0
            and route.method != "GET"
            and reads_ids.search(inspect.getsource(route.handler))
        ):
            detected.add(route.name)
    operator_or_scim = {
        r.name
        for r in CLOUD_ROUTES
        if r.auth in {"operator"} or r.name.startswith("scim_")
    }
    undeclared = detected - BODY_IDENTIFIER_ROUTES - operator_or_scim
    assert not undeclared, (
        f"these routes take identifiers from the body; add a body-identifier test and list them: {sorted(undeclared)}"
    )
    assert BODY_IDENTIFIER_ROUTES <= {r.name for r in CLOUD_ROUTES}
    assert len([1 for _k, _m, p, _n in table if groups_of(p)]) > 120
    assert len([1 for _k, m, p, _n in table if m == "GET" and not groups_of(p)]) > 80
