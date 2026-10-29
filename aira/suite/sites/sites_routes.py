"""Sites routes (``/api/sites/*``), its public pages and its Business hub cards.

The build route (``POST /api/sites/projects/{id}/generate``) answers when the whole site is written; while it works,
the page reads ``GET /api/sites/runs/{run_id}?from=N`` for the text written so far (the page picks the run id, so it
can start reading at once). Sites are served to their preview and public links under ``/api/public/<token>/w/``
with a sandboxed policy (see :func:`aira.suite.sites.service.site_headers`). Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from typing import Any

from aira.airaapi import store as api_store
from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life, public
from aira.suite.sites import deploy, designer, generator, providers, service, store

APP = store.APP
BASE = "/api/sites"
ID = "([0-9a-f]{32})"
TOKEN = f"({public.TOKEN_PATTERN})"
PROVIDER = "([a-z]{3,20})"
TARGET = "(github|netlify|vercel)"


def _open(request: Request) -> sqlite3.Connection:
    connection = request.connect()
    store.ensure(connection)
    life.require_grown_up(request, connection)
    return connection


def _actor(request: Request) -> str:
    return str((request.user or {}).get("email") or "")


def _id(request: Request, group: int = 1) -> str:
    return str(request.match.group(group))


# ---------------------------------------------------------------------------
# overview, search, templates
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        data = service.overview(connection)
        data["recent"] = service.list_projects(connection)[:12]
        data["templates"] = list(designer.TEMPLATES)
        data["stacks"] = [
            {"id": k, "label": v["label"]} for k, v in generator.STACKS.items()
        ]
        data["providers"] = _providers(request, connection)
        return 200, data


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, service.search(
            connection, request.q("q"), limit=request.qint("limit", 8, 1, 50)
        )


@aira_route("GET", f"{BASE}/templates")
def templates(request: Request) -> tuple[int, dict[str, Any]]:
    return 200, {"templates": list(designer.TEMPLATES)}


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/projects")
def projects_list(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, {
            "projects": service.list_projects(
                connection, q=request.q("q"), sort=request.q("sort", "updated")
            )
        }


@aira_route("GET", f"{BASE}/projects.csv")
def projects_csv(request: Request) -> Raw:
    with closing(_open(request)) as connection:
        rows = service.list_projects(connection)
    for row in rows:
        row["price"] = kit.fmt_money(int(row["price_cents"] or 0))
        row["paid"] = "yes" if row["paid"] else "no"
        row["published"] = (
            f"version {row['published_version']}" if row["published_version"] else "no"
        )
    columns = [
        "name",
        "client",
        "price",
        "paid",
        "published",
        "version",
        "file_count",
        "created_at",
        "updated_at",
    ]
    return Raw(
        200,
        kit.csv_bytes(rows, columns),
        "text/csv; charset=utf-8",
        filename="sites.csv",
    )


@aira_route("POST", f"{BASE}/projects")
def projects_create(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        project = service.create_project(
            connection, request.payload, actor=_actor(request)
        )
    request.audit("sites.create", "site", project["id"])
    return 201, {"project": project}


@aira_route("GET", f"{BASE}/projects/{ID}")
def project_get(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, {"project": service.get_project(connection, _id(request))}


@aira_route("PATCH", f"{BASE}/projects/{ID}")
def project_update(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        project = service.update_project(connection, _id(request), request.payload)
    request.audit("sites.update", "site", project["id"])
    return 200, {"project": project}


@aira_route("DELETE", f"{BASE}/projects/{ID}")
def project_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        service.delete_project(connection, _id(request))
    request.audit("sites.delete", "site", _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/projects/{ID}/duplicate")
def project_duplicate(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        project = service.duplicate_project(
            connection, _id(request), actor=_actor(request)
        )
    request.audit("sites.duplicate", "site", project["id"])
    return 201, {"project": project}


@aira_route("POST", f"{BASE}/projects/{ID}/paid")
def project_paid(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        project = service.mark_paid(
            connection, _id(request), kit.flag(request.payload.get("paid", True))
        )
    request.audit("sites.paid", "site", project["id"], {"paid": project["paid"]})
    return 200, {"project": project}


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/projects/{ID}/generate")
def project_generate(request: Request) -> tuple[int, dict[str, Any]]:
    """Build or change the site from the person's words; streams into the run while it works."""
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        row, config, context = service.start_run(
            connection, request.secret_store, _id(request), request.payload
        )
    result = service.run_build(
        request.connect, row, config, context, actor=_actor(request)
    )
    request.audit(
        "sites.build",
        "site",
        row["id"],
        {"provider": config.provider.id, "status": result["run"]["status"]},
    )
    return 200, result


