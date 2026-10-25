"""Helpers for the hosted-shell tests: an isolated CloudApp with a controllable clock, and the
sign-up, invite and payment steps a person (or a payment provider) would take."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import pg_support
from aira.hosting import HOOKS
from aira_cloud import crypto
from aira_cloud.app import CloudApp
from aira_cloud.config import CloudConfig, FakeClock
from aira_cloud.mail import link_from
from aira_cloud.rbac import Principal

PASSWORD = "correct horse battery staple"
START = 1_790_000_000  # 2026-09-21, a fixed point so month and day keys are stable


def make_app(tmp_path: Path, **overrides: Any) -> CloudApp:
    crypto.SCRYPT_N = (
        2**11
    )  # tests hash many passwords; the cost is stored in each hash, so verification is unaffected
    limits = {
        "ip_requests_per_minute": 10**7,
        "org_requests_per_minute": 10**7,
        "member_requests_per_minute": 10**7,
    }
    defaults = {
        "data_root": tmp_path / "cloud",
        "base_url": "http://127.0.0.1:8780",
        "master_key": "",
        **limits,
    }
    if pg_support.enabled() and "db_backend" not in overrides:
        database = pg_support.new_database(
            str(defaults["data_root"])
        )  # a fresh PostgreSQL database per installation
        database.pop("_name")
        pg_support.export_env(database)
        defaults.update(database)
    config = CloudConfig(**{**defaults, **overrides})
    return CloudApp(config, clock=FakeClock(START))


@pytest.fixture
def cloud(tmp_path: Path):
    HOOKS.reset()
    app = make_app(tmp_path)
    yield app
    app.close()


def token_from_mail(app: CloudApp, recipient: str, kind: str) -> str:
    link = link_from(app.mail.latest(recipient, kind))
    assert link, f"no {kind} mail for {recipient}"
    return link.rsplit("/", 1)[1]


def principal(app: CloudApp, token: str) -> Principal:
    found = app.identity.principal_for_token(token)
    assert found is not None
    return found


def signup(
    app: CloudApp,
    name: str = "Acme Outplacement",
    email: str = "owner@acme.test",
    *,
    verify: bool = True,
) -> dict[str, Any]:
    result = app.identity.signup_org(name, email, PASSWORD, owner_name="Olive Owner")
    if verify:
        app.identity.verify_email(token_from_mail(app, email, "verify_email"))
    result["owner"] = principal(app, result["token"])
    return result


def pay(app: CloudApp, org_id: str, plan: str = "team", seats: int = 25) -> None:
    """Complete hosted checkout the way the provider would: a signed webhook."""
    payload, signature = app.billing.standin.make_event(
        "checkout.completed",
        {
            "org_id": org_id,
            "plan_id": plan,
            "seats": seats,
            "customer_id": f"cus_{org_id[:8]}",
            "subscription_id": f"sub_{org_id[:8]}",
        },
    )
    app.billing.handle_webhook("standin", payload, signature)


def webhook(app: CloudApp, event_type: str, **data: Any) -> dict[str, Any]:
    payload, signature = app.billing.standin.make_event(event_type, data)
    return app.billing.handle_webhook("standin", payload, signature)


def add_member(
    app: CloudApp, owner: Principal, email: str, role: str, **invite: Any
) -> Principal:
    app.identity.invite(owner, email, role, **invite)
    token = token_from_mail(app, email, "invite")
    accepted = app.identity.accept_invite(
        token, PASSWORD, name=email.split("@")[0].title()
    )
    return principal(app, accepted["token"])


def audit_actions(app: CloudApp, org_id: str, prefix: str = "") -> list[str]:
    return [row["action"] for row in app.audit.list(org_id, limit=500, action=prefix)]


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


class Org:
    """An organization with one member of every role (and a second candidate), on a paid plan."""

    def __init__(self, app: CloudApp, org_id: str, **people: Principal) -> None:
        self.app = app
        self.org_id = org_id
        self.__dict__.update(people)

    def relogin(self, who: str) -> Principal:
        current = getattr(self, who)
        token = self.app.identity.login(current.email, PASSWORD, org=self.org_id)[
            "token"
        ]
        refreshed = principal(self.app, token)
        setattr(self, who, refreshed)
        return refreshed


def build_org(
    app: CloudApp,
    name: str = "Acme Outplacement",
    *,
    plan: str = "team",
    seats: int = 25,
    slug_email: str = "acme.test",
    apps: str | list[str] | None = None,
) -> Org:
    """``apps`` turns app add-ons on after checkout: a list of app ids, or ``"all"`` for every add-on the plan offers."""
    result = signup(app, name, f"owner@{slug_email}")
    org_id = result["org"]["org_id"]
    if plan != "trial":
        pay(app, org_id, plan, seats)
    owner = result["owner"]
    if apps:
        subscribe_apps(app, owner, apps)
    people = {
        "owner": owner,
        "admin": add_member(app, owner, f"admin@{slug_email}", "admin"),
        "coach": add_member(app, owner, f"coach@{slug_email}", "coach"),
        "candidate": add_member(app, owner, f"cand@{slug_email}", "candidate"),
        "other": add_member(app, owner, f"other@{slug_email}", "candidate"),
        "billing": add_member(app, owner, f"billing@{slug_email}", "billing"),
        "auditor": add_member(app, owner, f"auditor@{slug_email}", "auditor"),
    }
    return Org(app, org_id, **people)


def subscribe_apps(app: CloudApp, owner: Principal, apps: str | list[str]) -> list[str]:
    """Turn on app add-ons for the owner's organization (``"all"`` = every add-on the plan sells); returns the ids."""
    from aira.apps import APP_IDS
    from aira_cloud.plans import app_state

    plan_id = app.billing.subscription(owner.org_id)["plan_id"]
    wanted = (
        [a for a in APP_IDS if app_state(plan_id, a) == "addon"]
        if apps == "all"
        else list(apps)
    )
    turned_on = []
    for app_id in wanted:
        if app_state(plan_id, app_id) == "addon":
            app.billing.subscribe_app(owner, app_id)
            turned_on.append(app_id)
    return turned_on


