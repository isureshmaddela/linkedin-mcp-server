"""What the gateway adds to specific kinds of route: limits, metering, source checks and sensitive-field rules.

Each hook has up to three stages: ``pre`` (before the tenant is bound), ``inside`` (bound, before the
handler, so the workspace can be read) and ``post`` (after, with the response or the failure).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from aira_cloud import pooled, redaction
from aira_cloud.errors import (
    Forbidden,
    PlanRequired,
    SubscriptionInactive,
    ValidationFailed,
)
from aira_cloud.policy import LINKEDIN_NOTICE
from aira_cloud.rbac import CANDIDATE

if TYPE_CHECKING:
    from aira_cloud.gateway import Ctx
    from aira_cloud.webio import HttpResponse


@dataclass
class Hook:
    pre: Callable[[Ctx], None] | None = None
    inside: Callable[[Ctx], None] | None = None
    post: Callable[[Ctx, Any, BaseException | None], None] | None = None


def _ok(response: HttpResponse | None, failure: BaseException | None) -> bool:
    return failure is None and response is not None and response.status < 400


# --------------------------------------------------------------------------- LinkedIn


def linkedin_pre(ctx: Ctx) -> None:
    app = ctx.app
    if not app.tenancy.policy(ctx.org_id).get("linkedin_allowed"):
        raise Forbidden(
            "LinkedIn is off in your organization's policy.", code="linkedin_off"
        )
    if ctx.payload.get("acknowledge") is not True:
        raise ValidationFailed(LINKEDIN_NOTICE, code="acknowledgement_required")
    block = app.gate.source_block(
        ctx.org_id, "linkedin", ""
    )  # the candidate's own opt-in is recorded by this very call
    if block and "turned LinkedIn on" not in block:
        raise Forbidden(block[0].upper() + block[1:], code="linkedin_off")


def linkedin_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    if not _ok(response, failure):
        return
    app = ctx.app
    now = app.clock.now()
    _add_remote_login(ctx, response)
    with app.db.write() as connection:
        row = connection.execute(
            "SELECT linkedin_opt_in_at FROM workspaces WHERE candidate_id = ?",
            (ctx.candidate_id,),
        ).fetchone()
        if row and not row["linkedin_opt_in_at"]:
            connection.execute(
                "UPDATE workspaces SET linkedin_opt_in_at = ? WHERE candidate_id = ?",
                (now, ctx.candidate_id),
            )
            app.audit.record(
                ctx.org_id,
                "linkedin.opt_in",
                actor=ctx.principal,
                candidate_id=ctx.candidate_id,
                detail={"notice": "user agreement warning shown"},
                connection=connection,
            )


def _add_remote_login(ctx: Ctx, response: HttpResponse | None) -> None:
    """When a sign-in window opens on a worker host, give the candidate a short-lived link to reach it remotely."""
    config = ctx.app.config
    if (
        response is None
        or not config.remote_login_url
        or ctx.principal.member_id != ctx.candidate_id
    ):
        return
    try:
        body = json.loads(response.body)
    except ValueError:
        return
    if body.get("state") != "login":
        return
    token = ctx.app.linkedin.create_remote_login(ctx.org_id, ctx.candidate_id)
    body["remote_login"] = {
        "url": config.remote_login_url.replace("{token}", token),
        "expires_in": config.remote_login_ttl,
    }
    response.body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    response.headers[:] = [
        (k, v) for k, v in response.headers if k.lower() != "content-length"
    ]


# --------------------------------------------------------------------------- search and drafts


def _source_of(ctx: Ctx) -> str:
    return (
        str(
            ctx.rule.source
            if ctx.rule.source != "payload"
            else ctx.payload.get("source", "linkedin")
        )
        .strip()
        .lower()
    )


def source_check_pre(ctx: Ctx) -> None:
    ctx.gateway.check_source(
        ctx.org_id, ctx.candidate_id, _source_of(ctx), ctx.audience
    )


def search_pre(ctx: Ctx) -> None:
    source_check_pre(ctx)
    if _source_of(ctx) == "google_jobs":  # the only source that spends SerpApi credits
        pooled.begin()
        ctx.state["pooled"] = pooled.reserve(
            ctx.app,
            ctx.org_id,
            candidate_id=ctx.candidate_id,
            audience=ctx.audience,
        )


def _search_cost(ctx: Ctx, response: HttpResponse | None) -> int:
    """Searches to meter: one per ordinary search; for Careers reports and alert runs, the live credits they used."""
    if ctx.name not in {
        "airaapi__careers_routes__report_run",
        "airaapi__careers_routes__alert_run",
    }:
        return 1
    try:
        body = json.loads(response.body) if response is not None else {}
    except ValueError:
        return 1
    report = body.get("report") if isinstance(body.get("report"), dict) else body
    credits = report.get("credits") if isinstance(report, dict) else None
    return int(credits) if isinstance(credits, int | float) and credits >= 0 else 1


def search_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    app = ctx.app
    ok = _ok(response, failure)
    app.record_outcome(
        ctx.org_id,
        ctx.candidate_id,
        "search",
        source=_source_of(ctx),
        status="ok" if ok else "error",
    )
    cost = _search_cost(ctx, response)
    if ok:
        app.usage.record(ctx.org_id, "searches", cost, candidate_id=ctx.candidate_id)
    if ctx.state.get("pooled") is not None:
        collected = pooled.collect()
        # reports and alert runs know their own credits; a Jobs-tab search reports what it spent
        credits = (
            cost
            if ctx.name
            in {
                "airaapi__careers_routes__report_run",
                "airaapi__careers_routes__alert_run",
            }
            else collected
        )
        pooled.settle(
            app,
            ctx.org_id,
            ctx.state["pooled"],
            credits=credits if ok else None,
            ok=ok,
            candidate_id=ctx.candidate_id,
        )


def generate_pre(ctx: Ctx) -> None:
    app = ctx.app
    app.entitlements.require_feature(
        ctx.org_id, "ai.cover_letters", audience=ctx.audience, label="AI cover letters"
    )
    if not app.tenancy.policy(ctx.org_id).get("cover_letters"):
        raise Forbidden(
            "AI cover letters are turned off in your organization's policy.",
            code="policy",
        )
    ctx.state["reservation"] = app.enforcer.reserve(
        ctx.org_id,
        "cover_letters",
        monthly_limit_name="cover_letters_per_month",
        candidate_id=ctx.candidate_id,
        audience=ctx.audience,
    )


def generate_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    reservation = ctx.state.get("reservation")
    if reservation is not None and not _ok(response, failure):
        ctx.app.usage.release(reservation)


# --------------------------------------------------------------------------- storage


def upload_inside(ctx: Ctx) -> None:
    app = ctx.app
    size = len(str(ctx.payload.get("content_base64", ""))) * 3 // 4
    limit = app.entitlements.resolve(ctx.org_id).limit("storage_bytes")
    if limit is None:
        return
    used = app.storage.usage_bytes(ctx.org_id, refresh_candidate=ctx.candidate_id)
    if used + size > limit:
        mb = lambda n: f"{n / 1048576:.0f} MB"  # noqa: E731
        plan = app.entitlements.resolve(ctx.org_id).plan_id
        message = (
            f"The {plan.title()} plan stores {mb(limit)} and {mb(used)} is used."
            if ctx.audience == "staff"
            else f"Your organization's document storage ({mb(limit)}) is full."
        )
        raise PlanRequired(
            message,
            limit="storage_bytes",
            used=used,
            limit_value=limit,
            required_plan=None,
            current_plan=plan,
            audience=ctx.audience,
        )


def upload_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    if _ok(response, failure):
        ctx.app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)


# --------------------------------------------------------------------------- automation


def automation_pre(ctx: Ctx) -> None:
    block = ctx.app.gate.automation_block(ctx.org_id, ctx.candidate_id)
    if block:
        raise SubscriptionInactive(block[1], code_detail=block[0])


def _plan_sources(ctx: Ctx) -> list[str]:
    sources = [str(s) for s in ctx.payload.get("sources") or []]
    for board in ctx.payload.get("boards") or []:
        if isinstance(board, dict) and board.get("source"):
            sources.append(str(board["source"]))
    return sources


def _plan_checks(ctx: Ctx) -> None:
    for source in _plan_sources(ctx):
        ctx.gateway.check_source(ctx.org_id, ctx.candidate_id, source, ctx.audience)
    if str(ctx.payload.get("mode", "")) == "apply":
        # Automatic submission is the candidate's switch (or a coach with approve scope).
        ctx.app.authz.require(
            ctx.principal, "applications.submit", candidate_id=ctx.candidate_id
        )
    if ctx.payload.get("linkedin_easy_apply"):
        if ctx.principal.role != CANDIDATE:
            raise Forbidden(
                "Only the candidate can turn on LinkedIn Easy Apply.",
                code="candidate_only",
            )
        if not ctx.app.gate._easy_apply_allowed(ctx.org_id, ctx.candidate_id):
            raise Forbidden(
                "LinkedIn Easy Apply is off for your organization or you have not enabled it for yourself.",
                code="easy_apply_off",
            )


def plan_create_pre(ctx: Ctx) -> None:
    _plan_checks(ctx)


def plan_create_inside(ctx: Ctx) -> None:
    limit = ctx.app.entitlements.resolve(ctx.org_id).limit("search_plans_per_candidate")
    if limit is None:
        return
    local = ctx.gateway.local
    with local._connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM autopilot_plans").fetchone()[0]
    if count >= limit:
        entitlements = ctx.app.entitlements.resolve(ctx.org_id)
        from aira_cloud.plans import plan_for_limit

        needed = plan_for_limit(
            ctx.app.catalog.current(), "search_plans_per_candidate", limit + 1
        )
        message = (
            f"The {entitlements.plan_id.title()} plan allows {limit} search plans per candidate."
            + (
                f" Upgrade to {needed.title()} for more."
                if needed and needed != entitlements.plan_id
                else ""
            )
            if ctx.audience == "staff"
            else f"Your organization allows {limit} search plans per candidate."
        )
        raise PlanRequired(
            message,
            limit="search_plans_per_candidate",
            used=count,
            limit_value=limit,
            required_plan=needed,
            current_plan=entitlements.plan_id,
            audience=ctx.audience,
        )


def plan_update_pre(ctx: Ctx) -> None:
    _plan_checks(ctx)


def monitor_create_inside(ctx: Ctx) -> None:
    limit = ctx.app.entitlements.resolve(ctx.org_id).limit("monitors_per_candidate")
    if limit is None:
        return
    local = ctx.gateway.local
    with local._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM airaapi_monitors WHERE enabled = 1"
        ).fetchone()[0]
    if count >= limit:
        raise PlanRequired(
            f"The plan allows {limit} monitors and alerts per candidate.",
            limit="monitors_per_candidate",
            used=count,
            limit_value=limit,
            audience=ctx.audience,
        )


def auto_apply_pre(ctx: Ctx) -> None:
    """Applying for the candidate right now is a submission when the mode is 'apply'; the organization's rules decide."""
    mode = str(ctx.payload.get("mode", "apply"))
    if mode == "apply":
        ctx.app.authz.require(
            ctx.principal, "applications.submit", candidate_id=ctx.candidate_id
        )
        decision = ctx.app.gate.decide(ctx.org_id, ctx.candidate_id)
        if not decision.allowed:
            raise SubscriptionInactive(decision.message, code_detail=decision.code)
        if not decision.can_submit:
            ctx.payload["mode"] = (
                "review"  # fill the form and wait for a person to submit
            )
            ctx.headers.append(("X-Aira-Review-Reason", decision.review_reason))
    else:
        block = ctx.app.gate.automation_block(ctx.org_id, ctx.candidate_id)
        if block:
            raise SubscriptionInactive(block[1], code_detail=block[0])


