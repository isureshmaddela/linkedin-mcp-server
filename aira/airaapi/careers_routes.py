"""Route handlers for the Careers workspace (``/api/careers/*``).

Thin glue over :mod:`aira.airaapi.careers` (job search, import, alerts) and
:mod:`aira.airaapi.careers_intel` (reports). Every handler runs as the signed-in
user, opens that user's database and uses the account's SerpApi key through the
same resolution as the Aira API console; the key is never part of a response.
Loaded by :func:`aira.airaapi.http.load_route_modules`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi import careers, careers_intel, compat, service, store
from aira.airaapi import routes as console
from aira.airaapi.http import Request, aira_route
from aira.audit import notify

REPORT_PATH = "|".join(careers_intel.KINDS)


def _payload_flag(request: Request, name: str) -> bool:
    return careers.flag(request.payload.get(name), name)


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/careers/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        resolved = console._resolved(request, connection)
        data = careers_intel.overview(
            connection,
            configured=resolved.configured,
            key=resolved.public(),
            now=store.now_utc(),
        )
    data["key_error"] = resolved.error
    return 200, data


# ---------------------------------------------------------------------------
# job search and import
# ---------------------------------------------------------------------------


@aira_route("POST", r"/api/careers/jobs/search")
def jobs_search(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        client = console._client(request, connection, required=False)
        outcome = careers.search_jobs(
            connection,
            client,
            request.payload,
            limits=console._limits(request),
            now=store.now_utc(),
        )
        if outcome.get("dry_run"):
            outcome["configured"] = console._resolved(request, connection).configured
    return 200, outcome


@aira_route("GET", r"/api/careers/records/([0-9a-f]{32})/jobs")
def record_jobs(request: Request) -> tuple[int, dict[str, Any]]:
    """The jobs inside a stored ``google_jobs`` search (Playground, History, monitors), ready to import."""
    with request.connect() as connection:
        record = service.get_search(connection, request.match.group(1))
        if record.get("engine") not in (careers.JOBS_ENGINE, careers.AIRA_JOBS_ENGINE):
            raise ValueError("Only Google Jobs searches hold jobs Careers can import.")
        created = record.get("created_at")
        jobs = careers.normalize_results(
            record.get("result"), store.from_iso(created) if created else None
        )
        careers.annotate_jobs(connection, jobs)
    return 200, {"jobs": jobs, "record_id": record["id"]}


@aira_route("POST", r"/api/careers/jobs/import")
def jobs_import(request: Request) -> tuple[int, dict[str, Any]]:
    job = request.payload.get("job")
    if not isinstance(job, dict):
        raise ValueError("Send the job to import as job.")
    with request.connect() as connection:
        outcome = careers.import_job(connection, job)
    request.audit(
        "careers.job.import",
        "job",
        str(outcome["job"].get("job_id", "")),
        {"source": careers.JOB_SOURCE, "created": outcome["created"]},
    )
    return (201 if outcome["created"] else 200), outcome


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


@aira_route("POST", rf"/api/careers/({REPORT_PATH})")
def report_run(request: Request) -> tuple[int, dict[str, Any]]:
    kind = request.match.group(1)
    spec = careers_intel.build_spec(kind, request.payload)
    limits = console._limits(request)
    now = store.now_utc()
    with request.connect() as connection:
        if _payload_flag(request, "dry_run"):
            plan = careers_intel.estimate(connection, spec, limits, now)
            plan["configured"] = console._resolved(request, connection).configured
            return 200, plan
        client = console._client(request, connection, required=False)
        report = careers_intel.run_report(
            connection, client, spec, limits=limits, now=now
        )
    request.audit(
        "careers.report",
        "careers_report",
        report["id"],
        {"kind": kind, "credits": report["credits"], "errors": report["errors"]},
    )
    return 200, report


@aira_route("GET", r"/api/careers/reports")
def reports_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        items = careers_intel.list_reports(
            connection,
            kind=request.q("kind"),
            limit=request.qint("limit", 20, 1, careers_intel.MAX_REPORTS),
        )
    return 200, {"reports": items}


@aira_route("GET", r"/api/careers/reports/([0-9a-f]{32})")
def report_detail(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, careers_intel.get_report(connection, request.match.group(1))


@aira_route("DELETE", r"/api/careers/reports/([0-9a-f]{32})")
def report_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        careers_intel.delete_report(connection, request.match.group(1))
    return 200, {"ok": True}


# ---------------------------------------------------------------------------
# job alerts
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/careers/alerts")
def alerts_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        alerts = careers.list_alerts(connection)
    return 200, {
        "alerts": alerts,
        "limits": {
            "max_alerts": careers.MAX_ALERTS,
            "min_interval_minutes": careers.ALERT_MIN_INTERVAL,
            "default_interval_minutes": careers.DEFAULT_ALERT_INTERVAL,
            "default_min_score": careers.DEFAULT_MIN_SCORE,
        },
    }


@aira_route("POST", r"/api/careers/alerts")
def alerts_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        alert = careers.create_alert(connection, request.payload, now=store.now_utc())
    request.audit(
        "careers.alert.create",
        "careers_alert",
        alert["id"],
        {
            "interval_minutes": alert["interval_minutes"],
            "auto_import": alert["auto_import"],
        },
    )
    return 201, alert


@aira_route("GET", r"/api/careers/alerts/([0-9a-f]{32})")
def alert_detail(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, careers.alert_detail(
            connection,
            request.match.group(1),
            include_dismissed=request.qbool("all") is True,
        )


@aira_route("PATCH", r"/api/careers/alerts/([0-9a-f]{32})")
def alert_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, careers.update_alert(
            connection, request.match.group(1), request.payload, now=store.now_utc()
        )


@aira_route("DELETE", r"/api/careers/alerts/([0-9a-f]{32})")
def alert_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        careers.delete_alert(connection, request.match.group(1))
    request.audit("careers.alert.delete", "careers_alert", request.match.group(1))
    return 200, {"ok": True}


@aira_route("POST", r"/api/careers/alerts/([0-9a-f]{32})/run")
def alert_run(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        client = console._client(request, connection, required=False)
        outcome = careers.run_alert(
            connection,
            client,
            request.match.group(1),
            notify=compat.make_notifier(request.connect, notify),
            limits=console._limits(request),
        )
    return 200, outcome


@aira_route("POST", r"/api/careers/alerts/([0-9a-f]{32})/dismiss")
def alert_dismiss(request: Request) -> tuple[int, dict[str, Any]]:
    keys = request.payload.get("job_keys")
    if keys is not None and (
        not isinstance(keys, list) or not all(isinstance(key, str) for key in keys)
    ):
        raise ValueError("job_keys must be a list of job keys.")
    if keys is None and not _payload_flag(request, "all"):
        raise ValueError("Send job_keys, or all: true to clear every new job.")
    with request.connect() as connection:
        return 200, careers.dismiss_alert_jobs(connection, request.match.group(1), keys)