def local_module():
    import auto_apply_assistant

    return auto_apply_assistant


def seed_workspace(
    app: CloudApp,
    org_id: str,
    candidate_id: str,
    *,
    profile: bool = True,
    resume: bool = True,
    document: bool = True,
    job_title: str = "",
) -> dict[str, Any]:
    """Put realistic data in a candidate's workspace through the same code the product uses."""

    local = local_module()
    out: dict[str, Any] = {}
    with app.workspaces.bind(
        org_id, candidate_id, kind="system", source="test"
    ) as user:
        with local._connect() as connection:
            if profile:
                for key, value in (
                    ("full_name", "Cam Candidate"),
                    ("email", user["email"]),
                    ("phone", "+1 555 010 0000"),
                    ("location", "Seattle, WA"),
                ):
                    connection.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (f"application_{key}", value),
                    )
            if resume:
                connection.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('resume', ?)",
                    (
                        "Cloud architect with twelve years of AWS and Kubernetes experience. "
                        * 4,
                    ),
                )
            if job_title:
                connection.execute(
                    "INSERT OR REPLACE INTO jobs (job_id, title, company, location, job_url, stage, updated_at, source) VALUES (?, ?, 'Acme', 'Remote', ?, 'discovered', '2026-09-21T00:00:00', 'greenhouse')",
                    (
                        f"job-{candidate_id[:10]}",
                        job_title,
                        f"https://example.test/jobs/{candidate_id[:10]}",
                    ),
                )
        if document:
            store = local._document_store()
            with local._connect() as connection:
                store.ensure_schema(connection)
                record = store.store(
                    connection,
                    owner_id=candidate_id,
                    kind="master_resume",
                    filename="resume.txt",
                    data=b"Cam Candidate resume. " * 20,
                    title="Resume",
                    actor="test",
                )
            out["document_id"] = record["document_id"]
    app.workspaces.invalidate_snapshot(org_id, candidate_id)
    return out


class Browser:
    """A cookie jar that calls the gateway directly: a person (or an API client) using the hosted product."""

    def __init__(self, app: CloudApp, *, operator: bool = False) -> None:
        from aira_cloud.gateway import Gateway

        self.app = app
        self.gateway = getattr(app, "gateway", None) or Gateway(app)
        self.cookies: dict[str, str] = {}
        self.last_headers: list[tuple[str, str]] = []

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        raw: bool = False,
        as_candidate: str = "",
    ) -> tuple[int, Any]:
        from aira_cloud.webio import HttpRequest

        sent = {
            "Host": "127.0.0.1:8780",
            "Origin": "http://127.0.0.1:8780",
            "X-Requested-With": "aira",
            **(headers or {}),
        }
        if as_candidate:
            sent["X-Aira-Candidate"] = as_candidate
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        request = HttpRequest.build(
            method,
            path,
            headers=sent,
            body=body if not isinstance(body, bytes) else body,
        )
        response = self.gateway.handle(request)
        for name, value in response.headers:
            if name.lower() == "set-cookie":
                pair = value.split(";", 1)[0]
                key, _, val = pair.partition("=")
                if "Max-Age=0" in value:
                    self.cookies.pop(key, None)
                else:
                    self.cookies[key] = val
        self.last_headers = response.headers
        if raw:
            return response.status, response.body
        try:
            return response.status, response.json()
        except ValueError:
            return response.status, response.body

    def header(self, name: str) -> str:
        for key, value in self.last_headers:
            if key.lower() == name.lower():
                return value
        return ""

    def login(self, email: str, org: str = "") -> dict[str, Any]:
        status, data = self.call(
            "POST",
            "/api/auth/login",
            {"email": email, "password": PASSWORD, "org": org},
        )
        assert status == 200, data
        return data