# --------------------------------------------------------------------------- held questions


def _epoch(value: Any) -> int | None:
    from aira_cloud.workspace import _to_epoch

    return _to_epoch(value)


def run_answer_inside(ctx: Ctx) -> None:
    labels = redaction.answers_touch_sensitive(ctx.payload.get("answers"))
    if labels and ctx.principal.role != CANDIDATE:
        raise Forbidden(
            "A question asks for sensitive information (such as work authorization). Only the candidate can answer it.",
            code="sensitive_answer",
            labels=labels[:5],
        )
    run_id = ctx.match.group(1)
    local = ctx.gateway.local
    with local._connect() as connection:
        row = connection.execute(
            "SELECT finished_at, created_at FROM application_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    held_at = _epoch(row["finished_at"] or row["created_at"]) if row else None
    seconds = max(0, ctx.app.clock.now() - held_at) if held_at else None
    ctx.state["audit"] = {
        "run_id": run_id,
        "seconds_to_answer": seconds,
        "remember": bool(ctx.payload.get("remember")),
        "answers": len(ctx.payload.get("answers") or {}),
    }


def run_answer_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    if not _ok(response, failure):
        return
    ctx.app.audit.record(
        ctx.org_id,
        "held.answer",
        actor=ctx.principal,
        candidate_id=ctx.candidate_id,
        target_type="application_run",
        target_id=ctx.match.group(1),
        detail=ctx.state.get("audit", {}),
        source="api",
        correlation_id=ctx.request.headers.get("x-request-id", ""),
    )
    ctx.app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)


