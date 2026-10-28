"""Who may call each route of the local build, and what the plan and the organization's policy add.

Every handler method of the local ``RequestHandler`` is listed here; a route that is not listed is
refused (default deny), and a test fails if one is missing. The gateway reads this table before it
binds a tenant, so the permission, the subscription state and the entitlement checks happen before
any domain code runs.

Scopes:
  ``public``     no sign-in (static files, health)
  ``candidate``  one candidate's workspace; ``permission`` is checked against that candidate
  ``org_api``    the organization's API workspace (owners and admins)
  ``apikey``     the SerpApi-compatible API, authenticated by an organization API key
  ``operator``   the platform console's own session
  ``deny``       not available in the hosted product
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LINKEDIN_NOTICE = (
    "LinkedIn's User Agreement forbids automated access. Connecting LinkedIn lets Monosage search, read and (if you enable it) "
    "apply and reply through your own session, which can lead LinkedIn to restrict your account. "
    "Send acknowledge: true to confirm you understand."
)


@dataclass(frozen=True)
class Rule:
    scope: str
    permission: str = ""
    write: bool = False
    feature: str = ""
    kind: str = ""
    source: str = (
        ""  # a fixed job source, or "payload" to read it from the request body
    )
    linkedin: bool = False  # needs LinkedIn: plan feature, organization policy and the candidate's own opt-in
    audit: bool | None = None  # None: audit writes only


def read(permission: str = "candidates.read", **kw: Any) -> Rule:
    return Rule("candidate", permission, **kw)  # type: ignore[arg-type]


def write(permission: str = "candidates.write", **kw: Any) -> Rule:
    return Rule("candidate", permission, write=True, **kw)  # type: ignore[arg-type]


PUBLIC = Rule("public")
DENY = Rule("deny")
OPERATOR = Rule("operator", "platform.health.read")

# Routes under an app's path prefix that every organization may call whatever apps it has: the
# organization's own SerpApi key is an integration Careers needs, not part of the API app.
APP_FREE_ROUTES = frozenset(
    {
        "airaapi__routes__set_key",
        "airaapi__routes__remove_key",
        "airaapi__routes__account",
        "airaapi__routes__status",
    }
)


def _org(permission: str, **kw: Any) -> Rule:
    return Rule("org_api", permission, write=permission.endswith(".manage"), **kw)  # type: ignore[arg-type]


POLICY: dict[str, Rule] = {
    # -- static and health ---------------------------------------------------------------
    "ui_index": PUBLIC,
    "ui_app_page": PUBLIC,
    "ui_static": PUBLIC,
    "ui_i18n": PUBLIC,
    "ui_copyright": PUBLIC,  # the Videos copyright notice form (anyone, no account)
    "api_health": PUBLIC,
    "api_ready": PUBLIC,
    "api_metrics": OPERATOR,
    "api_apps": DENY,  # the control plane answers /api/apps with the organization's subscriptions
    # -- sign-in is the control plane's (these local routes are replaced) -----------------
    "api_auth_me": DENY,
    "api_auth_register": DENY,
    "api_auth_login": DENY,
    "api_auth_launch": DENY,  # the hosted build is the account; a launch opens a local installation, not this one
    "api_auth_account_link": DENY,
    "api_auth_logout": DENY,
    "api_auth_password_forgot": DENY,
    "api_auth_password_reset": DENY,
    "api_account_password": DENY,
    "api_settings_reset_mail": DENY,  # the hosted build sends its own account emails
    "api_settings_reset_mail_save": DENY,
    "api_settings_reset_mail_clear": DENY,
    "api_settings_reset_mail_test": DENY,
    # the local build's own model provider and Anthropic key; the hosted build has its own model (HOOKS.model_json)
    "airaapi__model_provider__provider_status": DENY,
    "airaapi__model_provider__provider_save": DENY,
    "airaapi__model_provider__provider_test": DENY,
    "airaapi__model_provider__provider_remove_key": DENY,
    # Workday accounts hold employer passwords; the hosted build applies through its own worker, not this store.
    "airaapi__workday_accounts__accounts_list": DENY,
    "airaapi__workday_accounts__accounts_add": DENY,
    "airaapi__workday_accounts__accounts_update": DENY,
    "airaapi__workday_accounts__accounts_delete": DENY,
    "airaapi__workday_accounts__accounts_settings": DENY,
    "api_account_delete": DENY,
    "api_account_export": read("export.own", kind="account_export", audit=True),
    # -- LinkedIn session (personal) ---------------------------------------------------------
    "api_linkedin_status": read(),
    "api_linkedin_connect": write(
        "linkedin.manage",
        kind="linkedin_connect",
        feature="sources.linkedin",
        audit=True,
    ),
    "api_linkedin_restart": write(
        "linkedin.manage", feature="sources.linkedin", linkedin=True
    ),
    # -- candidate reads ---------------------------------------------------------------------------
    "api_status": read(),
    "api_dashboard": read(),
    "api_sources": read(),
    "api_settings": read(),
    "api_audit": read(),
    "api_notifications": read(),
    "api_profile": read(kind="profile_read"),
    "api_answers": read(kind="profile_read"),
    "api_jobs": read(),
    "api_job_detail": read(),
    "api_job_events": read(),
    "api_applications_queue": read(),
    "api_applications_summary": read(),
    "api_job_document_versions": read("documents.read"),
    "api_job_document_version_text": read("documents.read", audit=True),
    "api_autopilot": read(kind="runs_read"),
    "api_autopilot_plan_passes": read(),
    "api_autopilot_runs": read(kind="runs_read"),
    "api_autopilot_run": read(kind="runs_read"),
    "api_job_preparation": read(kind="runs_read"),
    "api_interviews": read(),
    "api_interview": read(),
    "api_interview_ics": read(),
    "api_interviews_ics": read(),
    "api_recruiters": read(),
    "api_recruiter": read(),
    "api_tasks": read(),
    "api_email": read(),
    "api_analytics": read(),
    "api_automation": read(),
    "api_activity": read(),
    "api_documents": read("documents.read"),
    "api_document_download": read("documents.read", audit=True),
    "api_document_versions": read("documents.read"),
    # -- candidate edits --------------------------------------------------------------------------------
    "api_profile_save": write(kind="profile_write"),
    "api_resume_extract": write(),
    "api_answers_save": write(kind="answers_write"),
    "api_answers_delete": write(kind="answers_write"),
    "api_settings_save": write(kind="settings_save"),
    "api_notifications_read": write(),
    "api_job_save": write(),
    "api_job_archive": write(),
    "api_job_stage": write(),
    "api_job_documents": write(),
    "api_job_notes": write(),
    "api_job_documents_snapshot": write("documents.read"),
    "api_job_document_versions_save": write(),
    "api_interviews_create": write(),
    "api_interview_update": write(),
    "api_interview_delete": write(),
    "api_recruiters_create": write(),
    "api_recruiter_update": write(),
    "api_recruiter_delete": write(),
    "api_recruiter_interaction": write(),
    "api_interaction_delete": write(),
    "api_tasks_create": write(),
    "api_task_update": write(),
    "api_task_delete": write(),
    "api_documents_upload": write(kind="upload", audit=True),
    "api_document_delete": write(audit=True),
    "api_email_import": write(),
    "api_email_classify": write(),
    "api_email_apply": write(),
    "api_email_update": write(),
    "api_email_delete": write(),
    "api_autopilot_answer_delete": write(),
    # -- searches and drafts -------------------------------------------------------------------------------
    "api_jobs_search": write("search.run", kind="search", source="payload"),
    "api_jobs_import": write("search.run"),
    "api_jobs_rescore": write("search.run"),
    "api_search_linkedin": write("search.run", kind="search", source="linkedin"),
    "api_search_greenhouse": write("search.run", kind="search", source="greenhouse"),
    "api_search_google": write("search.run", kind="search", source="google_jobs"),
    "api_job_details": write("search.run", kind="search", source="linkedin"),
    "api_job_score": write("search.run"),
    "api_job_evaluate": write("search.run", kind="model"),
    "api_job_generate": write("search.run", kind="generate"),
    "api_job_prepare": write("search.run"),
    "api_preparation_review": write("search.run"),
    # -- auto-apply ---------------------------------------------------------------------------------------------
    "api_autopilot_settings": write("search.run"),
    "api_autopilot_plan_create": write("search.run", kind="plan_create"),
    "api_autopilot_plan_update": write("search.run", kind="plan_update"),
    "api_autopilot_plan_delete": write("search.run"),
    "api_autopilot_plan_run": write("search.run", kind="automation"),
    "api_autopilot_run_skip": write("search.run"),
    "api_autopilot_run_answer": write("held.answer", kind="run_answer", audit=True),
    "api_autopilot_run_submit": write(
        "applications.submit", kind="automation", audit=True
    ),
    "api_job_auto_apply": write("search.run", kind="auto_apply", audit=True),
    "api_preparation_approve": write("applications.submit", audit=True),
    "api_job_apply": write("applications.submit", audit=True),
    "api_job_submit": write("applications.submit", kind="submit_manual", audit=True),
    # -- LinkedIn inbox and schedules (personal session) -----------------------------------------------------------
    "api_inbox": read("messages.read", linkedin=True, feature="sources.linkedin"),
    "api_inbox_sync_status": read(
        "messages.read", linkedin=True, feature="sources.linkedin"
    ),
    "api_conversation": read(
        "messages.read", linkedin=True, feature="sources.linkedin"
    ),
    "api_message_policies": read(
        "messages.read", linkedin=True, feature="sources.linkedin"
    ),
    "api_auto_reply_status": read(
        "messages.read", linkedin=True, feature="sources.linkedin"
    ),
    "api_inbox_sync": write("messages.send", linkedin=True, feature="sources.linkedin"),
    "api_inbox_cache_clear": write(
        "messages.send", linkedin=True, feature="sources.linkedin"
    ),
    "api_auto_reply_run": write(
        "messages.send", linkedin=True, feature="sources.linkedin", kind="automation"
    ),
    "api_conversation_send": write(
        "messages.send", linkedin=True, feature="sources.linkedin", audit=True
    ),
    "api_conversation_policy": write(
        "messages.send", linkedin=True, feature="sources.linkedin", audit=True
    ),
    "api_conversation_recruiter": write(
        "messages.send", linkedin=True, feature="sources.linkedin"
    ),
    "api_conversation_draft": write(
        "messages.send", linkedin=True, feature="sources.linkedin", kind="model"
    ),
    "api_conversation_draft_save": write(
        "messages.send", linkedin=True, feature="sources.linkedin"
    ),
    "api_schedules_create": write(
        "linkedin.manage", linkedin=True, feature="sources.linkedin"
    ),
    "api_schedule_run": write(
        "linkedin.manage", linkedin=True, feature="sources.linkedin", kind="automation"
    ),
    "api_schedule_update": write(
        "linkedin.manage", linkedin=True, feature="sources.linkedin"
    ),
    "api_schedule_delete": write(
        "linkedin.manage", linkedin=True, feature="sources.linkedin"
    ),
    # -- the organization's API console (owner and admin) --------------------------------------------------------
    "airaapi__routes__overview": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__account": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__status": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__engines": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__engine_detail": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__searches": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__search_detail": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__search_export": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__search_xray": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__diff": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__locations": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__saved_list": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__monitors_list": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__keys_list": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__docs": _org("integrations.read", feature="api.console_read"),
    "airaapi__routes__platform_reference": _org(
        "integrations.read", feature="api.console_read"
    ),
    "airaapi__routes__set_key": _org(
        "integrations.manage", feature="sources.google_jobs", audit=True
    ),
    "airaapi__routes__remove_key": _org("integrations.manage", audit=True),
    "airaapi__routes__search": _org(
        "integrations.manage", feature="api.searches", kind="api_search"
    ),
    "airaapi__routes__delete_searches": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__search_update": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__search_delete": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__search_rerun": _org(
        "integrations.manage", feature="api.searches", kind="api_search"
    ),
    "airaapi__routes__saved_create": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__saved_update": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__saved_delete": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__saved_run": _org(
        "integrations.manage", feature="api.searches", kind="api_search"
    ),
    "airaapi__routes__monitors_create": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__monitors_update": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__monitors_delete": _org(
        "integrations.manage", feature="api.searches"
    ),
    "airaapi__routes__monitors_run": _org(
        "integrations.manage", feature="api.searches", kind="api_search"
    ),
    "airaapi__routes__keys_create": _org(
        "integrations.manage", feature="api.v1", kind="v1_key_create", audit=True
    ),
    "airaapi__routes__keys_revoke": _org(
        "integrations.manage", feature="api.v1", kind="v1_key_revoke", audit=True
    ),
    # -- the SerpApi-compatible API: an organization API key, not a session -------------------------------------
    "airaapi__routes__v1_search": Rule("apikey", feature="api.v1", kind="v1_search"),
    "airaapi__routes__v1_account": Rule("apikey", feature="api.v1"),
    "airaapi__routes__v1_locations": Rule("apikey", feature="api.v1"),
    "airaapi__routes__v1_searches": Rule("apikey", feature="api.v1"),
    "airaapi__routes__v1_search_detail": Rule("apikey", feature="api.v1"),
    "airaapi__routes__v1_engines": Rule("apikey", feature="api.v1"),
    # -- Careers (Google Jobs through the organization's SerpApi key) -------------------------------------------------
    "airaapi__careers_routes__overview": read(),
    "airaapi__careers_routes__record_jobs": read(),
    "airaapi__careers_routes__reports_list": read(),
    "airaapi__careers_routes__report_detail": read(),
    "airaapi__careers_routes__alerts_list": read(),
    "airaapi__careers_routes__alert_detail": read(),
    "airaapi__careers_routes__jobs_search": write(
        "search.run", kind="search", source="google_jobs"
    ),
    "airaapi__careers_routes__jobs_import": write("search.run"),
    "airaapi__careers_routes__report_run": write(
        "search.run", kind="search", source="google_jobs"
    ),
    "airaapi__careers_routes__report_delete": write("candidates.write"),
    "airaapi__careers_routes__alerts_create": write(
        "search.run", kind="monitor_create", source="google_jobs"
    ),
    "airaapi__careers_routes__alert_update": write("search.run"),
    "airaapi__careers_routes__alert_delete": write("search.run"),
    "airaapi__careers_routes__alert_run": write(
        "search.run", kind="search", source="google_jobs"
    ),
    "airaapi__careers_routes__alert_dismiss": write("search.run"),
    # -- Aira Leads (the candidate's own workspace; owners, admins, auditors and billing contacts do not read it) ----------
    # Leads is the business's shared pipeline (Marketing): organization-level, like the industry apps.
    "airaapi__leads_routes__overview": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__icp_get": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__lists_get": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__searches_list": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__items_list": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__item_get": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__export": _org("suite.read", feature="leads", audit=True),
    "airaapi__leads_routes__icp_save": _org(
        "suite.manage", feature="leads", kind="leads_changed"
    ),
    "airaapi__leads_routes__lists_create": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__lists_update": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__lists_delete": _org(
        "suite.manage", feature="leads", kind="leads_changed", audit=True
    ),
    "airaapi__leads_routes__search": _org(
        "suite.manage", feature="leads", kind="leads_search"
    ),
    "airaapi__leads_routes__searches_create": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__searches_delete": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__searches_run": _org(
        "suite.manage", feature="leads", kind="leads_search"
    ),
    "airaapi__leads_routes__items_save": _org(
        "suite.manage", feature="leads", kind="leads_save"
    ),
    "airaapi__leads_routes__items_import_csv": _org(
        "suite.manage", feature="leads", kind="leads_save", audit=True
    ),
    "airaapi__leads_routes__items_bulk": _org(
        "suite.manage", feature="leads", kind="leads_changed"
    ),
    "airaapi__leads_routes__item_update": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__item_delete": _org(
        "suite.manage", feature="leads", kind="leads_changed"
    ),
    "airaapi__leads_routes__item_enrich": _org(
        "suite.manage", feature="leads", kind="leads_enrich"
    ),
    "airaapi__leads_routes__item_draft": _org("suite.manage", feature="leads"),
    # -- Leads (part of Marketing): finding people who need services, email outreach and call assist --------
    # The business's shared pipeline, like a CRM: owners, admins and coaches (team members) work on it together,
    # auditors read it; the organization's database holds it. 402 until Marketing is on.
    "airaapi__leads_routes__services_get": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__outreach_settings_get": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__outreach_settings_save": _org(
        "suite.manage",
        feature="leads_outreach",
        kind="leads_outreach_settings",
        audit=True,
    ),
    "airaapi__leads_routes__outreach_test": _org(
        "suite.manage",
        feature="leads_outreach",
        kind="leads_outreach_test",
        audit=True,
    ),
    "airaapi__leads_routes__outbox_list": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__outbox_drafts": _org(
        "suite.manage", feature="leads", kind="leads_outreach_count", audit=True
    ),
    "airaapi__leads_routes__outbox_update": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__outbox_delete": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__outbox_approve": _org(
        "suite.manage",
        feature="leads_outreach",
        kind="leads_outreach_count",
        audit=True,
    ),
    "airaapi__leads_routes__outbox_reject": _org(
        "suite.manage", feature="leads", kind="leads_outreach_count"
    ),
    "airaapi__leads_routes__outbox_send": _org(
        "suite.manage",
        feature="leads_outreach",
        kind="leads_outreach_send",
        audit=True,
    ),
    "airaapi__leads_routes__suppressions_list": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__suppressions_add": _org(
        "suite.manage", feature="leads", audit=True
    ),
    "airaapi__leads_routes__suppressions_remove": _org(
        "suite.manage", feature="leads", audit=True
    ),
    "airaapi__leads_routes__item_dial": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__item_call_script": _org("suite.manage", feature="leads"),
    "airaapi__leads_routes__item_calls": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__item_call_log": _org(
        "suite.manage", feature="leads", audit=True
    ),
    "airaapi__leads_routes__follow_ups": _org("suite.read", feature="leads"),
    "airaapi__leads_routes__item_signals": _org(
        "suite.manage", feature="leads", kind="leads_search"
    ),
}


# Routes that need the candidate's LinkedIn browser session, which lives on one worker host. The web tier authorizes them,
# then the gateway forwards them to that host's agent. ``True`` means "start a session here if none exists" (connect, restart).
REMOTE_ROUTES: dict[str, bool] = {
    "api_linkedin_status": False,
    "api_linkedin_connect": True,
    "api_linkedin_restart": True,
    "api_search_linkedin": False,
    "api_job_details": False,
}


def remote_assignment(name: str, rule: "Rule", payload: dict) -> bool | None:
    """None: run here. False: run on the session's host if one holds it. True: pick a host if none does."""
    if name in REMOTE_ROUTES:
        return REMOTE_ROUTES[name]
    if rule.linkedin:
        return False
    if (
        name == "api_jobs_search"
        and str(payload.get("source", "linkedin")).strip().lower() == "linkedin"
    ):
        return False
    if (
        name == "airaapi__leads_routes__search"
        and str(payload.get("source", "")).strip().lower() == "sales_navigator"
    ):
        return False  # Sales Navigator is read in the candidate's LinkedIn browser, on the host that holds it
    return None


