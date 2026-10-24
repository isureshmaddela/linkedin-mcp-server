"""The web UI shell, health and readiness, and the vulnerability-disclosure file."""

from __future__ import annotations

from aira_cloud import __version__
from aira_cloud.db import loads, one
from aira_cloud.webio import Reply, Req, cloud_route

CLOUD_MARKER = "<!--aira:cloud-->"
CLOUD_SCRIPTS = (
    '<link rel="stylesheet" href="/static/cloud.css">'
    '<link rel="stylesheet" href="/static/cloud-admin.css">'
    '<link rel="stylesheet" href="/static/cloud-platform.css">'
    '<script src="/static/cloud-core.js"></script>'
    '<script src="/static/cloud-auth.js"></script>'
    '<script src="/static/cloud-org.js"></script>'
    '<script src="/static/cloud-people.js"></script>'
    '<script src="/static/cloud-billing.js"></script>'
    '<script src="/static/cloud-coach.js"></script>'
    '<script src="/static/cloud-platform.js"></script>'
    '<script src="/static/cloud-boot.js"></script>'
)


def _local():  # noqa: ANN202
    import auto_apply_assistant

    return auto_apply_assistant


@cloud_route("GET", r"/", auth="public")
def ui_index(req: Req):  # noqa: ANN201
    local = _local()
    try:
        text = local.UI_FILE.read_text(encoding="utf-8")
    except OSError:
        return Reply(500, {"error": "The web interface files are missing."})
    return Reply(
        200,
        text.replace(CLOUD_MARKER, CLOUD_SCRIPTS).encode("utf-8"),
        content_type="text/html; charset=utf-8",
    )


@cloud_route("GET", r"/static/([A-Za-z0-9_.-]+)", auth="public")
def ui_static(req: Req):  # noqa: ANN201
    local = _local()
    name = req.group()
    target = (local.STATIC_DIR / name).resolve()
    if (
        local.STATIC_DIR.resolve() not in target.parents
        or target.suffix not in local.STATIC_TYPES
        or not target.is_file()
    ):
        return Reply(404, {"error": "Not found.", "code": "not_found"})
    return Reply(
        200,
        target.read_bytes(),
        content_type=local.STATIC_TYPES[target.suffix],
        headers=[("Cache-Control", "no-cache")],
    )


@cloud_route("GET", r"/api/health", auth="public")
def health(req: Req):  # noqa: ANN201
    return 200, {
        "status": "ok",
        "service": "aira-cloud",
        "version": __version__,
        "region": req.app.config.region,
    }


@cloud_route("GET", r"/api/ready", auth="public")
def ready(req: Req):  # noqa: ANN201
    app = req.app
    checks: dict[str, dict[str, object]] = {}
    try:
        with app.db.read() as connection:
            one(connection, "SELECT 1 AS x")
        from aira_cloud.schema import MIGRATIONS

        checks["database"] = {
            "ok": app.db.version() == MIGRATIONS[-1][0],
            "version": app.db.version(),
        }
    except Exception as error:  # noqa: BLE001
        checks["database"] = {"ok": False, "message": str(error)[:120]}
    try:
        probe = app.config.data_root / ".ready"
        probe.write_text("ok")
        probe.unlink()
        checks["data_directory"] = {"ok": True}
    except OSError as error:
        checks["data_directory"] = {"ok": False, "message": str(error)[:120]}
    stats = app.queue.stats()
    checks["queue"] = {
        "ok": True,
        "depth": stats["depth"],
        "workers": len(stats["workers"]),
        "optional": True,
    }
    healthy = all(item["ok"] for item in checks.values() if not item.get("optional"))
    return (200 if healthy else 503), {
        "status": "ready" if healthy else "degraded",
        "checks": checks,
    }


@cloud_route("GET", r"/\.well-known/security\.txt", auth="public")
def security_txt(req: Req):  # noqa: ANN201
    contact = req.app.config.security_contact
    text = f"Contact: mailto:{contact}\nExpires: 2027-12-31T00:00:00.000Z\nPreferred-Languages: en\nPolicy: {req.app.config.base_url}/legal/security\n"
    return Reply(200, text.encode("utf-8"), content_type="text/plain; charset=utf-8")


@cloud_route("GET", r"/api/orgs/([a-z0-9-]{1,60})/branding", auth="public")
def org_branding(req: Req):  # noqa: ANN201
    """The organization's name and logo for its sign-in page. Only organizations whose plan includes branding show them."""
    app = req.app
    with app.db.read() as connection:
        org = one(
            connection,
            "SELECT org_id, branding FROM organizations WHERE slug = ? AND deleted_at IS NULL",
            (req.group(),),
        )
    if org is None or not loads(org["branding"], {}).get("public"):
        return 200, {
            "branded": False
        }  # nothing is revealed unless the organization chose to show its name here
    branding = app.tenancy.branding(org["org_id"])
    if not branding.get("branded"):
        return 200, {"branded": False}
    return 200, {
        "branded": True,
        "name": branding["name"],
        "logo_url": branding.get("logo_url", ""),
    }


@cloud_route("POST", r"/api/internal/remote-login/validate", auth="public", csrf=False)
def remote_login_validate(req: Req):  # noqa: ANN201
    """The remote-desktop gateway asks which candidate's browser a sign-in token opens (signed with the broker secret; once)."""
    try:
        req.app.signed_requests.verify(
            req.request.headers_object(), req.request.body, purpose="broker"
        )
    except PermissionError as error:
        return 401, {"error": str(error), "code": "bad_signature"}
    return 200, req.app.linkedin.validate(str(req.body().get("token", "")))


@cloud_route("POST", r"/api/internal/remote-login/event", auth="public", csrf=False)
def remote_login_event(req: Req):  # noqa: ANN201
    """The sign-in broker reports a session's start, its heartbeats and how it ended (signed with the broker secret)."""
    try:
        req.app.signed_requests.verify(
            req.request.headers_object(), req.request.body, purpose="broker"
        )
    except PermissionError as error:
        return 401, {"error": str(error), "code": "bad_signature"}
    body = req.body()
    return 200, req.app.linkedin.remote_login_event(
        str(body.get("token", "")),
        str(body.get("event", "")),
        str(body.get("outcome", "")),
    )


@cloud_route("GET", r"/robots\.txt", auth="public")
def robots(req: Req):  # noqa: ANN201
    return Reply(
        200,
        b"User-agent: *\nDisallow: /api/\n",
        content_type="text/plain; charset=utf-8",
    )