# --------------------------------------------------------------------------- sensitive fields


def profile_write_inside(ctx: Ctx) -> None:
    """Only the candidate changes sensitive profile fields; anyone else's save keeps the stored values."""
    if ctx.principal.role == CANDIDATE:
        return
    profile = ctx.payload.get("application_profile")
    if not isinstance(profile, dict):
        return
    local = ctx.gateway.local
    stored = local._profile_state()["candidate_profile"]
    for name in redaction.sensitive_field_names():
        if name in profile:
            provided = str(profile.get(name) or "").strip()
            if provided and provided != str(stored.get(name) or "").strip():
                raise Forbidden(
                    f"'{name.replace('_', ' ')}' is sensitive. Only the candidate enters and confirms it.",
                    code="sensitive_field",
                    field=name,
                )
            profile[name] = stored.get(
                name, ""
            )  # an empty value from a redacted view must not erase it


def answers_write_pre(ctx: Ctx) -> None:
    if ctx.principal.role == CANDIDATE:
        return
    field = str(
        ctx.payload.get("canonical_field")
        or (ctx.match.group(1) if ctx.match.groups() else "")
    )
    if (
        redaction.is_sensitive_key(field)
        or str(ctx.payload.get("sensitivity")) == "sensitive"
    ):
        raise Forbidden(
            f"'{field.replace('_', ' ')}' is a sensitive answer. Only the candidate enters and confirms it.",
            code="sensitive_answer",
            field=field,
        )


