"""Aira Leads orchestration: run a source search, score and de-duplicate what it finds, save, enrich, export."""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from aira.airaapi import service as airaservice
from aira.airaapi import store as airastore
from aira.airaapi import xlsx
from aira.leads import enrich, intent, outreach, scoring, sources, store

SOURCE_LABELS = {
    "google_maps": "Google Maps",
    "google_web": "Google web (LinkedIn pages)",
    "google_jobs": "Hiring signals (Google Jobs)",
    "google_intent": "People asking for a service (public posts)",
    "sales_navigator": "LinkedIn Sales Navigator",
    "import": "CSV import",
    "manual": "Added by hand",
}
SERP_SOURCES = {
    "google_maps": (sources.MAPS_ENGINE, sources.build_maps_params),
    "google_web": (sources.WEB_ENGINE, sources.build_web_params),
    "google_jobs": (sources.JOBS_ENGINE, sources.build_hiring_params),
    "google_intent": (sources.WEB_ENGINE, intent.build_intent_params),
}
SEARCH_SOURCE_TAG = "leads"
SALES_NAV_TOOL = "search_sales_navigator"
EXPORT_COLUMNS = (
    ("name", "Name"),
    ("title", "Title"),
    ("company", "Company"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("website", "Website"),
    ("linkedin_url", "LinkedIn"),
    ("location", "Location"),
    ("industry", "Industry"),
    ("status", "Status"),
    ("score", "Fit score"),
    ("source", "Source"),
    ("tags", "Tags"),
    ("notes", "Notes"),
    ("signals", "Signals"),
    ("created_at", "Added"),
)


class Gateway(Protocol):
    """What the server provides: the person's LinkedIn tools and the writing model."""

    def linkedin_state(self) -> str: ...

    def linkedin_call(self, name: str, arguments: dict[str, Any]) -> Any: ...

    def sender_name(self) -> str: ...

    def model(self, prompt: str) -> str | None: ...


_gateway: Gateway | None = None


def set_gateway(gateway: Gateway | None) -> None:
    global _gateway
    _gateway = gateway


def gateway() -> Gateway | None:
    return _gateway


# ---------------------------------------------------------------------------
# previews
# ---------------------------------------------------------------------------


def _preview_id(lead: Mapping[str, Any]) -> str:
    keys = store.dedupe_keys(lead) or [str(lead.get("name") or "")]
    return hashlib.sha1(keys[0].encode("utf-8")).hexdigest()[:16]


def annotate(
    connection: sqlite3.Connection, raw_leads: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Normalize, score against the ICP and mark the leads that are already saved."""
    icp = scoring.get_icp(connection)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_leads:
        try:
            lead = store.normalize_lead(raw)
        except ValueError:
            continue
        preview = _preview_id(lead)
        if preview in seen:
            continue
        seen.add(preview)
        score, reasons = scoring.score_lead(lead, icp)
        duplicate = store.find_duplicate(connection, lead)
        lead.update(
            {
                "preview_id": preview,
                "score": score,
                "score_reasons": reasons,
                "duplicate_of": (
                    {
                        "id": duplicate["id"],
                        "name": duplicate["name"],
                        "list_id": duplicate["list_id"],
                    }
                    if duplicate
                    else None
                ),
            }
        )
        out.append(lead)
    out.sort(key=lambda lead: -(lead["score"] if lead["score"] is not None else -1))
    return out


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------


def _cache_would_hit(
    connection: sqlite3.Connection,
    engine: str,
    params: Mapping[str, Any],
    limits: airaservice.Limits,
    moment: datetime,
) -> bool:
    from aira.airaapi import careers

    return careers._cache_would_hit(connection, engine, params, limits, moment)


def _next_start(
    source: str, params: Mapping[str, Any], result: Any, found: int
) -> int | None:
    if not isinstance(result, Mapping):
        return None
    start = int(params.get("start") or 0)
    if source == "google_maps":
        pagination = result.get("serpapi_pagination")
        more = isinstance(pagination, Mapping) and pagination.get("next")
        return start + 20 if more and found and start + 20 <= 100 else None
    if source in {"google_web", "google_intent"}:
        return start + 10 if found >= 8 and start + 10 <= 90 else None
    return None


def _next_token(result: Any) -> str:
    pagination = (
        result.get("serpapi_pagination") if isinstance(result, Mapping) else None
    )
    return (
        str((pagination or {}).get("next_page_token") or "")
        if isinstance(pagination, Mapping)
        else ""
    )


def search(
    connection: sqlite3.Connection,
    client: Any,
    payload: Mapping[str, Any],
    *,
    limits: airaservice.Limits | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one search on one source and return annotated leads. Nothing is saved unless ``auto_add`` is set."""
    moment = now or airastore.now_utc()
    limits = limits or airaservice.Limits()
    source = str(payload.get("source") or "").strip().lower()
    if source not in SERP_SOURCES and source != "sales_navigator":
        raise ValueError(
            "source must be google_maps, google_web, google_jobs or sales_navigator."
        )
    dry_run = bool(payload.get("dry_run")) and str(
        payload.get("dry_run")
    ).lower() not in {"0", "false", "no", "off"}
    auto_add = (
        str(payload.get("auto_add") or "").lower() in {"1", "true", "yes", "on"}
        or payload.get("auto_add") is True
    )
    list_id = str(payload.get("list_id") or "")
    if list_id:
        store.get_list(connection, list_id)
    search_id = str(payload.get("search_id") or "")

    if source == "sales_navigator":
        return _search_sales_navigator(
            connection,
            payload,
            dry_run=dry_run,
            auto_add=auto_add,
            list_id=list_id,
            search_id=search_id,
        )

    engine, build = SERP_SOURCES[source]
    params, applied, warnings = build(payload)
    if dry_run:
        hit = _cache_would_hit(connection, engine, params, limits, moment)
        return {
            "dry_run": True,
            "source": source,
            "engine": engine,
            "params": params,
            "applied": applied,
            "warnings": warnings,
            "credits": 0 if hit else 1,
            "cache_hit": hit,
        }
    if client is None:
        from aira.airaapi.client import SerpApiError

        raise SerpApiError(
            "not_configured",
            "Connect a SerpApi key in Aira API > Account to search Google for leads.",
        )
    record = airaservice.run_search(
        connection,
        client,
        engine,
        params,
        source=SEARCH_SOURCE_TAG,
        label=f"Leads: {params.get('q', '')}"[:200],
        now=moment,
        limits=limits,
    )
    result = record.get("result")
    skipped = 0
    if source == "google_maps":
        found = sources.normalize_maps(result)
    elif source == "google_web":
        found, skipped = sources.normalize_web(
            result, str(payload.get("mode") or "people")
        )
    elif source == "google_intent":
        found = intent.normalize_intent(result, intent._services(payload))
    else:
        found = sources.normalize_hiring(result)
    return _finish(
        connection,
        source=source,
        found=found,
        payload=payload,
        params=params,
        applied=applied,
        warnings=warnings,
        record=record,
        skipped=skipped,
        next_start=_next_start(source, params, result, len(found)),
        next_page_token=_next_token(result) if source == "google_jobs" else "",
        auto_add=auto_add,
        list_id=list_id,
        search_id=search_id,
    )


def _search_sales_navigator(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    dry_run: bool,
    auto_add: bool,
    list_id: str,
    search_id: str,
) -> dict[str, Any]:
    request = sources.build_sales_navigator_request(payload)
    plan = {
        "source": "sales_navigator",
        "engine": "linkedin_sales_navigator",
        "params": {k: v for k, v in request.items() if k != "url"}
        | ({"url": "(your search address)"} if "url" in request else {}),
        "applied": [f"up to {request['max_pages']} page(s) of about 25 people"],
        "warnings": [
            "Read from your own signed-in LinkedIn session, at a human pace, read only. LinkedIn restricts automated access: "
            "keep volumes low and use it on your own account."
        ],
        "credits": 0,
        "cache_hit": False,
    }
    if dry_run:
        return {"dry_run": True, **plan}
    bridge = gateway()
    if bridge is None:
        raise RuntimeError("LinkedIn is not available in this build.")
    state = bridge.linkedin_state()
    if state != "connected":
        raise PermissionError(
            "Connect your LinkedIn account in Settings > Integrations first (it needs Sales Navigator on that account)."
        )
    try:
        result = bridge.linkedin_call(SALES_NAV_TOOL, request)
    except Exception as error:  # noqa: BLE001 - the tool's own message says what LinkedIn did
        store.record_run(
            connection,
            source="sales_navigator",
            params=plan["params"],
            status="error",
            error=str(error),
            search_id=search_id,
        )
        raise RuntimeError(
            str(error)[:300] or "Sales Navigator could not be read."
        ) from error
    found = sources.normalize_sales_navigator(result)
    extra_warnings = (
        [str(w) for w in (result or {}).get("warnings", [])]
        if isinstance(result, Mapping)
        else []
    )
    return _finish(
        connection,
        source="sales_navigator",
        found=found,
        payload=payload,
        params=plan["params"],
        applied=plan["applied"],
        warnings=[*plan["warnings"], *extra_warnings],
        record={"id": "", "credits": 0, "cache_hit": False, "note": ""},
        skipped=0,
        next_start=None,
        next_page_token="",
        auto_add=auto_add,
        list_id=list_id,
        search_id=search_id,
        pages=int((result or {}).get("pages_read") or 0)
        if isinstance(result, Mapping)
        else 0,
    )


def _finish(
    connection: sqlite3.Connection,
    *,
    source: str,
    found: list[dict[str, Any]],
    payload: Mapping[str, Any],
    params: Mapping[str, Any],
    applied: list[str],
    warnings: list[str],
    record: Mapping[str, Any],
    skipped: int,
    next_start: int | None,
    next_page_token: str,
    auto_add: bool,
    list_id: str,
    search_id: str,
    pages: int = 0,
) -> dict[str, Any]:
    leads = annotate(connection, found)
    new = [lead for lead in leads if not lead["duplicate_of"]]
    added = 0
    target_list = ""
    if auto_add and new:
        target = (
            store.get_list(connection, list_id)
            if list_id
            else store.default_list(connection)
        )
        target_list = target["id"]
        added = save_leads(connection, new, target_list)["created"]
    run = store.record_run(
        connection,
        source=source,
        params=params,
        found=len(leads),
        added=added,
        duplicates=len(leads) - len(new),
        credits=int(record.get("credits") or 0),
        cache_hit=bool(record.get("cache_hit")),
        record_id=str(record.get("id") or ""),
        search_id=search_id,
    )
    return {
        "source": source,
        "leads": leads,
        "count": len(leads),
        "new_count": len(new),
        "duplicate_count": len(leads) - len(new),
        "skipped": skipped,
        "added": added,
        "list_id": target_list,
        "record_id": record.get("id") or "",
        "cache_hit": bool(record.get("cache_hit")),
        "credits": int(record.get("credits") or 0),
        "note": record.get("note") or "",
        "next_start": next_start,
        "next_page_token": next_page_token,
        "pages_read": pages,
        "params": dict(params),
        "applied": applied,
        "warnings": warnings,
        "run_id": run["id"],
    }


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------


def save_leads(
    connection: sqlite3.Connection,
    raw_leads: Sequence[Mapping[str, Any]],
    list_id: str,
) -> dict[str, Any]:
    """Add leads to a list, skipping ones already known. Returns counts and the stored leads."""
    store.get_list(connection, list_id)
    icp = scoring.get_icp(connection)
    created: list[dict[str, Any]] = []
    duplicates = 0
    failed: list[str] = []
    for raw in raw_leads[:2000]:
        try:
            lead = store.normalize_lead(raw)
            score, reasons = scoring.score_lead(lead, icp)
            outcome = store.insert_lead(
                connection, lead, list_id=list_id, score=score, score_reasons=reasons
            )
        except ValueError as error:
            failed.append(str(error))
            continue
        if outcome["created"]:
            created.append(outcome["lead"])
        else:
            duplicates += 1
    return {
        "created": len(created),
        "duplicates": duplicates,
        "failed": len(failed),
        "errors": failed[:5],
        "leads": created,
    }


def import_csv(
    connection: sqlite3.Connection, text: str, list_id: str
) -> dict[str, Any]:
    leads, skipped = sources.parse_csv(text)
    outcome = save_leads(connection, leads, list_id)
    outcome["skipped"] = skipped[:10]
    outcome["rows"] = len(leads)
    return outcome


def rescore(connection: sqlite3.Connection) -> int:
    """Score every saved lead against the current ICP."""
    icp = scoring.get_icp(connection)
    ids = [r[0] for r in connection.execute("SELECT id FROM leads")]
    for lead_id in ids:
        lead = store.get_lead(connection, lead_id)
        score, reasons = scoring.score_lead(lead, icp)
        store.save_scoring(connection, lead_id, score, reasons)
    connection.commit()
    return len(ids)


def save_icp(connection: sqlite3.Connection, raw: Mapping[str, Any]) -> dict[str, Any]:
    icp = scoring.save_icp(connection, raw)
    return {"icp": icp, "rescored": rescore(connection)}


# ---------------------------------------------------------------------------
# enrichment and drafts
# ---------------------------------------------------------------------------


def enrich_lead(
    connection: sqlite3.Connection,
    lead_id: str,
    *,
    fetcher: Callable[[str], dict[str, Any]] = enrich.enrich_website,
) -> dict[str, Any]:
    lead = store.get_lead(connection, lead_id)
    website = lead["website"]
    if not website:
        raise ValueError(
            "This lead has no website to read. Add the company's website first (Google Maps leads usually have one)."
        )
    try:
        enrichment = fetcher(website)
    except enrich.FetchError as error:
        raise RuntimeError(str(error)) from None
    updated = store.save_enrichment(
        connection,
        lead_id,
        enrichment,
        email=enrich.best_email(enrichment),
        phone=enrich.best_phone(enrichment),
    )
    audit = enrichment.get("audit") if isinstance(enrichment, Mapping) else None
    if isinstance(audit, Mapping) and audit.get("findings"):
        updated = store.add_signals(connection, lead_id, enrich.audit_signals(audit))
    score, reasons = scoring.score_lead(updated, scoring.get_icp(connection))
    store.save_scoring(connection, lead_id, score, reasons)
    connection.commit()
    return store.get_lead(connection, lead_id)


def news_signals(
    connection: sqlite3.Connection,
    client: Any,
    lead_id: str,
    payload: Mapping[str, Any],
    *,
    limits: airaservice.Limits | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Look up recent news about the lead's company and keep it as buying signals (1 search, 0 when cached)."""
    lead = store.get_lead(connection, lead_id)
    company = (
        lead["company"]
        if lead["kind"] == "person"
        else (lead["company"] or lead["name"])
    )
    params, applied = sources.build_news_params(
        company, str(payload.get("topic") or "")
    )
    moment = now or airastore.now_utc()
    limits = limits or airaservice.Limits()
    if str(payload.get("dry_run") or "").lower() in {"1", "true", "yes", "on"}:
        hit = _cache_would_hit(connection, sources.NEWS_ENGINE, params, limits, moment)
        return {
            "dry_run": True,
            "params": params,
            "applied": applied,
            "credits": 0 if hit else 1,
            "cache_hit": hit,
        }
    if client is None:
        from aira.airaapi.client import SerpApiError

        raise SerpApiError(
            "not_configured",
            "Connect a SerpApi key in Aira API > Account to look up news.",
        )
    record = airaservice.run_search(
        connection,
        client,
        sources.NEWS_ENGINE,
        params,
        source=SEARCH_SOURCE_TAG,
        label=f"Leads news: {params['q']}"[:200],
        now=moment,
        limits=limits,
    )
    found = sources.normalize_news(record.get("result"))
    updated = store.add_signals(connection, lead_id, found)
    score, reasons = scoring.score_lead(updated, scoring.get_icp(connection))
    store.save_scoring(connection, lead_id, score, reasons)
    connection.commit()
    store.record_run(
        connection,
        source="google_news",
        params={"news": params["q"]},
        found=len(found),
        added=0,
        credits=int(record.get("credits") or 0),
        cache_hit=bool(record.get("cache_hit")),
        record_id=str(record.get("id") or ""),
    )
    return {
        "lead": store.get_lead(connection, lead_id),
        "found": len(found),
        "credits": int(record.get("credits") or 0),
        "cache_hit": bool(record.get("cache_hit")),
        "record_id": record.get("id") or "",
    }


def draft_message(
    connection: sqlite3.Connection, lead_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    lead = store.get_lead(connection, lead_id)
    bridge = gateway()
    sender = store.clean(payload.get("sender"), 80) or (
        bridge.sender_name() if bridge else ""
    )
    return outreach.draft(
        lead,
        offer=str(payload.get("offer") or ""),
        sender=sender,
        tone=str(payload.get("tone") or "friendly"),
        channel=str(payload.get("channel") or "email"),
        model=bridge.model
        if bridge is not None and payload.get("use_model", True)
        else None,
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "\n")


def _cell(lead: Mapping[str, Any], key: str) -> str:
    value = lead.get(key)
    if key == "tags":
        return ", ".join(value or [])
    if key == "signals":
        return " | ".join(
            s.get("text", "") for s in (value or []) if isinstance(s, Mapping)
        )
    if key == "score":
        return "" if value is None else str(value)
    if key == "linkedin_url":
        return str(value or lead.get("sales_nav_url") or "")
    text = "" if value is None else str(value)
    return text


def export(
    connection: sqlite3.Connection, *, fmt: str, **filters: Any
) -> tuple[bytes, str, str]:
    """``(body, content_type, filename)`` for the leads matching the filters."""
    if fmt not in {"csv", "xlsx"}:
        raise ValueError("format must be csv or xlsx.")
    leads: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.query_leads(connection, limit=500, offset=offset, **filters)
        leads.extend(page["leads"])
        offset += 500
        if offset >= page["total"] or offset >= 50_000:
            break
    header = [label for _key, label in EXPORT_COLUMNS]
    rows = [[_cell(lead, key) for key, _label in EXPORT_COLUMNS] for lead in leads]
    stamp = airastore.now_utc().strftime("%Y%m%d")
    if fmt == "xlsx":
        return (
            xlsx.workbook("Leads", header, rows),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"aira-leads-{stamp}.xlsx",
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["'" + c if c[:1] in _FORMULA_LEADS else c for c in row])
    return (
        buffer.getvalue().encode("utf-8-sig"),
        "text/csv; charset=utf-8",
        f"aira-leads-{stamp}.csv",
    )


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def overview(
    connection: sqlite3.Connection,
    *,
    serpapi_configured: bool,
    key: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bridge = gateway()
    state = bridge.linkedin_state() if bridge is not None else "unavailable"
    return {
        "lists": store.list_lists(connection),
        "counts": store.counts(connection),
        "icp": scoring.get_icp(connection),
        "icp_empty": scoring.is_empty(scoring.get_icp(connection)),
        "searches": store.list_searches(connection),
        "runs": store.recent_runs(connection),
        "sources": [
            {
                "id": "google_maps",
                "label": SOURCE_LABELS["google_maps"],
                "available": serpapi_configured,
                "needs": ""
                if serpapi_configured
                else "A SerpApi key (Aira API > Account).",
                "cost": "1 search per page of about 20 businesses",
            },
            {
                "id": "google_web",
                "label": SOURCE_LABELS["google_web"],
                "available": serpapi_configured,
                "needs": ""
                if serpapi_configured
                else "A SerpApi key (Aira API > Account).",
                "cost": "1 search per page of about 10 results",
            },
            {
                "id": "google_intent",
                "label": SOURCE_LABELS["google_intent"],
                "available": serpapi_configured,
                "needs": ""
                if serpapi_configured
                else "A SerpApi key (Aira API > Account).",
                "cost": "1 search per page of about 10 posts",
            },
            {
                "id": "google_jobs",
                "label": SOURCE_LABELS["google_jobs"],
                "available": serpapi_configured,
                "needs": ""
                if serpapi_configured
                else "A SerpApi key (Aira API > Account).",
                "cost": "1 search per page of about 10 jobs",
            },
            {
                "id": "sales_navigator",
                "label": SOURCE_LABELS["sales_navigator"],
                "available": state == "connected",
                "needs": ""
                if state == "connected"
                else "Your LinkedIn account connected in Settings > Integrations, with Sales Navigator on it.",
                "cost": "no search credits; reads your own Sales Navigator",
            },
            {
                "id": "import",
                "label": SOURCE_LABELS["import"],
                "available": True,
                "needs": "",
                "cost": "free",
            },
        ],
        "serpapi": dict(key or {}),
        "linkedin_state": state,
    }


# ---------------------------------------------------------------------------
# outreach: drafts, call scripts (the person sends and calls; Aira prepares and records)
# ---------------------------------------------------------------------------


def draft_emails(
    connection: sqlite3.Connection, lead_ids: Sequence[str], payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Email drafts for the chosen leads, written from what each lead really has. Nothing is sent."""
    from aira.leads import mail

    settings = mail.get_settings(connection)
    bridge = gateway()
    services = payload.get("services")
    if isinstance(services, str):
        services = [s for s in services.replace(",", " ").split() if s]
    return mail.create_drafts(
        connection,
        lead_ids,
        settings=settings,
        offer=str(payload.get("offer") or ""),
        services=[str(s) for s in (services or settings.get("services") or [])],
        tone=str(payload.get("tone") or "friendly"),
        model=bridge.model
        if bridge is not None and payload.get("use_model", True)
        else None,
        sender=bridge.sender_name() if bridge is not None else "",
    )


def make_call_script(
    connection: sqlite3.Connection, lead_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    from aira.leads import calls, mail

    lead = store.get_lead(connection, lead_id)
    settings = mail.get_settings(connection)
    bridge = gateway()
    offered = [
        str(s) for s in (payload.get("services") or settings.get("services") or [])
    ]
    needs, findings = mail.lead_facts(connection, lead, offered)
    return calls.call_script(
        lead,
        offer=str(payload.get("offer") or settings.get("offer") or ""),
        sender=(bridge.sender_name() if bridge is not None else "")
        or str(settings.get("from_name") or ""),
        needs=needs,
        findings=findings,
        calling_from=str(settings.get("calling_number") or ""),
        model=bridge.model
        if bridge is not None and payload.get("use_model", True)
        else None,
    )