def sign_in(
    app: CloudApp, who: Principal | str, org: str = "", *, mfa: bool = True
) -> Browser:
    """Sign in, and (like a careful person on a plan that requires it) set up or give the second factor."""
    email = who.email if isinstance(who, Principal) else who
    browser = Browser(app)
    login = browser.login(email, org)
    secrets_by_email = app.__dict__.setdefault("_test_totp", {})
    if not mfa:
        return browser
    if login["mfa_required"]:
        browser.app.clock.advance(
            seconds=30
        )  # a fresh time step: the previous code cannot be replayed
        code = crypto.totp_code(secrets_by_email[email], app.clock.now())
        status, data = browser.call("POST", "/api/auth/mfa/verify", {"code": code})
        assert status == 200, data
    else:
        status, me = browser.call("GET", "/api/auth/me")
        if status == 403 or (
            status == 200
            and me.get("mfa", {}).get("required")
            and not me["mfa"]["enabled"]
        ):
            status, begun = browser.call("POST", "/api/auth/mfa/begin", {})
            assert status == 200, begun
            secrets_by_email[email] = begun["secret"]
            status, data = browser.call(
                "POST",
                "/api/auth/mfa/enable",
                {"code": crypto.totp_code(begun["secret"], app.clock.now())},
            )
            assert status == 200, data
    return browser


def seed_held_run(
    app: CloudApp, org_id: str, candidate_id: str, *, held_hours: int = 30
) -> dict[str, Any]:
    """A job with an automatic application held for two answers: one ordinary, one about work authorization."""
    from datetime import datetime, timedelta, timezone

    from aira import autopilot

    local = local_module()
    out: dict[str, Any] = {}
    with app.workspaces.bind(org_id, candidate_id, kind="system", source="test"):
        with local._connect() as connection:
            now = datetime.fromtimestamp(
                app.clock.now(), timezone.utc
            )  # the app's clock, which tests control
            plan = autopilot.create_plan(
                connection,
                {
                    "name": "Held plan",
                    "positions": ["Cloud Architect"],
                    "sources": ["aira_jobs"],
                    "boards": [],
                    "mode": "apply",
                    "min_score": 50,
                    "max_per_run": 2,
                    "interval_minutes": 240,
                },
            )
            job_id = f"held-{candidate_id[:8]}"
            connection.execute(
                "INSERT OR REPLACE INTO jobs (job_id, title, company, location, job_url, application_url, stage, updated_at, source, match_score) VALUES (?, 'Cloud Architect', 'Acme', 'Remote', 'https://example.test/j', 'https://boards.greenhouse.io/acme/jobs/9', 'needs_review', ?, 'greenhouse', 80)",
                (job_id, now.isoformat(timespec="seconds")),
            )
            run = autopilot.create_run(
                connection,
                job={"job_id": job_id, "match_score": 80},
                mode="apply",
                trigger="schedule",
                plan_id=plan["plan_id"],
            )
            autopilot.update_run(
                connection,
                run["run_id"],
                {
                    "status": "needs_review",
                    "unanswered": [
                        {
                            "label": "Why do you want to work at Acme?",
                            "required": True,
                            "type": "textarea",
                        },
                        {
                            "label": "Are you legally authorized to work in the United States?",
                            "required": True,
                            "type": "select",
                        },
                    ],
                    "answers": {
                        "Are you legally authorized to work in the United States?": "SECRET-AUTH-VALUE"
                    },
                    "finished_at": (now - timedelta(hours=held_hours)).isoformat(
                        timespec="seconds"
                    ),
                },
            )
            out.update(
                {"run_id": run["run_id"], "job_id": job_id, "plan_id": plan["plan_id"]}
            )
    app.workspaces.invalidate_snapshot(org_id, candidate_id)
    return out


CONSENTS = ["automation", "coach_access", "sensitive_policy"]