# --------------------------------------------------------------------------- the organization's API


def api_search_pre(ctx: Ctx) -> None:
    ctx.state["reservation"] = ctx.app.enforcer.reserve(
        ctx.org_id,
        "api_searches",
        monthly_limit_name="api_searches_per_month",
        audience=ctx.audience,
    )
    try:
        ctx.state["pooled"] = pooled.reserve(ctx.app, ctx.org_id, audience=ctx.audience)
    except BaseException:
        ctx.app.usage.release(ctx.state["reservation"])
        raise


def api_search_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    reservation = ctx.state.get("reservation")
    if reservation is None:
        return
    release = not _ok(response, failure)
    if not release and response is not None and response.body:
        try:
            parsed = json.loads(response.body)
        except ValueError:
            parsed = {}
        body: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        inner = body.get("record")
        record: dict[str, Any] = inner if isinstance(inner, dict) else body
        release = bool(record.get("cache_hit") or body.get("cache_hit"))
    if release:
        ctx.app.usage.release(reservation)
    pooled.settle(
        ctx.app,
        ctx.org_id,
        ctx.state.get("pooled"),
        credits=None if release else 1,
        ok=not release,
    )


def v1_key_create_inside(ctx: Ctx) -> None:
    limit = ctx.app.entitlements.resolve(ctx.org_id).limit("api_v1_keys")
    local = ctx.gateway.local
    with local._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM airaapi_api_keys WHERE revoked_at IS NULL"
        ).fetchone()[0]
    if limit is not None and count >= limit:
        raise PlanRequired(
            f"The plan allows {limit} API keys.",
            limit="api_v1_keys",
            used=count,
            limit_value=limit,
            audience=ctx.audience,
        )