@aira_route("GET", f"{BASE}/runs/{ID}")
def run_get(request: Request) -> tuple[int, dict[str, Any]]:
    """The run so far (the text from ``from`` on). An id that is not recorded is 404, like another account's."""
    with closing(_open(request)) as connection:
        row = store.run(connection, _id(request))
        return 200, {
            "run": service.public_run(row, since=request.qint("from", 0, 0, 10_000_000))
        }


@aira_route("POST", f"{BASE}/runs/{ID}/cancel")
def run_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, {"run": service.cancel_run(connection, _id(request))}


# ---------------------------------------------------------------------------
# files, images and versions
# ---------------------------------------------------------------------------


@aira_route("PUT", f"{BASE}/projects/{ID}/files")
def file_save(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, service.save_file(connection, _id(request), request.payload)


@aira_route("POST", f"{BASE}/projects/{ID}/files/delete")
def file_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, service.delete_file(
            connection, _id(request), str(request.payload.get("path") or "")
        )


@aira_route("POST", f"{BASE}/projects/{ID}/files/rename")
def file_rename(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, service.rename_file(connection, _id(request), request.payload)


@aira_route("POST", f"{BASE}/projects/{ID}/assets")
def asset_add(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 201, service.add_asset(connection, _id(request), request.payload)


@aira_route("DELETE", f"{BASE}/projects/{ID}/assets/([A-Za-z0-9._-]{{1,80}})")
def asset_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        service.delete_asset(connection, _id(request), _id(request, 2))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/projects/{ID}/versions/([0-9]{{1,6}})")
def version_get(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        store.project(connection, _id(request))
        return 200, {
            "version": store.version(connection, _id(request), int(_id(request, 2)))
        }


@aira_route("POST", f"{BASE}/projects/{ID}/versions/([0-9]{{1,6}})/restore")
def version_restore(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        result = service.restore(connection, _id(request), int(_id(request, 2)))
        result["project"] = service.get_project(connection, _id(request))
    request.audit(
        "sites.restore", "site", _id(request), {"version": int(_id(request, 2))}
    )
    return 200, result


# ---------------------------------------------------------------------------
# publishing, export and deploys
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/projects/{ID}/publish")
def project_publish(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        result = service.publish(
            connection, _id(request), request.payload, actor=_actor(request)
        )
        result["project"] = service.get_project(connection, _id(request))
    request.audit("sites.publish", "site", _id(request), {"version": result["version"]})
    return 200, result


@aira_route("DELETE", f"{BASE}/projects/{ID}/publish")
def project_unpublish(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        service.unpublish(connection, _id(request))
        project = service.get_project(connection, _id(request))
    request.audit("sites.unpublish", "site", _id(request))
    return 200, {"project": project}


@aira_route("GET", f"{BASE}/projects/{ID}/export.zip")
def project_export(request: Request) -> Raw:
    version = request.qint("version", 0, 0, 999_999) or None
    with closing(_open(request)) as connection:
        row, files = service.bundle_for(
            connection,
            _id(request),
            with_kit=request.qbool("kit") is not False,
            target="zip",
            version=version,
        )
        service.record_deploy(
            connection,
            row["id"],
            "zip",
            "done",
            detail=f"{len(files)} files",
            version=version or row["version"],
            actor=_actor(request),
        )
    request.audit("sites.export", "site", row["id"])
    return Raw(
        200,
        deploy.zip_bytes(files),
        "application/zip",
        filename=f"{deploy.slug(row['name'])}.zip",
    )


@aira_route("POST", f"{BASE}/projects/{ID}/deploy")
def project_deploy(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    target = kit.choice(payload.get("target"), "where to deploy", deploy.TARGETS)
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        token = _token(request, connection, target)
        if not token:
            raise ValueError(
                f"Save a {deploy.TARGET_LABELS[target]} token first (Deploy > Connect)."
            )
        row, files = service.bundle_for(
            connection,
            _id(request),
            with_kit=kit.flag(payload.get("kit", True)),
            target=target,
        )
        saved = kit.loads(row.get("deploy_json"), {}).get(target) or {}
    try:
        if target == "github":
            repo = deploy.check_repo(
                payload.get("repo") or saved.get("repo") or deploy.slug(row["name"])
            )
            branch = (
                kit.text(payload.get("branch"), "the branch", max_len=100)
                or saved.get("branch")
                or "main"
            )
            if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch) or ".." in branch:
                raise ValueError("Name the branch like main or site.")
            message = (
                kit.text(payload.get("message"), "the commit message", max_len=200)
                or f"Update {row['name']} (version {row['version']})"
            )
            result = deploy.push_github(
                token,
                repo,
                files,
                message=message,
                branch=branch,
                private=kit.flag(payload.get("private", True)),
                pages=kit.flag(payload.get("pages", False)),
                description=f"{row['name']}, built with Monosage Sites",
            )
            remember = {
                "repo": result["repo"],
                "branch": branch,
                "url": result["url"],
                "pages_url": result.get("pages_url", ""),
            }
            url = result.get("pages_url") or result["url"]
        elif target == "netlify":
            result = deploy.deploy_netlify(
                token, files, site_id=str(saved.get("site_id") or "")
            )
            remember = {"site_id": result["site_id"], "url": result["url"]}
            url = result["url"]
        else:
            result = deploy.deploy_vercel(token, files, name=row["name"])
            remember = {"url": result["url"]}
            url = result["url"]
    except deploy.DeployError as failure:
        with closing(_open(request)) as connection:
            service.record_deploy(
                connection,
                row["id"],
                target,
                "failed",
                detail=str(failure),
                version=row["version"],
                actor=_actor(request),
            )
        raise RuntimeError(str(failure)) from None
    with closing(_open(request)) as connection:
        record = service.record_deploy(
            connection,
            row["id"],
            target,
            "done",
            url=url,
            detail=kit.dumps(result)[:900],
            version=row["version"],
            actor=_actor(request),
            remember=remember,
        )
        project = service.get_project(connection, row["id"])
    request.audit("sites.deploy", "site", row["id"], {"target": target})
    return 200, {"deploy": {**record, "result": result}, "project": project}


def _token(request: Request, connection: sqlite3.Connection, target: str) -> str | None:
    try:
        return request.secret_store.get(connection, deploy.TOKEN_PREFIX + target)
    except Exception:  # noqa: BLE001 - an unreadable token reads as none; the page asks for it again
        return None


@aira_route("GET", f"{BASE}/deploy-targets")
def targets_status(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        out = []
        for target in deploy.TARGETS:
            token = _token(request, connection, target)
            out.append(
                {
                    "id": target,
                    "label": deploy.TARGET_LABELS[target],
                    "connected": bool(token),
                    "last4": token[-4:] if token else None,
                    "account": kit.get_setting(connection, APP, f"{target}_account", "")
                    if token
                    else "",
                }
            )
        return 200, {"targets": out}


@aira_route("POST", f"{BASE}/deploy-targets/{TARGET}")
def target_save(request: Request) -> tuple[int, dict[str, Any]]:
    target = _id(request)
    token = str(request.payload.get("token") or "").strip()
    if not 8 <= len(token) <= 500 or re.search(r"\s", token):
        raise ValueError(
            f"Paste the {deploy.TARGET_LABELS[target]} token (it has no spaces)."
        )
    account = ""
    if target == "github":
        try:
            account = deploy.github_login(token)
        except deploy.DeployError as failure:
            raise ValueError(str(failure)) from None
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        request.secret_store.put(
            connection, deploy.TOKEN_PREFIX + target, token, kit.utcnow()
        )
        kit.set_setting(connection, APP, f"{target}_account", account)
        connection.commit()
    request.audit("sites.deploy_token", "deploy_target", target, {"saved": True})
    return 200, {
        "id": target,
        "connected": True,
        "last4": token[-4:],
        "account": account,
    }


@aira_route("DELETE", f"{BASE}/deploy-targets/{TARGET}")
def target_forget(request: Request) -> tuple[int, dict[str, Any]]:
    target = _id(request)
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        request.secret_store.delete(connection, deploy.TOKEN_PREFIX + target)
        kit.set_setting(connection, APP, f"{target}_account", "")
        connection.commit()
    request.audit("sites.deploy_token", "deploy_target", target, {"saved": False})
    return 200, {"id": target, "connected": False}


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def _providers(request: Request, connection: sqlite3.Connection) -> dict[str, Any]:
    api_store.ensure_schema(connection)
    items = []
    for provider in providers.offered():
        config = providers.load_config(connection, request.secret_store, provider.id)
        saved = (
            kit.fetch_one(
                connection,
                "SELECT base_url, model FROM sb_providers WHERE id = ?",
                (provider.id,),
            )
            or {}
        )
        items.append(
            {
                **provider.public(),
                "base_url": config.base_url,
                "model": config.model,
                "configured": bool(config.key),
                "last4": config.key[-4:] if config.key else None,
                "ready": config.ready
                and (
                    provider.kind in ("builtin", "platform")
                    or bool(config.base_url and config.model)
                ),
                "saved": bool(saved),
            }
        )
    return {
        "providers": items,
        "default": service.default_provider(connection),
        "hosted": providers.hosted(),
    }


@aira_route("GET", f"{BASE}/providers")
def providers_list(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        return 200, _providers(request, connection)


@aira_route("POST", f"{BASE}/default-provider")
def providers_default(request: Request) -> tuple[int, dict[str, Any]]:
    provider = providers.get(str(request.payload.get("provider") or ""))
    with closing(_open(request)) as connection:
        kit.set_setting(connection, APP, service.DEFAULT_PROVIDER_SETTING, provider.id)
        connection.commit()
        return 200, _providers(request, connection)


@aira_route("POST", f"{BASE}/providers/{PROVIDER}")
def provider_save(request: Request) -> tuple[int, dict[str, Any]]:
    provider = providers.get(_id(request))
    payload = request.payload
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        row = (
            kit.fetch_one(
                connection, "SELECT * FROM sb_providers WHERE id = ?", (provider.id,)
            )
            or {}
        )
        base_url = row.get("base_url") or ""
        model = row.get("model") or ""
        if "base_url" in payload:
            base_url = providers.check_base_url(provider, payload.get("base_url"))
            if base_url == provider.base_url.rstrip("/"):
                base_url = ""
        if "model" in payload:
            model = kit.text(payload.get("model"), "the model", max_len=200)
        key = payload.get("api_key")
        if key not in (None, ""):
            key = str(key).strip()
            if not 8 <= len(key) <= 500 or re.search(r"\s", key):
                raise ValueError("Paste the API key as it is (it has no spaces).")
            request.secret_store.put(
                connection, providers.KEY_PREFIX + provider.id, key, kit.utcnow()
            )
        connection.execute("DELETE FROM sb_providers WHERE id = ?", (provider.id,))
        connection.execute(
            "INSERT INTO sb_providers (id, base_url, model, updated_at) VALUES (?, ?, ?, ?)",
            (provider.id, base_url, model, kit.now()),
        )
        if kit.flag(payload.get("default")):
            kit.set_setting(
                connection, APP, service.DEFAULT_PROVIDER_SETTING, provider.id
            )
        connection.commit()
        status = _providers(request, connection)
    request.audit(
        "sites.provider",
        "provider",
        provider.id,
        {
            "key_saved": key not in (None, ""),
            "changed": sorted(k for k in payload if k != "api_key"),
        },
    )
    return 200, status


@aira_route("DELETE", f"{BASE}/providers/{PROVIDER}/key")
def provider_forget_key(request: Request) -> tuple[int, dict[str, Any]]:
    provider = providers.get(_id(request))
    with closing(_open(request)) as connection:
        api_store.ensure_schema(connection)
        request.secret_store.delete(connection, providers.KEY_PREFIX + provider.id)
        connection.commit()
        status = _providers(request, connection)
    request.audit("sites.provider", "provider", provider.id, {"key_saved": False})
    return 200, status


@aira_route("POST", f"{BASE}/providers/{PROVIDER}/test")
def provider_test(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        config = providers.load_config(
            connection,
            request.secret_store,
            _id(request),
            model=str(request.payload.get("model") or ""),
        )
    try:
        return 200, providers.check(config)
    except providers.ModelError as failure:
        raise RuntimeError(str(failure)) from None


@aira_route("GET", f"{BASE}/providers/{PROVIDER}/models")
def provider_models(request: Request) -> tuple[int, dict[str, Any]]:
    with closing(_open(request)) as connection:
        config = providers.load_config(connection, request.secret_store, _id(request))
    try:
        models = providers.list_models(config)
    except providers.ModelError as failure:
        return 200, {
            "models": list(config.provider.models),
            "error": str(failure),
            "reachable": False,
        }
    return 200, {"models": models or list(config.provider.models), "reachable": True}


# ---------------------------------------------------------------------------
# public: the preview and the published site
# ---------------------------------------------------------------------------


@public.page(APP, "preview")
def _preview_page(
    connection: sqlite3.Connection, link: dict[str, Any]
) -> dict[str, Any]:
    return {
        "url": service.site_path(link["token"]),
        "brand": False,
        "business_payment": False,
    }


@public.page(APP, "site")
def _site_page(connection: sqlite3.Connection, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    if not store.project(connection, str(link["ref_id"])).get("published_version"):
        raise LookupError("This site is not published.")
    return {
        "url": service.site_path(link["token"]),
        "brand": False,
        "business_payment": False,
    }


def _site_link(request: Request, connection: sqlite3.Connection) -> dict[str, Any]:
    store.ensure(connection)
    link = public.resolve(connection, request.match.group(1))
    if link["app"] != APP or link["kind"] not in ("preview", "site"):
        raise LookupError("This link is no longer available.")
    return link


def _serve(
    request: Request,
    link: dict[str, Any],
    connection: sqlite3.Connection,
    path: str,
    version: int | None,
) -> Raw:
    status, body, content_type = service.serve(connection, link, path, version=version)
    return Raw(
        status,
        body,
        content_type,
        headers=service.site_headers(
            request.base_url(), published=link["kind"] == "site"
        ),
    )


@aira_route("GET", f"/api/public/{TOKEN}/w(/.*)?", auth="public_link")
def public_site(request: Request) -> Raw:
    """A file of the site: the working copy for a preview link, the published version for a site link."""
    token, rest = request.match.group(1), request.match.group(2)
    if rest is None:
        return Raw(
            301,
            b"",
            "text/plain; charset=utf-8",
            headers=(("Location", f"/api/public/{token}/w/"),),
        )
    with closing(request.connect()) as connection:
        link = _site_link(request, connection)
        return _serve(request, link, connection, rest[1:] or "/", None)


@aira_route("GET", f"/api/public/{TOKEN}/v/([0-9]{{1,6}})(/.*)?", auth="public_link")
def public_version(request: Request) -> Raw:
    """An earlier version of the site through its preview link (History > Preview)."""
    token, number, rest = (
        request.match.group(1),
        int(request.match.group(2)),
        request.match.group(3),
    )
    if rest is None:
        return Raw(
            301,
            b"",
            "text/plain; charset=utf-8",
            headers=(("Location", f"/api/public/{token}/v/{number}/"),),
        )
    with closing(request.connect()) as connection:
        link = _site_link(request, connection)
        if link["kind"] != "preview":
            raise LookupError("This link is no longer available.")
        return _serve(request, link, connection, rest[1:] or "/", number)


# ---------------------------------------------------------------------------
# Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    store.ensure(connection)
    models = kit.count(
        connection, "SELECT COUNT(*) FROM sb_providers WHERE id <> 'builtin'"
    )
    return [
        kit.step(
            "site",
            "Build your first site",
            kit.count(connection, "SELECT COUNT(*) FROM sb_versions") > 0,
            "Describe it in Sites and press Build",
        ),
        kit.step(
            "model",
            "Connect a model",
            models > 0,
            "Ollama on this computer, Claude, Gemini or OpenAI in Sites > Models",
        ),
        kit.step(
            "publish",
            "Publish a site",
            kit.count(
                connection,
                "SELECT COUNT(*) FROM sb_projects WHERE published_version IS NOT NULL",
            )
            > 0,
            "Deploy > Publish gives it a public link",
        ),
        kit.step(
            "client",
            "Bill a client for a site",
            kit.count(
                connection, "SELECT COUNT(*) FROM sb_projects WHERE price_cents > 0"
            )
            > 0,
            "Set a client and a price in the site's details",
        ),
    ]


@kit.summary(APP)
def _summary(connection: sqlite3.Connection) -> dict[str, Any]:
    store.ensure(connection)
    data = service.overview(connection)
    return {
        "cards": [
            {"label": "Sites", "value": data["sites"]},
            {"label": "Published", "value": data["published"]},
            {
                "label": "Client sites paid",
                "value": kit.fmt_money(data["paid_cents"]),
                "tone": "good" if data["paid_cents"] else "",
            },
            {
                "label": "Waiting for payment",
                "value": kit.fmt_money(data["unpaid_cents"]),
                "tone": "warn" if data["unpaid_cents"] else "",
            },
        ]
    }
