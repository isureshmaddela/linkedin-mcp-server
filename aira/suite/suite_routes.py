"""The Business hub (``/api/suite/*``) and the public link endpoints (``/api/public/<token>``).

The hub adds up every industry app: revenue this month and by app, what customers did on public pages,
each app's setup checklist and headline numbers, the business profile customers see, and the email
outbox all apps share. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira import apps as app_catalog
from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public

BASE = "/api/suite"
TOKEN = f"({public.TOKEN_PATTERN})"


def _section_summary(connection: Any, package: str) -> dict[str, Any]:
    """One package's setup steps and headline numbers (Shop, Bookings...)."""
    steps: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    if package in kit.CHECKLISTS:
        try:
            steps = kit.CHECKLISTS[package](connection)
        except Exception:  # noqa: BLE001 - one app's numbers must not break the hub
            kit.suite.logger.exception("Checklist for %s failed", package)
    if package in kit.SUMMARIES:
        try:
            cards = list(kit.SUMMARIES[package](connection).get("cards", []))
        except Exception:  # noqa: BLE001
            kit.suite.logger.exception("Summary for %s failed", package)
    return {
        "id": package,
        "installed": package in kit.CHECKLISTS or package in kit.SUMMARIES,
        "checklist": steps,
        "progress": {
            "done": sum(1 for step in steps if step.get("done")),
            "total": len(steps),
        },
        "cards": cards,
    }


def _apps_summary(connection: Any) -> list[dict[str, Any]]:
    """One entry per business app; an app with several sections (Commerce: Shop and Bookings) lists each, and
    its checklist and numbers are the sections' together."""
    out = []
    for app in app_catalog.INDUSTRY_APPS:
        sections = [_section_summary(connection, package) for package in app.sections]
        steps = [step for section in sections for step in section["checklist"]]
        out.append(
            {
                "id": app.id,
                "home": app.home,
                "name": app.name,
                "tagline": app.tagline,
                "industry": app.industry,
                "installed": any(section["installed"] for section in sections),
                "sections": sections,
                "checklist": steps,
                "progress": {
                    "done": sum(1 for step in steps if step.get("done")),
                    "total": len(steps),
                },
                "cards": [card for section in sections for card in section["cards"]],
            }
        )
    return out