# --------------------------------------------------------------------------- Aira Leads

LEADS_GOOGLE_SOURCES = {"google_maps", "google_web", "google_jobs", "google_intent"}
# Worst case a single search can add to a list when it saves what it finds (one page of results).
LEADS_PAGE_SIZE = {
    "google_maps": 20,
    "google_web": 10,
    "google_jobs": 10,
    "google_intent": 10,
    "sales_navigator": 125,
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _leads_remaining(ctx: Ctx, *, refresh: bool = True) -> int | None:
    """How many more leads the organization may store (None: no limit)."""
    limit = ctx.app.entitlements.resolve(ctx.org_id).limit("leads_stored")
    if limit is None:
        return None
    used = ctx.app.workspaces.leads_total(
        ctx.org_id, refresh_candidate=ctx.candidate_id if refresh else ""
    )
    return max(0, int(limit) - used)


def _leads_plan_required(ctx: Ctx, wanted: int, remaining: int) -> PlanRequired:
    from aira_cloud.plans import plan_for_limit

    entitlements = ctx.app.entitlements.resolve(ctx.org_id)
    limit = int(entitlements.limit("leads_stored") or 0)
    needed = plan_for_limit(
        ctx.app.entitlements.catalog.current(), "leads_stored", limit + 1
    )
    message = (
        f"The {entitlements.plan_id.title()} plan stores {limit:,} leads and {remaining:,} more fit; "
        f"this would add {wanted:,}."
        if ctx.audience == "staff"
        else f"Your organization stores up to {limit:,} leads and {remaining:,} more fit."
    )
    return PlanRequired(
        message,
        limit="leads_stored",
        used=limit - remaining,
        limit_value=limit,
        required_plan=needed if ctx.audience == "staff" else None,
        current_plan=entitlements.plan_id,
        audience=ctx.audience,
    )


def leads_capacity_pre(ctx: Ctx) -> None:
    """Saving leads by hand (or importing a CSV) is refused when it would not fit in the plan."""
    remaining = _leads_remaining(ctx)
    if remaining is None:
        return
    if ctx.name == "airaapi__leads_routes__items_import_csv":
        text = ctx.payload.get("csv")
        wanted = max(
            0, sum(1 for line in str(text or "").splitlines() if line.strip()) - 1
        )
    else:
        leads = ctx.payload.get("leads")
        wanted = len(leads) if isinstance(leads, list) else 0
    if wanted > remaining:
        raise _leads_plan_required(ctx, wanted, remaining)


def _leads_source_gate(ctx: Ctx, source: str, *, dry_run: bool) -> None:
    """The plan, the organization's policy, the kill switch and (for LinkedIn) the candidate's own opt-in."""
    source = str(source or "").strip().lower()
    ctx.state["leads_source"] = source
    if source == "sales_navigator":
        # Reading Sales Navigator is LinkedIn automation through the candidate's own session: exactly the
        # LinkedIn gates (plan feature, organization policy, opt-in, platform kill switch).
        ctx.gateway.check_source(ctx.org_id, ctx.candidate_id, "linkedin", ctx.audience)
    elif source in LEADS_GOOGLE_SOURCES:
        ctx.gateway.check_source(
            ctx.org_id, ctx.candidate_id, "google_jobs", ctx.audience
        )
        if not dry_run:
            pooled.begin()
            ctx.state["pooled"] = pooled.reserve(
                ctx.app,
                ctx.org_id,
                candidate_id=ctx.candidate_id,
                audience=ctx.audience,
            )
    if dry_run or source not in LEADS_PAGE_SIZE:
        return
    default = (
        ctx.name == "airaapi__leads_routes__searches_run"
    )  # a saved search saves by default
    wants_save = (
        _truthy(ctx.payload.get("auto_add", default))
        if "auto_add" in ctx.payload or default
        else False
    )
    if wants_save:
        remaining = _leads_remaining(ctx)
        if remaining is not None and remaining < LEADS_PAGE_SIZE[source]:
            # not enough room for a whole page: show the results and let the person choose what to keep
            ctx.payload["auto_add"] = False
            ctx.headers.append(
                (
                    "X-Aira-Leads-Auto-Add",
                    f"off: {remaining} lead(s) fit in this plan; choose which results to save",
                )
            )


def leads_search_pre(ctx: Ctx) -> None:
    if ctx.name == "airaapi__leads_routes__searches_run":
        return  # the saved search names its source; checked once the workspace is bound
    if ctx.name == "airaapi__leads_routes__item_signals":
        # company news is a Google search on the organization's SerpApi key like any other Google lead search
        _leads_source_gate(
            ctx, "google_web", dry_run=_truthy(ctx.payload.get("dry_run"))
        )
        return
    _leads_source_gate(
        ctx,
        str(ctx.payload.get("source") or ""),
        dry_run=_truthy(ctx.payload.get("dry_run")),
    )


def leads_search_inside(ctx: Ctx) -> None:
    if ctx.name != "airaapi__leads_routes__searches_run":
        return
    local = ctx.gateway.local
    with local._connect() as connection:
        row = connection.execute(
            "SELECT source FROM lead_searches WHERE id = ?", (ctx.match.group(1),)
        ).fetchone()
    if row is None:
        return  # the handler answers 404
    source = str(row["source"] if hasattr(row, "keys") else row[0])
    _leads_source_gate(ctx, source, dry_run=False)
    if source == "sales_navigator":
        ctx.state["remote_assign"] = (
            False  # read it on the host that holds the LinkedIn session
        )


def leads_search_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    app = ctx.app
    ok = _ok(response, failure)
    source = ctx.state.get("leads_source", "")
    body: dict[str, Any] = {}
    if response is not None:
        try:
            parsed = json.loads(response.body)
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            body = {}
    if body.get("dry_run"):
        if ctx.state.get("pooled") is not None:  # a plan reserves nothing; defensive
            pooled.collect()
        return
    effective = "linkedin" if source == "sales_navigator" else "google_jobs"
    if source:
        app.record_outcome(
            ctx.org_id,
            ctx.candidate_id,
            "search",
            source=effective,
            status="ok" if ok else "error",
        )
    if ok:
        app.usage.record(ctx.org_id, "searches", 1, candidate_id=ctx.candidate_id)
        app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)
    if ctx.state.get("pooled") is not None:
        pooled.collect()
        credits = body.get("credits")
        pooled.settle(
            app,
            ctx.org_id,
            ctx.state["pooled"],
            credits=int(credits) if ok and isinstance(credits, int | float) else None,
            ok=ok,
            candidate_id=ctx.candidate_id,
        )