def unclassified(routes: list[tuple[str, object, str, bool]]) -> list[str]:
    """Route handler names that have no rule (the tests require this to be empty)."""
    return sorted(
        {name for _method, _pattern, name, _auth in routes if name not in POLICY}
    )


def suite_rules() -> dict[str, Rule]:
    """Rules for the industry apps and the Business hub (aira.suite), derived from their routes.

    Their data is the organization's (``org_api``): a member who may read it (``suite.read``) calls the
    GET routes, a member who may manage it (``suite.manage``) the others. The life apps and their core
    (``/api/life``) use ``life.read`` / ``life.manage``, which every member has. A route module can mark a
    non-GET route as a read with ``HOSTED_RULES = {"handler_name": "read"}``, or refuse it in the hosted build with
    ``"deny"`` (a computer owner's page that the platform console replaces). Public link routes answer a
    business's customers without a session (``public_link``: the token names the organization).
    """
    import sys

    from aira.airaapi import http as airaapi_http

    airaapi_http.load_route_modules()
    rules: dict[str, Rule] = {"ui_public": PUBLIC}
    for entry in airaapi_http.AIRA_ROUTES:
        module = entry.handler.__module__
        if not module.startswith("aira.suite."):
            continue
        if entry.auth == "public_link":
            rules[entry.attr] = Rule("public_link")
            continue
        if entry.auth == "none":
            rules[entry.attr] = PUBLIC
            continue
        override = getattr(sys.modules.get(module), "HOSTED_RULES", {}).get(
            getattr(entry.handler, "__name__", "")
        )
        if override == "deny":
            rules[entry.attr] = DENY
            continue
        reads = override == "read" or (override is None and entry.method == "GET")
        family = "life" if _is_life_module(module) else "suite"
        rules[entry.attr] = _org(f"{family}.read" if reads else f"{family}.manage")
    return rules


def _is_life_module(module: str) -> bool:
    """The life apps (everyday use by every member: ``life.*``) as opposed to the industry apps (``suite.*``)."""
    from aira import suite

    if module == "aira.suite.life_routes":
        return True
    return any(module.startswith(f"aira.suite.{name}.") for name in suite.LIFE_PACKAGES)


POLICY.update(suite_rules())
