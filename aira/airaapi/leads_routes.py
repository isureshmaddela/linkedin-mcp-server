"""Route handlers for Aira Leads (``/api/leads/*``).

Thin glue over :mod:`aira.leads` (storage, sources, scoring, enrichment, drafts). Every handler runs as the
signed-in user and opens that user's database. Google searches use the account's SerpApi key through the same
resolution as the API console (so every search is in API > History and shares its cache); the key is
never part of a response. LinkedIn Sales Navigator is read through the person's own LinkedIn connection.
Loaded by :func:`aira.airaapi.http.load_route_modules`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi import routes as console
from aira.airaapi import store as airastore
from aira.airaapi.http import Raw, Request, aira_route
from aira.leads import calls, intent, mail, scoring, service, store
from aira.suite.marketing import access

BASE = "/api/leads"
ID = "([0-9a-f]{32})"


def _connect(request: Request) -> Any:
    """The account's database, refused (403) to a child or teen household profile (customers, a paid search key)."""
    connection = request.connect()
    access.require_grown_up(request, connection)
    return connection


def _flag(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _list_id(request: Request, connection: Any, *, create: bool) -> str:
    """The list the request names, or the first list (created on first use)."""
    named = str(request.payload.get("list_id") or request.q("list_id") or "")
    if named:
        return str(store.get_list(connection, named)["id"])
    return str(store.default_list(connection)["id"]) if create else ""


# ---------------------------------------------------------------------------
# overview, ICP
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        resolved = console._resolved(request, connection)
        data = service.overview(
            connection, serpapi_configured=resolved.configured, key=resolved.public()
        )
    data["key_error"] = resolved.error
    return 200, data


@aira_route("GET", f"{BASE}/icp")
def icp_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        icp = scoring.get_icp(connection)
    return 200, {"icp": icp, "empty": scoring.is_empty(icp)}


@aira_route("POST", f"{BASE}/icp")
def icp_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        outcome = service.save_icp(connection, request.payload)
    request.audit("leads.icp", "leads", "", {"rescored": outcome["rescored"]})
    return 200, outcome


# ---------------------------------------------------------------------------
# lists
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/lists")
def lists_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"lists": store.list_lists(connection)}


@aira_route("POST", f"{BASE}/lists")
def lists_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        created = store.create_list(
            connection,
            str(request.payload.get("name") or ""),
            str(request.payload.get("description") or ""),
        )
    request.audit("leads.list.create", "lead_list", created["id"], {})
    return 201, {"list": created}


@aira_route("PATCH", f"{BASE}/lists/{ID}")
def lists_update(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _connect(request) as connection:
        updated = store.rename_list(
            connection,
            request.match.group(1),
            payload.get("name") if "name" in payload else None,
            payload.get("description") if "description" in payload else None,
        )
    return 200, {"list": updated}


@aira_route("DELETE", f"{BASE}/lists/{ID}")
def lists_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        removed = store.delete_list(connection, request.match.group(1))
    request.audit(
        "leads.list.delete", "lead_list", request.match.group(1), {"leads": removed}
    )
    return 200, {"deleted": True, "leads_removed": removed}


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    source = str(request.payload.get("source") or "")
    with _connect(request) as connection:
        client = (
            console._client(request, connection, required=False)
            if source in service.SERP_SOURCES
            else None
        )
        outcome = service.search(
            connection,
            client,
            request.payload,
            limits=console._limits(request),
            now=airastore.now_utc(),
        )
        if outcome.get("dry_run") and source in service.SERP_SOURCES:
            outcome["configured"] = console._resolved(request, connection).configured
    if not outcome.get("dry_run"):
        request.audit(
            "leads.search",
            "lead_search",
            str(outcome.get("run_id", "")),
            {
                "source": source,
                "found": outcome["count"],
                "added": outcome["added"],
                "credits": outcome["credits"],
            },
        )
    return 200, outcome


@aira_route("GET", f"{BASE}/searches")
def searches_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"searches": store.list_searches(connection)}


