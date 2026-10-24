"""Demo organizations for development and walkthroughs. Never run against a real deployment."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from aira import autopilot

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

DEMO_PASSWORD = "demo-password-123"
CONSENTS = ["automation", "coach_access", "sensitive_policy"]


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _invite(app: CloudApp, owner: Any, email: str, role: str, **kw: Any) -> Any:
    from aira_cloud.mail import link_from

    app.identity.invite(owner, email, role, **kw)
    token = link_from(app.mail.latest(email, "invite")).rsplit("/", 1)[1]
    accepted = app.identity.accept_invite(
        token, DEMO_PASSWORD, name=email.split("@")[0].replace(".", " ").title()
    )
    principal = app.identity.principal_for_token(accepted["token"])
    assert principal is not None
    return principal


def _seed_candidate(
    app: CloudApp,
    org_id: str,
    principal: Any,
    *,
    title: str,
    company: str,
    held: bool = False,
    applied: int = 0,
) -> None:
    import auto_apply_assistant as local

    app.tenancy.record_consent(principal, CONSENTS)
    with app.workspaces.bind(org_id, principal.member_id, kind="system", source="demo"):
        now = datetime.now(timezone.utc)
        with local._connect() as connection:
            for key, value in (
                ("full_name", principal.name or principal.email),
                ("email", principal.email),
                ("phone", "+1 555 010 0100"),
                ("location", "Seattle, WA"),
                ("years_of_experience", "9"),
                ("career_profile", "cloud_architect"),
                ("work_authorization", "authorized"),
            ):
                connection.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (f"application_{key}", value),
                )
            connection.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('resume', ?)",
                (
                    f"{principal.name or 'Candidate'}, {title}. Nine years of AWS, Kubernetes and Terraform delivery. "
                    * 3,
                ),
            )
            plan = autopilot.create_plan(
                connection,
                {
                    "name": f"{title} search",
                    "positions": [title],
                    "sources": ["aira_jobs"],
                    "boards": [],
                    "mode": "review",
                    "min_score": 60,
                    "max_per_run": 3,
                    "interval_minutes": 240,
                },
            )
            for index in range(max(applied, 1) + (1 if held else 0)):
                job_id = f"demo-{principal.member_id[:6]}-{index}"
                connection.execute(
                    """INSERT OR REPLACE INTO jobs (job_id, title, company, location, job_url, application_url, stage, updated_at, source, match_score, match_classification)
                    VALUES (?, ?, ?, 'Remote, United States', ?, ?, 'applied', ?, 'greenhouse', ?, 'Strong match')""",
                    (
                        job_id,
                        title,
                        f"{company} {index + 1}",
                        f"https://example.test/jobs/{job_id}",
                        f"https://boards.greenhouse.io/demo/jobs/{index}",
                        _iso(now),
                        82 - index,
                    ),
                )
                if index < applied:
                    connection.execute(
                        "INSERT OR IGNORE INTO application_submissions (submission_id, job_id, submitted_at, method, actor, correlation_id, idempotency_key) VALUES (?, ?, ?, 'auto', 'demo', 'demo', ?)",
                        (
                            f"sub-{job_id}",
                            job_id,
                            _iso(now - timedelta(days=index)),
                            f"demo:{job_id}",
                        ),
                    )
                elif held:
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
                                    "label": "Why do you want to work at this company?",
                                    "required": True,
                                    "type": "textarea",
                                },
                                {
                                    "label": "Are you legally authorized to work in the United States?",
                                    "required": True,
                                    "type": "select",
                                },
                            ],
                            "finished_at": _iso(now - timedelta(hours=30)),
                        },
                    )
                    connection.execute(
                        "UPDATE jobs SET stage = 'needs_review' WHERE job_id = ?",
                        (job_id,),
                    )
    app.workspaces.invalidate_snapshot(org_id, principal.member_id)


def seed_demo(app: CloudApp) -> dict[str, Any]:
    """Three organizations on different plans, every role, candidates with data, a held question and a coach queue."""
    from aira_cloud.mail import link_from

    if (
        app.config.base_url.startswith("https://")
        and "localhost" not in app.config.base_url
        and "127.0.0.1" not in app.config.base_url
    ):
        raise RuntimeError(
            "seed-demo is for development; refusing to run against an https deployment."
        )
    accounts: dict[str, Any] = {"password": DEMO_PASSWORD, "organizations": []}

    def signup(name: str, owner_email: str) -> Any:
        result = app.identity.signup_org(
            name, owner_email, DEMO_PASSWORD, owner_name="Olivia Owner"
        )
        token = link_from(app.mail.latest(owner_email, "verify_email")).rsplit("/", 1)[
            1
        ]
        app.identity.verify_email(token)
        return result["org"]["org_id"], app.identity.principal_for_token(
            result["token"]
        )

    def pay(org_id: str, plan: str, seats: int) -> None:
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

    org_id, owner = signup("Northwind Outplacement", "owner@northwind.test")
    pay(org_id, "team", 50)
    app.tenancy.update_policy(
        owner,
        {
            "linkedin_allowed": True,
            "allowed_sources": [
                "aira_jobs",
                "company_boards",
                "google_jobs",
                "linkedin",
            ],
            "submission_mode": "auto",
            "daily_cap_per_candidate": 15,
        },
    )
    admin = _invite(app, owner, "admin@northwind.test", "admin")
    coach_a = _invite(app, owner, "coach.ana@northwind.test", "coach")
    coach_b = _invite(app, owner, "coach.ben@northwind.test", "coach")
    billing = _invite(app, owner, "billing@northwind.test", "billing")
    auditor = _invite(app, owner, "auditor@northwind.test", "auditor")
    people = []
    for index, (name, title) in enumerate(
        (
            ("cam.cole", "Cloud Architect"),
            ("dee.dunn", "Platform Engineer"),
            ("eli.ernst", "Data Engineer"),
            ("fay.fox", "Security Architect"),
        )
    ):
        candidate = _invite(app, owner, f"{name}@northwind.test", "candidate")
        coach = coach_a if index < 2 else coach_b
        app.tenancy.assign_coach(
            owner,
            coach.member_id,
            candidate.member_id,
            "approve" if index == 0 else "act",
        )
        _seed_candidate(
            app,
            org_id,
            candidate,
            title=title,
            company="Contoso",
            held=index in (0, 2),
            applied=3 - index if index < 3 else 0,
        )
        people.append(candidate.email)
    accounts["organizations"].append(
        {
            "name": "Northwind Outplacement",
            "plan": "team",
            "owner": owner.email,
            "admin": admin.email,
            "coaches": [coach_a.email, coach_b.email],
            "billing": billing.email,
            "auditor": auditor.email,
            "candidates": people,
            "note": "Owners and admins on Team must set up MFA at first sign-in (any authenticator app).",
        }
    )

    org2, owner2 = signup("Contoso Staffing", "owner@contoso.test")
    pay(org2, "starter", 10)
    candidate2 = _invite(app, owner2, "gil.grant@contoso.test", "candidate")
    coach2 = _invite(app, owner2, "coach@contoso.test", "coach")
    app.tenancy.assign_coach(owner2, coach2.member_id, candidate2.member_id, "act")
    _seed_candidate(
        app,
        org2,
        candidate2,
        title="Product Manager",
        company="Fabrikam",
        held=True,
        applied=1,
    )
    accounts["organizations"].append(
        {
            "name": "Contoso Staffing",
            "plan": "starter",
            "owner": owner2.email,
            "coaches": [coach2.email],
            "candidates": [candidate2.email],
        }
    )

    org3, owner3 = signup("Fabrikam University Careers", "owner@fabrikam.test")
    accounts["organizations"].append(
        {
            "name": "Fabrikam University Careers",
            "plan": "trial (14 days)",
            "owner": owner3.email,
        }
    )

    for email, role in (
        ("ops@aira.test", "platform_admin"),
        ("support@aira.test", "support"),
    ):
        try:
            app.identity.create_operator(
                email,
                DEMO_PASSWORD,
                role=role,
                name=email.split("@")[0].title(),
                by="demo",
            )
        except Exception:  # noqa: BLE001 - already created on an earlier run
            pass
    accounts["operators"] = [
        {"email": "ops@aira.test", "role": "platform_admin"},
        {"email": "support@aira.test", "role": "support"},
    ]
    return accounts


__all__ = ["seed_demo", "DEMO_PASSWORD", "json", "sqlite3"]