def leads_enrich_pre(ctx: Ctx) -> None:
    """Reading a lead's website is metered and limited, and only ever runs against public addresses."""
    ctx.state["reservation"] = ctx.app.enforcer.reserve(
        ctx.org_id,
        "lead_enrichments",
        monthly_limit_name="lead_enrichments_per_month",
        candidate_id=ctx.candidate_id,
        audience=ctx.audience,
    )


def leads_enrich_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    reservation = ctx.state.get("reservation")
    read_a_page = False
    if _ok(response, failure) and response is not None:
        try:
            body = json.loads(response.body)
            pages = ((body.get("lead") or {}).get("enrichment") or {}).get("pages")
            read_a_page = bool(pages)
        except ValueError:
            read_a_page = False
    if reservation is not None and not read_a_page:
        # a failure, or a site that could not be read (not public, blocked by robots.txt, down): nothing is charged
        ctx.app.usage.release(reservation)
    if _ok(response, failure):
        ctx.app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)


def leads_changed_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    if _ok(response, failure):
        ctx.app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)


# ----------------------------------------------------------------------- Aira Leads: outbound email
# A hosted send request carries at most this many messages, with the pause below between them, so one request
# stays well inside a proxy's timeout: 5 messages x 20 s of SMTP timeout + 4 x 10 s pause is 140 s at the very worst
# (a healthy server answers in about a second, so a normal batch takes 5 to 25 seconds).
OUTREACH_BATCH = 5
OUTREACH_DELAY = (2, 10)