@aira_route("GET", f"{BASE}/home")
def home(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        kit.ensure_core(connection)
        profile = kit.business(connection)
        data = {
            "business": profile,
            "profile_complete": bool(
                profile["name"] and (profile["email"] or profile["phone"])
            ),
            "revenue": kit.revenue(connection),
            "apps": _apps_summary(connection),
            "activity": kit.activity(connection, limit=20),
            "outbox": kit.outbox_counts(connection),
            "email_ready": bool(kit.email_settings(connection)["smtp_host"]),
        }
        data["goal"] = _goal(connection, data["revenue"]["this_month_cents"])
    return 200, data


def _goal(connection: Any, this_month_cents: int) -> dict[str, Any]:
    """The monthly revenue goal the business set, and how far this month is."""
    target = int(kit.get_setting(connection, "core", "goal_cents", 0) or 0)
    return {
        "monthly_cents": target,
        "progress_percent": min(100, round(this_month_cents * 100 / target))
        if target
        else None,
        "remaining_cents": max(0, target - this_month_cents) if target else None,
    }


@aira_route("POST", f"{BASE}/goal")
def goal_save(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        target = kit.integer(
            request.payload.get("monthly_cents"),
            "the monthly goal (in cents)",
            default=0,
            minimum=0,
            maximum=10**11,
        )
        kit.set_setting(connection, "core", "goal_cents", target)
        connection.commit()
        return 200, {
            "goal": _goal(connection, kit.revenue(connection)["this_month_cents"])
        }


@aira_route("GET", f"{BASE}/business")
def business_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, {
            "business": kit.business(connection),
            "currencies": list(kit.CURRENCIES),
        }


@aira_route("POST", f"{BASE}/business")
def business_save(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        saved = kit.save_business(connection, request.payload)
        kit.log(
            connection,
            "core",
            "business",
            "Business profile updated",
            actor=_actor(request),
        )
        connection.commit()
    request.audit("suite.business_update", "suite", "business")
    return 200, {"business": saved}


@aira_route("GET", f"{BASE}/activity")
def activity(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        rows = kit.activity(
            connection,
            app=request.q("app"),
            limit=request.qint("limit", 50, 1, 500),
            before=request.q("before"),
        )
    return 200, {"activity": rows}


@aira_route("GET", f"{BASE}/revenue")
def revenue(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with request.connect() as connection:
        app = request.q("app")
        lines = kit.revenue_lines(connection, app=app)
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(
                    lines,
                    (
                        "at",
                        "app",
                        "label",
                        "customer",
                        "amount_cents",
                        "currency",
                        "source_type",
                        "source_id",
                        "voided_at",
                    ),
                ),
                "text/csv; charset=utf-8",
                filename="revenue.csv",
            )
        return 200, {
            "summary": kit.revenue(
                connection, app=app, months=request.qint("months", 12, 1, 24)
            ),
            "lines": lines[:200],
        }


# ---------------------------------------------------------------------------
# email (shared by every app)
# ---------------------------------------------------------------------------


def _email_view(request: Request, connection: Any) -> dict[str, Any]:
    settings = kit.email_settings(connection)
    return {
        "settings": settings,
        "has_password": bool(
            request.secret_store.get(connection, kit.SMTP_PASSWORD_SECRET)
        ),
        "ready": bool(settings["smtp_host"] and settings["from_email"]),
    }


@aira_route("GET", f"{BASE}/email")
def email_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, _email_view(request, connection)


@aira_route("POST", f"{BASE}/email")
def email_save(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with request.connect() as connection:
        kit.save_email_settings(connection, payload)
        if payload.get("smtp_host"):
            from aira.leads import mail as transport

            settings = kit.email_settings(connection)
            transport.check_smtp_host(settings["smtp_host"], int(settings["smtp_port"]))
        password = payload.get("smtp_password")
        if isinstance(password, str) and password:
            from aira.airaapi import store as airastore

            request.secret_store.put(
                connection, kit.SMTP_PASSWORD_SECRET, password, airastore.now_utc()
            )
        elif payload.get("clear_password"):
            request.secret_store.delete(connection, kit.SMTP_PASSWORD_SECRET)
        connection.commit()
        view = _email_view(request, connection)
    request.audit("suite.email_settings", "suite", "email")
    return 200, view


@aira_route("GET", f"{BASE}/outbox")
def outbox_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, {
            "messages": kit.outbox(
                connection, status=request.q("status"), app=request.q("app")
            ),
            "counts": kit.outbox_counts(connection),
        }


@aira_route("POST", f"{BASE}/outbox/approve")
def outbox_approve(request: Request) -> tuple[int, dict[str, Any]]:
    ids = [str(item) for item in (request.payload.get("ids") or [])]
    with request.connect() as connection:
        changed = kit.set_outbox_status(connection, ids, "approved")
        return 200, {"approved": changed, "counts": kit.outbox_counts(connection)}


@aira_route("POST", f"{BASE}/outbox/cancel")
def outbox_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    ids = [str(item) for item in (request.payload.get("ids") or [])]
    with request.connect() as connection:
        changed = kit.set_outbox_status(connection, ids, "cancelled")
        return 200, {"cancelled": changed, "counts": kit.outbox_counts(connection)}


@aira_route("PATCH", f"{BASE}/outbox/([0-9a-f]{{32}})")
def outbox_edit(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, {
            "message": kit.edit_outbox(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/outbox/send")
def outbox_send(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        password = request.secret_store.get(connection, kit.SMTP_PASSWORD_SECRET) or ""
        result = kit.send_outbox(connection, password)
        result["counts"] = kit.outbox_counts(connection)
    request.audit(
        "suite.outbox_send",
        "suite",
        "outbox",
        {"sent": result["sent"], "failed": result["failed"]},
    )
    return 200, result


# ---------------------------------------------------------------------------
# public links (no sign-in: the token names the account and the page)
# ---------------------------------------------------------------------------


@aira_route("GET", f"/api/public/{TOKEN}", auth="public_link")
def public_view(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, public.view(connection, request.match.group(1))


@aira_route("GET", f"/api/public/{TOKEN}/([a-z_]{{2,40}})", auth="public_link")
def public_query(request: Request) -> tuple[int, dict[str, Any]]:
    # A visitor's secret (a chat's) comes in a header so it stays out of addresses and logs.
    params: dict[str, Any] = {
        name: values[-1] for name, values in request.query.items() if values
    }
    params.pop("secret", None)
    secret = request.header("Aira-Secret")
    if secret:
        params["secret"] = secret[:200]
    with request.connect() as connection:
        return public.ask(
            connection, request.match.group(1), request.match.group(2), params
        )


@aira_route("POST", f"/api/public/{TOKEN}/([a-z_]{{2,40}})", auth="public_link")
def public_action(request: Request) -> tuple[int, dict[str, Any]]:
    visitor = {
        "client": request.client,
        "user_agent": request.header("User-Agent")[:200],
        "base_url": request.base_url(),  # for links in emails: see kit.public_url
    }
    with request.connect() as connection:
        return public.act(
            connection,
            request.match.group(1),
            request.match.group(2),
            request.payload,
            visitor,
        )


def _actor(request: Request) -> str:
    user = request.user or {}
    return str(user.get("email") or "")