@aira_route("POST", f"{BASE}/searches")
def searches_create(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    params = payload.get("params")
    if not isinstance(params, dict):
        raise ValueError("Send the search form as params.")
    with _connect(request) as connection:
        created = store.create_search(
            connection,
            str(payload.get("name") or ""),
            str(payload.get("source") or ""),
            params,
            str(payload.get("list_id") or ""),
        )
    return 201, {"search": created}


@aira_route("DELETE", f"{BASE}/searches/{ID}")
def searches_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.delete_search(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/searches/{ID}/run")
def searches_run(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        saved = store.get_search(connection, request.match.group(1))
        payload = {
            **saved["params"],
            "source": saved["source"],
            "search_id": saved["id"],
            "list_id": saved["list_id"],
            "auto_add": request.payload.get("auto_add", True),
        }
        client = (
            console._client(request, connection, required=False)
            if saved["source"] in service.SERP_SOURCES
            else None
        )
        outcome = service.search(
            connection,
            client,
            payload,
            limits=console._limits(request),
            now=airastore.now_utc(),
        )
    request.audit(
        "leads.search",
        "lead_search",
        saved["id"],
        {
            "source": saved["source"],
            "found": outcome["count"],
            "added": outcome["added"],
            "credits": outcome["credits"],
        },
    )
    return 200, outcome


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/items")
def items_list(request: Request) -> tuple[int, dict[str, Any]]:
    min_score = request.q("min_score")
    with _connect(request) as connection:
        return 200, store.query_leads(
            connection,
            list_id=request.q("list_id"),
            status=request.q("status"),
            source=request.q("source"),
            kind=request.q("kind"),
            text=request.q("q"),
            min_score=request.qint("min_score", 0, 0, 100) if min_score else None,
            has_email=request.qbool("has_email"),
            tag=request.q("tag"),
            sort=request.q("sort", "updated"),
            limit=request.qint("limit", 50, 1, 500),
            offset=request.qint("offset", 0, 0, 1_000_000),
        )


@aira_route("POST", f"{BASE}/items")
def items_save(request: Request) -> tuple[int, dict[str, Any]]:
    leads = request.payload.get("leads")
    if not isinstance(leads, list) or not leads:
        raise ValueError("Choose at least one lead to save.")
    with _connect(request) as connection:
        list_id = _list_id(request, connection, create=True)
        outcome = service.save_leads(
            connection, [lead for lead in leads if isinstance(lead, dict)], list_id
        )
    request.audit(
        "leads.save",
        "lead_list",
        list_id,
        {"created": outcome["created"], "duplicates": outcome["duplicates"]},
    )
    outcome["list_id"] = list_id
    return 201, outcome


@aira_route("POST", f"{BASE}/items/import-csv")
def items_import_csv(request: Request) -> tuple[int, dict[str, Any]]:
    text = request.payload.get("csv")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Send the CSV text as csv.")
    with _connect(request) as connection:
        list_id = _list_id(request, connection, create=True)
        outcome = service.import_csv(connection, text, list_id)
    request.audit(
        "leads.import",
        "lead_list",
        list_id,
        {"created": outcome["created"], "rows": outcome["rows"]},
    )
    outcome["list_id"] = list_id
    return 201, outcome


@aira_route("POST", f"{BASE}/items/bulk")
def items_bulk(request: Request) -> tuple[int, dict[str, Any]]:
    ids = request.payload.get("ids")
    if not isinstance(ids, list):
        raise ValueError("Send the lead ids as ids.")
    with _connect(request) as connection:
        outcome = store.bulk_update(
            connection,
            [str(i) for i in ids],
            str(request.payload.get("action") or ""),
            request.payload.get("value", ""),
        )
    request.audit(
        "leads.bulk",
        "leads",
        "",
        {"action": str(request.payload.get("action")), "changed": outcome["changed"]},
    )
    return 200, outcome


@aira_route("GET", f"{BASE}/items/{ID}")
def item_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"lead": store.get_lead(connection, request.match.group(1))}


@aira_route("PATCH", f"{BASE}/items/{ID}")
def item_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        updated = store.update_lead(connection, request.match.group(1), request.payload)
        score, reasons = scoring.score_lead(updated, scoring.get_icp(connection))
        store.save_scoring(connection, updated["id"], score, reasons)
        connection.commit()
        updated = store.get_lead(connection, updated["id"])
    return 200, {"lead": updated}


@aira_route("DELETE", f"{BASE}/items/{ID}")
def item_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.get_lead(connection, request.match.group(1))
        store.delete_leads(connection, [request.match.group(1)])
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/items/{ID}/enrich")
def item_enrich(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        lead = service.enrich_lead(connection, request.match.group(1))
    request.audit(
        "leads.enrich",
        "lead",
        lead["id"],
        {"emails": len(lead["enrichment"].get("emails", []))},
    )
    return 200, {"lead": lead}


@aira_route("POST", f"{BASE}/items/{ID}/signals")
def item_signals(request: Request) -> tuple[int, dict[str, Any]]:
    """Recent news about the lead's company, kept as buying signals (1 search)."""
    with _connect(request) as connection:
        client = console._client(request, connection, required=False)
        outcome = service.news_signals(
            connection,
            client,
            request.match.group(1),
            request.payload,
            limits=console._limits(request),
            now=airastore.now_utc(),
        )
    if not outcome.get("dry_run"):
        request.audit(
            "leads.signals",
            "lead",
            request.match.group(1),
            {"found": outcome["found"], "credits": outcome["credits"]},
        )
    return 200, outcome


@aira_route("POST", f"{BASE}/items/{ID}/draft")
def item_draft(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "draft": service.draft_message(
                connection, request.match.group(1), request.payload
            )
        }


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/export")
def export(request: Request) -> Raw:
    min_score = request.q("min_score")
    with _connect(request) as connection:
        body, content_type, filename = service.export(
            connection,
            fmt=request.q("format", "csv").lower(),
            list_id=request.q("list_id"),
            status=request.q("status"),
            source=request.q("source"),
            text=request.q("q"),
            min_score=request.qint("min_score", 0, 0, 100) if min_score else None,
            sort="updated",
        )
    request.audit("leads.export", "leads", "", {"format": request.q("format", "csv")})
    return Raw(200, body, content_type, filename)


# ---------------------------------------------------------------------------
# service presets, outreach settings, the outbox, the do-not-contact list
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/services")
def services_get(request: Request) -> tuple[int, dict[str, Any]]:
    """The services a lead can need (website, marketing, content, ads, social), with the phrases and roles searched."""
    return 200, {"services": intent.catalog()}


def _smtp_password(request: Request, connection: Any) -> str:
    return request.secret_store.get(connection, mail.PASSWORD_SECRET) or ""


@aira_route("GET", f"{BASE}/outreach/settings")
def outreach_settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        settings = mail.get_settings(connection)
        has_password = bool(_smtp_password(request, connection))
        return 200, {
            "settings": settings,
            "has_password": has_password,
            "problems": mail.settings_problems(settings, has_password),
            "counts": mail.counts(connection),
            "limits": {
                "max_daily_cap": mail.MAX_DAILY_CAP,
                "min_delay": mail.MIN_DELAY,
                "batch": mail.SEND_BATCH,
            },
        }


@aira_route("POST", f"{BASE}/outreach/settings")
def outreach_settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _connect(request) as connection:
        settings = mail.save_settings(connection, payload)
        password = payload.get("smtp_password")
        if isinstance(password, str) and password:
            request.secret_store.put(
                connection, mail.PASSWORD_SECRET, password, airastore.now_utc()
            )
        elif _flag(payload.get("clear_password"), "clear_password"):
            request.secret_store.delete(connection, mail.PASSWORD_SECRET)
        connection.commit()
        has_password = bool(_smtp_password(request, connection))
    request.audit(
        "leads.outreach.settings",
        "leads",
        "",
        {"host": settings["smtp_host"], "cap": settings["daily_cap"]},
    )
    return 200, {
        "settings": settings,
        "has_password": has_password,
        "problems": mail.settings_problems(settings, has_password),
    }


@aira_route("POST", f"{BASE}/outreach/test")
def outreach_test(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        settings = mail.get_settings(connection)
        password = _smtp_password(request, connection)
    try:
        return 200, mail.send_test(settings, password)
    except mail.SendError as error:
        raise RuntimeError(str(error)) from None


@aira_route("GET", f"{BASE}/outbox")
def outbox_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "messages": mail.list_messages(
                connection,
                status=request.q("status"),
                lead_id=request.q("lead_id"),
                limit=request.qint("limit", 100, 1, 500),
            ),
            "counts": mail.counts(connection),
        }


@aira_route("POST", f"{BASE}/outbox/drafts")
def outbox_drafts(request: Request) -> tuple[int, dict[str, Any]]:
    ids = request.payload.get("lead_ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError("Choose at least one lead.")
    with _connect(request) as connection:
        outcome = service.draft_emails(
            connection, [str(i) for i in ids], request.payload
        )
    request.audit(
        "leads.outbox.drafts",
        "leads",
        "",
        {"created": outcome["created"], "skipped": len(outcome["skipped"])},
    )
    return 201, outcome


@aira_route("PATCH", f"{BASE}/outbox/{ID}")
def outbox_update(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _connect(request) as connection:
        message = mail.update_message(
            connection,
            request.match.group(1),
            payload.get("subject") if "subject" in payload else None,
            payload.get("body") if "body" in payload else None,
        )
    return 200, {"message": message}


@aira_route("DELETE", f"{BASE}/outbox/{ID}")
def outbox_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        mail.delete_message(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/outbox/approve")
def outbox_approve(request: Request) -> tuple[int, dict[str, Any]]:
    ids = request.payload.get("ids")
    if not isinstance(ids, list):
        raise ValueError("Send the message ids as ids.")
    with _connect(request) as connection:
        outcome = mail.approve(connection, [str(i) for i in ids])
    request.audit("leads.outbox.approve", "leads", "", outcome)
    return 200, outcome


@aira_route("POST", f"{BASE}/outbox/reject")
def outbox_reject(request: Request) -> tuple[int, dict[str, Any]]:
    ids = request.payload.get("ids")
    if not isinstance(ids, list):
        raise ValueError("Send the message ids as ids.")
    with _connect(request) as connection:
        return 200, mail.reject(connection, [str(i) for i in ids])


@aira_route("POST", f"{BASE}/outbox/send")
def outbox_send(request: Request) -> tuple[int, dict[str, Any]]:
    """Send up to ten approved messages (one at a time, with a pause), within the daily cap."""
    with _connect(request) as connection:
        settings = mail.get_settings(connection)
        password = _smtp_password(request, connection)
        outcome = mail.send_approved(
            connection,
            settings,
            password,
            limit=request.qint("limit", mail.SEND_BATCH, 1, mail.SEND_BATCH),
        )
    request.audit(
        "leads.outbox.send",
        "leads",
        "",
        {
            "sent": outcome["sent"],
            "failed": outcome["failed"],
            "skipped": len(outcome["skipped"]),
        },
    )
    return 200, outcome


@aira_route("GET", f"{BASE}/suppressions")
def suppressions_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"suppressions": mail.list_suppressions(connection)}


@aira_route("POST", f"{BASE}/suppressions")
def suppressions_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        entry = mail.add_suppression(
            connection,
            str(request.payload.get("value") or ""),
            str(request.payload.get("reason") or ""),
        )
    return 201, {"suppression": entry}


@aira_route("POST", f"{BASE}/suppressions/remove")
def suppressions_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        mail.remove_suppression(connection, str(request.payload.get("key") or ""))
    return 200, {"removed": True}


# ---------------------------------------------------------------------------
# call assist (the person dials; Aira prepares the call and keeps the record)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/items/{ID}/dial")
def item_dial(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        lead = store.get_lead(connection, request.match.group(1))
        return 200, {
            "dial": calls.dial_info(connection, lead, mail.get_settings(connection))
        }


@aira_route("POST", f"{BASE}/items/{ID}/call-script")
def item_call_script(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "script": service.make_call_script(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("GET", f"{BASE}/items/{ID}/calls")
def item_calls(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"calls": calls.list_calls(connection, request.match.group(1))}


@aira_route("POST", f"{BASE}/items/{ID}/calls")
def item_call_log(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    days = payload.get("follow_up_days")
    with _connect(request) as connection:
        call = calls.log_call(
            connection,
            request.match.group(1),
            outcome=str(payload.get("outcome") or ""),
            notes=str(payload.get("notes") or ""),
            follow_up_days=None if days in (None, "") else int(days),
        )
        lead = store.get_lead(connection, request.match.group(1))
    request.audit(
        "leads.call", "lead", request.match.group(1), {"outcome": call["outcome"]}
    )
    return 201, {"call": call, "lead": lead}


@aira_route("GET", f"{BASE}/follow-ups")
def follow_ups(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"follow_ups": calls.follow_ups_due(connection)}