def _outreach_open(ctx: Ctx) -> None:
    """The kill switch and the organization-state gates (suspended, past due, read-only, paused, over limits)."""
    block = ctx.app.gate.outreach_block(ctx.org_id, ctx.candidate_id)
    if block is None:
        return
    kind, code, message = block
    if kind == "switch":
        raise Forbidden(message, code=code)
    raise SubscriptionInactive(message, code_detail=code)


def _response_body(response: HttpResponse | None) -> dict[str, Any]:
    if response is None:
        return {}
    try:
        parsed = json.loads(response.body)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clamp_sender(connection: Any, cap: int) -> None:
    """Keep the stored sender settings inside what the plan and a hosted request allow."""
    from aira.leads import mail

    settings = mail.get_settings(connection)
    changes: dict[str, Any] = {}
    if cap and int(settings["daily_cap"]) > cap:
        changes["daily_cap"] = cap
    low, high = OUTREACH_DELAY
    if not low <= int(settings["delay_seconds"]) <= high:
        changes["delay_seconds"] = max(low, min(int(settings["delay_seconds"]), high))
    if changes:
        mail.save_settings(connection, changes)


def leads_outreach_settings_pre(ctx: Ctx) -> None:
    """The plan caps the daily limit and a hosted request caps the pause, whatever the form sent."""
    from aira.leads import mail

    cap = int(
        ctx.app.entitlements.resolve(ctx.org_id).limit("lead_email_daily_cap") or 0
    )
    payload = ctx.payload
    host = str(payload.get("smtp_host") or "").strip()
    if host:  # a mail server that can never be used from here is refused now, not at the first send
        try:
            mail.check_smtp_host(host, int(payload.get("smtp_port") or 587))
        except (mail.SendError, TypeError, ValueError) as error:
            raise ValidationFailed(str(error)) from None
    if "daily_cap" in payload:
        try:
            wanted = int(payload["daily_cap"])
        except (TypeError, ValueError):
            return  # the handler answers 400
        payload["daily_cap"] = min(wanted, cap) if cap else wanted
    if "delay_seconds" in payload:
        try:
            delay = int(payload["delay_seconds"])
        except (TypeError, ValueError):
            return
        low, high = OUTREACH_DELAY
        payload["delay_seconds"] = max(low, min(delay, high))


def leads_outreach_settings_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    if not _ok(response, failure):
        return
    settings = _response_body(response).get("settings") or {}
    # who changed what: the mail server and the limits, never a password or any message text
    ctx.state["audit"] = {
        "smtp_host": str(settings.get("smtp_host") or ""),
        "daily_cap": settings.get("daily_cap"),
        "credentials_changed": bool(ctx.payload.get("smtp_password")),
    }


def leads_outreach_test_pre(ctx: Ctx) -> None:
    _outreach_open(ctx)


def leads_outreach_send_pre(ctx: Ctx) -> None:
    """Switch and state gates, then reserve this batch against the month's allowance (the plan's emails)."""
    _outreach_open(ctx)
    app = ctx.app
    entitlements = app.entitlements.resolve(ctx.org_id)
    limit = entitlements.limit("lead_emails_per_month")
    wanted = OUTREACH_BATCH
    try:
        asked = int((ctx.request.query.get("limit") or [wanted])[0])
        wanted = max(1, min(asked, OUTREACH_BATCH))
    except (TypeError, ValueError):
        pass
    remaining = app.usage.remaining_month(ctx.org_id, "lead_emails", limit)
    amount = wanted if remaining is None else min(wanted, remaining)
    try:
        if amount <= 0:
            from aira_cloud.entitlements import LimitReached

            raise LimitReached(
                "lead_emails",
                "month",
                app.usage.used(ctx.org_id, "lead_emails"),
                int(limit or 0),
            )
        ctx.state["email_reservation"] = app.usage.reserve(
            ctx.org_id,
            "lead_emails",
            amount,
            monthly_limit=limit,
            allow_overage=False,  # there is no price for extra emails: the allowance is a hard stop
        )
    except Exception as reached:  # noqa: BLE001
        from aira_cloud.entitlements import LimitReached

        if isinstance(reached, LimitReached):
            raise app.enforcer.plan_required(
                entitlements, reached, ctx.audience
            ) from None
        raise
    ctx.state["email_batch"] = amount
    ctx.request.query["limit"] = [
        str(amount)
    ]  # the handler sends no more than what was reserved


def leads_outreach_send_inside(ctx: Ctx) -> None:
    local = ctx.gateway.local
    cap = int(
        ctx.app.entitlements.resolve(ctx.org_id).limit("lead_email_daily_cap") or 0
    )
    with local._connect() as connection:
        _clamp_sender(connection, cap)


def leads_outreach_send_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    from aira_cloud.entitlements import Reservation

    reservation = ctx.state.get("email_reservation")
    if reservation is None:
        return
    sent = 0
    body = _response_body(response)
    if _ok(response, failure):
        sent = int(body.get("sent") or 0)
        ctx.state["audit"] = {
            "sent": sent,
            "failed": int(body.get("failed") or 0),
            "skipped": len(body.get("skipped") or []),
        }
    unused = int(ctx.state.get("email_batch", 0)) - sent
    if unused > 0:  # the allowance counts messages actually sent
        ctx.app.usage.release(
            Reservation(
                reservation.org_id,
                reservation.metric,
                unused,
                reservation.period,
                reservation.day,
                reservation.candidate_id,
            )
        )
    ctx.app.workspaces.invalidate_snapshot(ctx.org_id, ctx.candidate_id)


def leads_outreach_count_post(
    ctx: Ctx, response: HttpResponse | None, failure: BaseException | None
) -> None:
    """Audit how many drafts were written, approved or rejected (counts only, never the text)."""
    if not _ok(response, failure):
        return
    body = _response_body(response)
    counts = {
        key: body[key]
        for key in ("created", "approved", "rejected")
        if isinstance(body.get(key), int)
    }
    if "skipped" in body and isinstance(body["skipped"], list):
        counts["skipped"] = len(body["skipped"])
    ctx.state["audit"] = counts


HOOKS: dict[str, Hook] = {
    "linkedin_connect": Hook(pre=linkedin_pre, post=linkedin_post),
    "search": Hook(pre=search_pre, post=search_post),
    "generate": Hook(pre=generate_pre, post=generate_post),
    "upload": Hook(inside=upload_inside, post=upload_post),
    "automation": Hook(pre=automation_pre),
    "plan_create": Hook(pre=plan_create_pre, inside=plan_create_inside),
    "plan_update": Hook(pre=plan_update_pre),
    "monitor_create": Hook(pre=source_check_pre, inside=monitor_create_inside),
    "auto_apply": Hook(pre=auto_apply_pre),
    "run_answer": Hook(inside=run_answer_inside, post=run_answer_post),
    "profile_write": Hook(inside=profile_write_inside),
    "answers_write": Hook(pre=answers_write_pre),
    "api_search": Hook(pre=api_search_pre, post=api_search_post),
    "v1_key_create": Hook(inside=v1_key_create_inside),
    "leads_search": Hook(
        pre=leads_search_pre, inside=leads_search_inside, post=leads_search_post
    ),
    "leads_enrich": Hook(pre=leads_enrich_pre, post=leads_enrich_post),
    "leads_save": Hook(pre=leads_capacity_pre, post=leads_changed_post),
    "leads_changed": Hook(post=leads_changed_post),
    "leads_outreach_settings": Hook(
        pre=leads_outreach_settings_pre, post=leads_outreach_settings_post
    ),
    "leads_outreach_test": Hook(pre=leads_outreach_test_pre),
    "leads_outreach_send": Hook(
        pre=leads_outreach_send_pre,
        inside=leads_outreach_send_inside,
        post=leads_outreach_send_post,
    ),
    "leads_outreach_count": Hook(post=leads_outreach_count_post),
}

__all__ = ["HOOKS", "Hook", "re", "sqlite3", "datetime", "timezone"]
