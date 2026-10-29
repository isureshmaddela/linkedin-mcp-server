"""Sites rules: projects, versions, builds (runs), files served to the preview and the public, and client money.

A **run** is one request to the builder. The route creates the run row, then the model's answer streams into it
(:class:`Progress`: the text so far, the phase, the file being written, the files finished), so the page can show
the agent's steps and the code while the same request is still working. When the answer is complete its files are
written to the working copy and a new version is made. A person can stop a run; a run left behind by a stopped
server is marked failed after :data:`STALE_MINUTES`.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from typing import Any

from aira import suite
from aira.suite import kit, public
from aira.suite.sites import deploy, designer, generator, providers, store

APP = store.APP
STALE_MINUTES = 30
PROGRESS_SECONDS = 0.3
DEFAULT_PROVIDER_SETTING = "default_provider"
STACK_CHOICES = tuple(generator.STACKS)
ASSET_TYPES = {
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",
    b"\x00\x00\x01\x00": "image/x-icon",
}
MAX_ASSET_BYTES = 3 * 1024 * 1024
MAX_ASSETS_BYTES = 20 * 1024 * 1024
MIME = {
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8",
    "jsx": "text/babel; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "svg": "image/svg+xml",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "xml": "application/xml; charset=utf-8",
    "webmanifest": "application/manifest+json",
    "map": "application/json; charset=utf-8",
}
_HEX32 = re.compile(r"[0-9a-f]{32}")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


def _links(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    preview = public.link_for(connection, APP, "preview", project_id)
    site = public.link_for(connection, APP, "site", project_id)
    return {
        "preview": {**preview, "url": site_path(preview["token"])} if preview else None,
        "site": {**site, "url": site_path(site["token"])} if site else None,
    }


def site_path(token: str) -> str:
    return f"/api/public/{token}/w/"


def _public_project(
    connection: sqlite3.Connection, row: dict[str, Any], *, full: bool = False
) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k not in ("spec_json", "deploy_json")}
    out["paid"] = bool(row.get("paid_at"))
    out["designer"] = bool(kit.loads(row.get("spec_json"), {}))
    out["deploys"] = kit.loads(row.get("deploy_json"), {})
    counts = kit.fetch_one(
        connection,
        "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(content)), 0) AS chars FROM sb_files WHERE project_id = ?",
        (row["id"],),
    ) or {"n": 0, "chars": 0}
    out["file_count"] = int(counts["n"] or 0)
    out["size"] = int(counts["chars"] or 0)
    out["links"] = _links(connection, row["id"])
    out["first_prompt"] = (
        (
            kit.fetch_one(
                connection,
                "SELECT content FROM sb_messages WHERE project_id = ? AND role = 'user' ORDER BY created_at LIMIT 1",
                (row["id"],),
            )
            or {}
        ).get("content")
        or ""
    )[:300]
    if full:
        out["files"] = store.files(connection, row["id"])
        out["assets"] = store.assets(connection, row["id"])
        out["versions"] = store.versions(connection, row["id"])
        out["messages"] = store.messages(connection, row["id"])
        out["deploy_history"] = kit.fetch_all(
            connection,
            "SELECT id, target, status, url, detail, version, created_at FROM sb_deploys WHERE project_id = ? ORDER BY created_at DESC LIMIT 20",
            (row["id"],),
        )
        out["running"] = _running(connection, row["id"])
    return out


def _running(connection: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    stale = kit.iso(kit.utcnow() - timedelta(minutes=STALE_MINUTES))
    connection.execute(
        "UPDATE sb_runs SET status = 'failed', error = ?, finished_at = ? WHERE project_id = ? AND status = 'running' AND started_at < ?",
        (
            "The build stopped (the server restarted or the model went quiet). Try again.",
            kit.now(),
            project_id,
            stale,
        ),
    )
    row = kit.fetch_one(
        connection,
        "SELECT id, status, phase, provider, model, started_at FROM sb_runs WHERE project_id = ? AND status = 'running' ORDER BY started_at DESC LIMIT 1",
        (project_id,),
    )
    return row


def list_projects(
    connection: sqlite3.Connection, *, q: str = "", sort: str = "updated"
) -> list[dict[str, Any]]:
    where, params = "", []
    if q:
        where = "WHERE name LIKE ? OR description LIKE ? OR client LIKE ?"
        like = f"%{q[:100]}%"
        params = [like, like, like]
    order = {
        "updated": "updated_at DESC",
        "name": "name COLLATE NOCASE",
        "created": "created_at DESC",
    }.get(sort, "updated_at DESC")
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM sb_projects {where} ORDER BY {order} LIMIT 500",
        params,
    )  # noqa: S608 - fixed order
    return [_public_project(connection, row) for row in rows]


def get_project(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    row = store.project(connection, project_id)
    ensure_preview(connection, row)
    return _public_project(connection, row, full=True)


def ensure_preview(
    connection: sqlite3.Connection, row: dict[str, Any]
) -> dict[str, Any]:
    return public.create_link(
        connection, APP, "preview", row["id"], title=f"Preview of {row['name']}"
    )


def _project_values(payload: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "name" in payload or not partial:
        values["name"] = (
            kit.text(
                payload.get("name"),
                "the site's name",
                max_len=80,
                required=not partial or "name" in payload,
            )
            or "Untitled site"
        )
    if "description" in payload:
        values["description"] = kit.text(
            payload.get("description"), "the description", max_len=500, multiline=True
        )
    if "stack" in payload or not partial:
        values["stack"] = kit.choice(
            payload.get("stack"),
            "the stack",
            STACK_CHOICES,
            default=generator.DEFAULT_STACK,
        )
    if "client" in payload:
        values["client"] = kit.text(payload.get("client"), "the client", max_len=120)
    if "price_cents" in payload:
        values["price_cents"] = kit.cents_value(
            payload.get("price_cents"), "the price", default=0
        )
    if "color" in payload:
        values["color"] = (
            kit.color(payload.get("color")) if payload.get("color") else ""
        )
    return values


def create_project(
    connection: sqlite3.Connection, payload: dict[str, Any], *, actor: str = ""
) -> dict[str, Any]:
    prompt = kit.text(payload.get("prompt"), "your idea", max_len=8000, multiline=True)
    template = str(payload.get("template") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not name and template:
        name = designer.KINDS.get(template, designer.GENERIC)["name"]
    if not name and prompt:
        name = designer.name_from(prompt) or _title_from(prompt)
    values = _project_values(
        {**payload, "name": name or "Untitled site"}, partial=False
    )
    project_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sb_projects (id, name, description, stack, color, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            values["name"],
            values.get("description", ""),
            values["stack"],
            values.get("color", ""),
            actor[:120],
            stamp,
            stamp,
        ),
    )
    if template:
        found = next((t for t in designer.TEMPLATES if t["id"] == template), None)
        if not found:
            raise ValueError("That template was not found.")
        spec = designer.spec_from_prompt(found["prompt"])
        spec["name"] = values["name"]
        files = designer.render(spec)
        store.write_files(connection, project_id, files)
        connection.execute(
            "UPDATE sb_projects SET spec_json = ?, provider = 'builtin' WHERE id = ?",
            (kit.dumps(spec), project_id),
        )
        snapshot(
            connection,
            project_id,
            label=f"Started from the {found['name']} template",
            source="template",
            changed=sorted(files),
        )
        store.add_message(
            connection,
            project_id,
            "assistant",
            f"Started {values['name']} from the {found['name']} template. Tell me what to change.",
            version=1,
        )
    kit.log(
        connection,
        APP,
        "site_created",
        f"New site: {values['name']}",
        ref_type="site",
        ref_id=project_id,
        actor=actor,
    )
    connection.commit()
    row = store.project(connection, project_id)
    ensure_preview(connection, row)
    return get_project(connection, project_id)


def _title_from(prompt: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&-]*", prompt)
    skip = {
        "a",
        "an",
        "the",
        "build",
        "create",
        "make",
        "me",
        "website",
        "site",
        "page",
        "landing",
        "for",
        "my",
        "of",
        "with",
        "please",
        "simple",
        "modern",
        "beautiful",
        "web",
    }
    picked = [w for w in words if w.lower() not in skip][:4]
    return " ".join(w[:1].upper() + w[1:] for w in picked)[:60] or "Untitled site"


def update_project(
    connection: sqlite3.Connection, project_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    row = store.project(connection, project_id)
    values = _project_values(payload, partial=True)
    if values:
        kit.update_row(
            connection, "sb_projects", project_id, {**values, "updated_at": kit.now()}
        )
        if "name" in values and values["name"] != row["name"]:
            ensure_preview(connection, {**row, **values})
            site = public.link_for(connection, APP, "site", project_id)
            if site:
                public.create_link(
                    connection, APP, "site", project_id, title=values["name"]
                )
        connection.commit()
    return get_project(connection, project_id)


def delete_project(connection: sqlite3.Connection, project_id: str) -> None:
    row = store.project(connection, project_id)
    for kind in ("preview", "site"):
        public.revoke(connection, APP, kind, project_id)
    if row.get("paid_at"):
        kit.void_revenue(connection, APP, "site", project_id)
    store.delete_project(connection, project_id)
    kit.log(
        connection,
        APP,
        "site_deleted",
        f"Deleted site: {row['name']}",
        ref_type="site",
        ref_id=project_id,
    )
    connection.commit()


def duplicate_project(
    connection: sqlite3.Connection, project_id: str, *, actor: str = ""
) -> dict[str, Any]:
    row = store.project(connection, project_id)
    new_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sb_projects (id, name, description, stack, provider, model, spec_json, color, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id,
            f"{row['name']} (copy)"[:80],
            row["description"],
            row["stack"],
            row["provider"],
            row["model"],
            row["spec_json"],
            row["color"],
            actor[:120],
            stamp,
            stamp,
        ),
    )
    files = store.files(connection, project_id)
    store.write_files(connection, new_id, files)
    for item in kit.fetch_all(
        connection, "SELECT * FROM sb_assets WHERE project_id = ?", (project_id,)
    ):
        connection.execute(
            "INSERT INTO sb_assets (id, project_id, name, mime, size, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                kit.new_id(),
                new_id,
                item["name"],
                item["mime"],
                item["size"],
                item["data"],
                stamp,
            ),
        )
    if files:
        snapshot(
            connection,
            new_id,
            label=f"Copied from {row['name']}",
            source="copy",
            changed=sorted(files),
        )
    connection.commit()
    ensure_preview(connection, store.project(connection, new_id))
    return get_project(connection, new_id)


# ---------------------------------------------------------------------------
# versions and files
# ---------------------------------------------------------------------------


def snapshot(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    label: str,
    source: str,
    changed: list[str],
    run_id: str = "",
) -> int:
    """Save every text file as a new version; returns its number."""
    files = store.files(connection, project_id)
    number = (
        kit.count(
            connection,
            "SELECT COALESCE(MAX(number), 0) FROM sb_versions WHERE project_id = ?",
            (project_id,),
        )
        + 1
    )
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sb_versions (id, project_id, number, label, source, files_json, changed_json, run_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kit.new_id(),
            project_id,
            number,
            label[:200],
            source,
            kit.dumps(files),
            kit.dumps(changed[:100]),
            run_id,
            stamp,
            stamp,
        ),
    )
    connection.execute(
        "UPDATE sb_projects SET version = ?, updated_at = ? WHERE id = ?",
        (number, stamp, project_id),
    )
    return number


def _edit_snapshot(
    connection: sqlite3.Connection, project_id: str, path: str, label: str
) -> int:
    """A manual edit: typing in the same file again within two minutes updates that version instead of adding one."""
    last = kit.fetch_one(
        connection,
        "SELECT * FROM sb_versions WHERE project_id = ? ORDER BY number DESC LIMIT 1",
        (project_id,),
    )
    recent = kit.iso(kit.utcnow() - timedelta(minutes=2))
    if (
        last
        and last["source"] == "edit"
        and kit.loads(last["changed_json"], []) == [path]
        and last["updated_at"] >= recent
    ):
        connection.execute(
            "UPDATE sb_versions SET files_json = ?, updated_at = ? WHERE id = ?",
            (kit.dumps(store.files(connection, project_id)), kit.now(), last["id"]),
        )
        connection.execute(
            "UPDATE sb_projects SET updated_at = ? WHERE id = ?",
            (kit.now(), project_id),
        )
        return int(last["number"])
    return snapshot(connection, project_id, label=label, source="edit", changed=[path])


def save_file(
    connection: sqlite3.Connection, project_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    store.project(connection, project_id)
    path = store.clean_path(payload.get("path"))
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValueError("Send the file's text as content.")
    current = store.files(connection, project_id)
    if current.get(path) == content:
        return {
            "path": path,
            "version": int(store.project(connection, project_id)["version"]),
            "changed": False,
        }
    store.write_files(connection, project_id, {path: content})
    number = _edit_snapshot(
        connection,
        project_id,
        path,
        f"{'Edited' if path in current else 'Added'} {path}",
    )
    connection.commit()
    return {"path": path, "version": number, "changed": True}


def delete_file(
    connection: sqlite3.Connection, project_id: str, path: str
) -> dict[str, Any]:
    store.project(connection, project_id)
    clean = store.clean_path(path)
    if clean not in store.files(connection, project_id):
        raise LookupError(f"{clean} was not found.")
    if clean == "index.html":
        raise ValueError("index.html is the home page. Edit it instead of deleting it.")
    store.write_files(connection, project_id, {}, [clean])
    number = snapshot(
        connection, project_id, label=f"Deleted {clean}", source="edit", changed=[clean]
    )
    connection.commit()
    return {"version": number}


def rename_file(
    connection: sqlite3.Connection, project_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    store.project(connection, project_id)
    old, new = (
        store.clean_path(payload.get("from")),
        store.clean_path(payload.get("to")),
    )
    current = store.files(connection, project_id)
    if old not in current:
        raise LookupError(f"{old} was not found.")
    if new in current:
        raise ValueError(f"{new} already exists.")
    if old == "index.html":
        raise ValueError("index.html is the home page; it keeps its name.")
    store.write_files(connection, project_id, {new: current[old]}, [old])
    number = snapshot(
        connection,
        project_id,
        label=f"Renamed {old} to {new}",
        source="edit",
        changed=[old, new],
    )
    connection.commit()
    return {"version": number, "path": new}


def restore(
    connection: sqlite3.Connection, project_id: str, number: int
) -> dict[str, Any]:
    store.project(connection, project_id)
    found = store.version(connection, project_id, number)
    current = store.files(connection, project_id)
    files = {path: str(text) for path, text in found["files"].items()}
    store.write_files(
        connection, project_id, files, [p for p in current if p not in files]
    )
    changed = sorted(
        {p for p in files if current.get(p) != files[p]}
        | {p for p in current if p not in files}
    )
    new_number = snapshot(
        connection,
        project_id,
        label=f"Restored version {number}",
        source="restore",
        changed=changed,
    )
    store.add_message(
        connection,
        project_id,
        "assistant",
        f"Restored version {number}.",
        version=new_number,
        meta={"restored": number},
    )
    connection.commit()
    return {"version": new_number}


def add_asset(
    connection: sqlite3.Connection, project_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    store.project(connection, project_id)
    raw_name = (
        str(payload.get("name") or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    )
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-.")[:80].lower()
    if not name or "." not in name:
        raise ValueError("Name the image file, like logo.png.")
    try:
        data = base64.b64decode(str(payload.get("data") or ""), validate=True)
    except ValueError:
        raise ValueError("The image could not be read. Pick the file again.") from None
    if not data:
        raise ValueError("The image is empty.")
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError("An image can be at most 3 MB. Make it smaller and try again.")
    mime = next((m for magic, m in ASSET_TYPES.items() if data.startswith(magic)), "")
    if mime == "image/webp" and data[8:12] != b"WEBP":
        mime = ""
    if not mime and name.endswith(".svg") and b"<svg" in data[:2000].lower():
        if re.search(rb"<script|\son[a-z]+\s*=|javascript:", data, re.I):
            raise ValueError(
                "That SVG has scripts in it. Export it again without scripts."
            )
        mime = "image/svg+xml"
    if not mime:
        raise ValueError("Upload a PNG, JPEG, GIF, WebP, SVG or ICO image.")
    used = kit.count(
        connection,
        "SELECT COALESCE(SUM(size), 0) FROM sb_assets WHERE project_id = ? AND name <> ?",
        (project_id, name),
    )
    if used + len(data) > MAX_ASSETS_BYTES:
        raise ValueError("A site can hold 20 MB of images. Remove some first.")
    connection.execute(
        "DELETE FROM sb_assets WHERE project_id = ? AND name = ?", (project_id, name)
    )
    connection.execute(
        "INSERT INTO sb_assets (id, project_id, name, mime, size, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            kit.new_id(),
            project_id,
            name,
            mime,
            len(data),
            base64.b64encode(data).decode(),
            kit.now(),
        ),
    )
    connection.execute(
        "UPDATE sb_projects SET updated_at = ? WHERE id = ?", (kit.now(), project_id)
    )
    connection.commit()
    return {
        "asset": {
            "name": name,
            "mime": mime,
            "size": len(data),
            "path": f"assets/{name}",
        }
    }


def delete_asset(connection: sqlite3.Connection, project_id: str, name: str) -> None:
    store.project(connection, project_id)
    if not connection.execute(
        "DELETE FROM sb_assets WHERE project_id = ? AND name = ?", (project_id, name)
    ).rowcount:
        raise LookupError("That image was not found.")
    connection.commit()


def _asset_bytes(
    connection: sqlite3.Connection, project_id: str
) -> list[tuple[str, bytes]]:
    rows = kit.fetch_all(
        connection,
        "SELECT name, data FROM sb_assets WHERE project_id = ? ORDER BY name",
        (project_id,),
    )
    return [(row["name"], base64.b64decode(row["data"])) for row in rows]


# ---------------------------------------------------------------------------
# publishing and serving
# ---------------------------------------------------------------------------


def publish(
    connection: sqlite3.Connection,
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    row = store.project(connection, project_id)
    number = kit.integer(
        payload.get("version"),
        "the version",
        default=int(row["version"] or 0),
        minimum=1,
    )
    if not int(row["version"] or 0):
        raise ValueError("Build the site first: there is nothing to publish yet.")
    store.version(connection, project_id, number)
    connection.execute(
        "UPDATE sb_projects SET published_version = ?, published_at = ?, updated_at = ? WHERE id = ?",
        (number, kit.now(), kit.now(), project_id),
    )
    link = public.create_link(connection, APP, "site", project_id, title=row["name"])
    kit.log(
        connection,
        APP,
        "site_published",
        f"Published {row['name']} (version {number})",
        ref_type="site",
        ref_id=project_id,
        actor=actor,
    )
    connection.commit()
    return {"version": number, "link": {**link, "url": site_path(link["token"])}}


def unpublish(connection: sqlite3.Connection, project_id: str) -> None:
    row = store.project(connection, project_id)
    public.revoke(connection, APP, "site", project_id)
    connection.execute(
        "UPDATE sb_projects SET published_version = NULL, published_at = NULL WHERE id = ?",
        (project_id,),
    )
    kit.log(
        connection,
        APP,
        "site_unpublished",
        f"Took {row['name']} offline",
        ref_type="site",
        ref_id=project_id,
    )
    connection.commit()


def _rewrite_root_links(text: str, depth: int) -> str:
    """``href="/styles.css"`` would leave the site's folder: make root links relative to the page."""
    prefix = "../" * depth
    text = re.sub(
        r"""(\b(?:href|src|action|poster)\s*=\s*["'])/(?!/)""",
        lambda m: m.group(1) + prefix,
        text,
    )
    return re.sub(r"""(url\(\s*["']?)/(?!/)""", lambda m: m.group(1) + prefix, text)


NOT_FOUND_PAGE = (
    "<!DOCTYPE html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
    "<title>Page not found</title><body style='font-family:system-ui;display:grid;place-items:center;min-height:90vh;"
    "color:#334155'><div style='text-align:center'><h1 style='font-size:3rem;margin:0'>404</h1><p>This page is not "
    "part of the site.</p><p><a href='{home}'>Go to the home page</a></p></div></body></html>"
)


def serve(
    connection: sqlite3.Connection,
    link: dict[str, Any],
    path: str,
    *,
    version: int | None = None,
) -> tuple[int, bytes, str]:
    """A file of a site for its preview or public link: (status, bytes, content type)."""
    project_id = str(link["ref_id"])
    row = store.project(connection, project_id)
    if link["kind"] == "site":
        number = row.get("published_version")
        if not number:
            raise LookupError("This site is not published.")
        files = store.version(connection, project_id, int(number))["files"]
    elif version is not None:
        files = store.version(connection, project_id, version)["files"]
    else:
        files = store.files(connection, project_id)
    raw = str(path or "").split("?", 1)[0].split("#", 1)[0]
    clean = raw.strip("/")
    if not clean or raw.endswith("/"):
        clean = f"{clean}/index.html".strip("/")
    if clean.startswith("assets/"):
        found = store.asset(connection, project_id, clean[7:])
        if found:
            return 200, base64.b64decode(found["data"]), found["mime"]
    if clean not in files and f"{clean}.html" in files:
        clean = f"{clean}.html"
    if ".." in clean.split("/") or clean not in files:
        depth = clean.count("/")
        return (
            404,
            NOT_FOUND_PAGE.replace("{home}", "../" * depth or "./").encode(),
            MIME["html"],
        )
    extension = clean.rsplit(".", 1)[-1].lower()
    text = files[clean]
    if extension in ("html", "htm", "css"):
        text = _rewrite_root_links(text, clean.count("/"))
    content_type = (
        MIME.get(extension)
        or mimetypes.guess_type(clean)[0]
        or "application/octet-stream"
    )
    if link["kind"] == "site" and extension in ("html", "htm"):
        connection.execute(
            "UPDATE suite_public_links SET views = views + 1, last_seen_at = ? WHERE token = ?",
            (kit.now(), link["token"]),
        )
        connection.commit()
    return 200, text.encode("utf-8"), content_type


def site_headers(origin: str, *, published: bool) -> tuple[tuple[str, str], ...]:
    """A site page runs its own scripts in a sandbox: an opaque origin, so it can never reach Monosage's session."""
    own = f" {origin}" if origin else ""
    policy = (
        "sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads; "
        f"default-src 'self'{own} https: data: blob:; "
        f"script-src 'self'{own} 'unsafe-inline' 'unsafe-eval' https: blob:; "
        f"style-src 'self'{own} 'unsafe-inline' https:; img-src 'self'{own} https: data: blob:; "
        f"font-src 'self'{own} https: data:; media-src 'self'{own} https: data: blob:; "
        f"connect-src 'self'{own} https:; frame-src https:; form-action 'self'{own} https: mailto:; base-uri 'self'{own}; "
        + ("frame-ancestors *" if published else "frame-ancestors 'self'")
    )
    headers = [
        ("Content-Security-Policy", policy),
        ("Cross-Origin-Resource-Policy", "cross-origin"),
        ("Access-Control-Allow-Origin", "*"),
        ("Cache-Control", "public, max-age=60" if published else "no-cache"),
    ]
    if not published:
        headers.append(("X-Frame-Options", "SAMEORIGIN"))
    return tuple(headers)


# ---------------------------------------------------------------------------
# runs: one request to the builder
# ---------------------------------------------------------------------------


class Progress:
    """Writes the streamed answer into the run row (at most every 0.3 s) and reads whether to stop."""

    def __init__(self, connect: Callable[[], sqlite3.Connection], run_id: str) -> None:
        self.connect = connect
        self.run_id = run_id
        self.reader = generator.Reader()
        self.pending = ""
        self.stop = False
        self.last = 0.0

    def feed(self, piece: str) -> None:
        self.reader.feed(piece)
        self.pending += piece
        if time.monotonic() - self.last >= PROGRESS_SECONDS:
            self.flush()

    def flush(self) -> None:
        self.last = time.monotonic()
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE sb_runs SET output = output || ?, phase = ?, current_file = ?, files_json = ? WHERE id = ?",
                (
                    self.pending,
                    self.reader.phase,
                    self.reader.current,
                    kit.dumps(self.reader.done),
                    self.run_id,
                ),
            )
            row = connection.execute(
                "SELECT cancel_requested FROM sb_runs WHERE id = ?", (self.run_id,)
            ).fetchone()
            connection.commit()
        self.pending = ""
        self.stop = bool(row and row[0])

    def should_stop(self) -> bool:
        return self.stop


def default_provider(connection: sqlite3.Connection) -> str:
    chosen = str(kit.get_setting(connection, APP, DEFAULT_PROVIDER_SETTING, "") or "")
    if chosen and chosen in {p.id for p in providers.offered()}:
        return chosen
    return "platform" if providers.hosted() else providers.TEMPLATE_ONLY


def start_run(
    connection: sqlite3.Connection,
    secret_store: Any,
    project_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], providers.Config, dict[str, Any]]:
    """Check the request, record the person's message and a running run; returns (project, config, context)."""
    row = store.project(connection, project_id)
    prompt = kit.text(
        payload.get("prompt"),
        "what to build",
        max_len=8000,
        required=True,
        multiline=True,
    )
    provider_id = str(
        payload.get("provider") or row["provider"] or default_provider(connection)
    )
    config = providers.load_config(
        connection, secret_store, provider_id, model=str(payload.get("model") or "")
    )
    if config.provider.needs_key and not config.key:
        raise ValueError(
            f"Add your {config.provider.label} key in Models first, or pick another model."
        )
    run_id = str(payload.get("run_id") or "")
    if not _HEX32.fullmatch(run_id):
        run_id = kit.new_id()
    if kit.fetch_one(connection, "SELECT id FROM sb_runs WHERE id = ?", (run_id,)):
        raise ValueError("That build was already started.")
    if _running(connection, project_id):
        raise ValueError(
            "A build is already running for this site. Wait for it or stop it first."
        )
    if "stack" in payload:
        stack = kit.choice(
            payload.get("stack"), "the stack", STACK_CHOICES, default=row["stack"]
        )
        connection.execute(
            "UPDATE sb_projects SET stack = ? WHERE id = ?", (stack, project_id)
        )
        row["stack"] = stack
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sb_runs (id, project_id, status, phase, provider, model, prompt, started_at)
        VALUES (?, ?, 'running', 'thinking', ?, ?, ?, ?)""",
        (run_id, project_id, config.provider.id, config.model, prompt, stamp),
    )
    store.add_message(
        connection,
        project_id,
        "user",
        prompt,
        run_id=run_id,
        meta={"provider": config.provider.id, "model": config.model},
    )
    connection.execute(
        "UPDATE sb_projects SET provider = ?, model = ?, updated_at = ? WHERE id = ?",
        (config.provider.id, config.model, stamp, project_id),
    )
    connection.commit()
    context = {
        "run_id": run_id,
        "prompt": prompt,
        "files": store.files(connection, project_id),
        "history": store.messages(connection, project_id, limit=12)[:-1],
        "assets": store.assets(connection, project_id),
        "spec": kit.loads(row["spec_json"], {}),
    }
    return row, config, context


def _designer_text(
    row: dict[str, Any], context: dict[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    """The offline designer's answer (tagged like a model's) and the new spec, or (message, None) for no change."""
    files, spec, prompt = context["files"], context["spec"], context["prompt"]
    if not files:
        spec = designer.spec_from_prompt(prompt)
        if row["name"] and row["name"] != "Untitled site":
            spec["name"] = row["name"]
        rendered = designer.render(spec)
        labels = ", ".join(designer.SECTION_LABELS[s].lower() for s in spec["sections"])
        plan = f"Building a {spec['kind']} site for {spec['name']} with {labels}."
        summary = (
            f"Built {spec['name']}: a responsive one-page site with {labels}. Try “make it dark”, “use green”, "
            "“add a pricing section” or “headline: …”, or connect a model in Models to ask for anything."
        )
        note = (
            ""
            if row["stack"] == "html"
            else " The offline designer writes plain HTML and CSS; a model follows the stack you picked."
        )
        return designer.answer(spec, rendered, plan=plan, summary=summary + note), spec
    if not spec:
        return (
            "The offline designer can only change sites it built itself. Pick a model (Ollama, Claude, Gemini, OpenAI…) "
            "in the menu under the message box to change this one.",
            None,
        )
    updated, notes = designer.change(spec, prompt)
    if not notes:
        return (
            "I could not work out a change from that without a model. Offline I can change colors (“use teal”), "
            "the theme (“make it dark”), sections (“add a FAQ”, “remove pricing”), the name (“call it Nova”), the "
            "headline (“headline: Fresh bread daily”), the button and the font. Connect a model in Models for "
            "anything else.",
            None,
        )
    rendered = designer.render(updated)
    return designer.answer(
        updated, rendered, plan=" ".join(notes), summary=" ".join(notes), previous=files
    ), updated


def run_build(
    connect: Callable[[], sqlite3.Connection],
    row: dict[str, Any],
    config: providers.Config,
    context: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    """Ask the model (streaming into the run), then write the files and make a version."""
    run_id, project_id = context["run_id"], row["id"]
    progress = Progress(connect, run_id)
    started = time.monotonic()
    status, error, text, new_spec = "done", "", "", None
    no_change_reply = ""
    try:
        if config.provider.kind == "builtin":
            text, new_spec = _designer_text(row, context)
            if new_spec is None:
                no_change_reply, text = text, ""
            for start in range(0, len(text), 1500):
                progress.feed(text[start : start + 1500])
                if progress.should_stop():
                    raise providers.Stopped
        else:
            text = providers.stream(
                config,
                generator.system_prompt(row["stack"]),
                generator.user_prompt(
                    context["prompt"],
                    files=context["files"],
                    history=context["history"],
                    assets=context["assets"],
                    project_name=row["name"],
                ),
                progress.feed,
                progress.should_stop,
            )
    except providers.Stopped:
        status, error = "cancelled", "Stopped."
    except providers.ModelError as failure:
        status, error = "failed", str(failure)
    except Exception as failure:  # noqa: BLE001 - the run row must end; the log keeps the details
        suite.logger.exception("A Sites build failed")
        status, error = "failed", f"The build failed: {type(failure).__name__}."
    if progress.pending:
        try:
            progress.flush()
        except sqlite3.Error:
            pass
    text = text or progress.reader.text
    seconds = round(time.monotonic() - started, 1)
    result = generator.parse(text) if status != "failed" or text else generator.Result()
    with closing(connect()) as connection:
        store.ensure(connection)
        number: int | None = None
        changed: list[str] = []
        if status == "cancelled" and not result.files:
            reply = "Stopped. Nothing was changed."
        elif no_change_reply:
            reply = no_change_reply
        elif status == "failed" and not result.files:
            reply = error
        elif not result.files and not result.deleted:
            status, error = "failed", "The model answered without any files."
            reply = (
                "The model answered, but did not write any files. Try again, ask more simply, or pick a stronger "
                "model (a coding model works best)."
            )
        else:
            current = store.files(connection, project_id)
            deleted = [
                p
                for p in result.deleted
                if p in current and p not in result.files and p != "index.html"
            ]
            try:
                store.write_files(connection, project_id, result.files, deleted)
            except ValueError as refused:
                status, error, reply = "failed", str(refused), str(refused)
            else:
                changed = sorted(
                    {p for p, t in result.files.items() if current.get(p) != t}
                    | set(deleted)
                )
                if new_spec is not None:
                    connection.execute(
                        "UPDATE sb_projects SET spec_json = ? WHERE id = ?",
                        (kit.dumps(new_spec), project_id),
                    )
                elif current and row.get("spec_json") not in (None, "", "{}"):
                    connection.execute(
                        "UPDATE sb_projects SET spec_json = '{}' WHERE id = ?",
                        (project_id,),
                    )  # a model rewrote it
                label = (result.plan or context["prompt"])[:120]
                number = snapshot(
                    connection,
                    project_id,
                    label=label,
                    source="ai",
                    changed=changed,
                    run_id=run_id,
                )
                reply = (
                    result.summary
                    or result.plan
                    or f"Updated {', '.join(changed) or 'the site'}."
                )
                if status == "cancelled":
                    reply = f"Stopped. Kept the files that were finished: {', '.join(changed)}."
                if status == "failed":
                    reply = f"{error} Kept the files that were finished: {', '.join(changed)}."
        meta = {
            "provider": config.provider.id,
            "provider_label": config.provider.label,
            "model": config.model,
            "seconds": seconds,
            "changed": changed,
            "deleted": [p for p in result.deleted if p in changed],
            "skipped": result.skipped,
            "format": result.format,
            "plan": result.plan,
            "status": status,
            "error": error,
        }
        message = store.add_message(
            connection,
            project_id,
            "assistant",
            reply,
            run_id=run_id,
            version=number,
            meta=meta,
        )
        connection.execute(
            "UPDATE sb_runs SET status = ?, phase = 'done', current_file = '', error = ?, version = ?, finished_at = ? WHERE id = ?",
            (status, error[:1000], number, kit.now(), run_id),
        )
        if number:
            kit.log(
                connection,
                APP,
                "site_built",
                f"Built {row['name']} (version {number})",
                detail=", ".join(changed)[:400],
                ref_type="site",
                ref_id=project_id,
                actor=actor,
            )
        connection.commit()
        return {
            "run": public_run(store.run(connection, run_id)),
            "message": message,
            "version": number,
            "project": get_project(connection, project_id),
        }


def public_run(row: dict[str, Any], *, since: int = 0) -> dict[str, Any]:
    output = str(row.get("output") or "")
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "status": row["status"],
        "phase": row["phase"],
        "provider": row["provider"],
        "model": row["model"],
        "current_file": row["current_file"],
        "files": row.get("files") or [],
        "error": row["error"],
        "version": row["version"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "length": len(output),
        "output": output[since:] if since < len(output) else "",
        "from": since,
    }


def cancel_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = store.run(connection, run_id)
    if row["status"] == "running":
        connection.execute(
            "UPDATE sb_runs SET cancel_requested = 1 WHERE id = ?", (run_id,)
        )
        connection.commit()
    return public_run(store.run(connection, run_id))


# ---------------------------------------------------------------------------
# money, search and the hub
# ---------------------------------------------------------------------------


def mark_paid(
    connection: sqlite3.Connection, project_id: str, paid: bool
) -> dict[str, Any]:
    row = store.project(connection, project_id)
    if paid:
        if int(row["price_cents"] or 0) <= 0:
            raise ValueError("Set the price the client pays first (Project details).")
        kit.record_revenue(
            connection,
            APP,
            source_type="site",
            source_id=project_id,
            amount_cents=int(row["price_cents"]),
            label=f"Website: {row['name']}",
            customer=row["client"] or "",
        )
        connection.execute(
            "UPDATE sb_projects SET paid_at = ? WHERE id = ?", (kit.now(), project_id)
        )
        kit.log(
            connection,
            APP,
            "site_paid",
            f"{row['client'] or 'Client'} paid for {row['name']}",
            ref_type="site",
            ref_id=project_id,
            amount_cents=int(row["price_cents"]),
        )
    else:
        kit.void_revenue(connection, APP, "site", project_id)
        connection.execute(
            "UPDATE sb_projects SET paid_at = NULL WHERE id = ?", (project_id,)
        )
    connection.commit()
    return get_project(connection, project_id)


def search(connection: sqlite3.Connection, q: str, *, limit: int = 8) -> dict[str, Any]:
    text = str(q or "").strip()[:100]
    if not text:
        return {"projects": [], "messages": []}
    like = f"%{text}%"
    projects = kit.fetch_all(
        connection,
        "SELECT id, name, description, client, updated_at FROM sb_projects WHERE name LIKE ? OR description LIKE ? OR client LIKE ? ORDER BY updated_at DESC LIMIT ?",
        (like, like, like, limit),
    )
    messages = kit.fetch_all(
        connection,
        """SELECT m.project_id, m.content, m.created_at, p.name FROM sb_messages m JOIN sb_projects p ON p.id = m.project_id
        WHERE m.role = 'user' AND m.content LIKE ? ORDER BY m.created_at DESC LIMIT ?""",
        (like, limit),
    )
    for message in messages:
        message["content"] = message["content"][:200]
    return {"projects": projects, "messages": messages}


def overview(connection: sqlite3.Connection) -> dict[str, Any]:
    month = kit.today()[:7]
    return {
        "sites": kit.count(connection, "SELECT COUNT(*) FROM sb_projects"),
        "published": kit.count(
            connection,
            "SELECT COUNT(*) FROM sb_projects WHERE published_version IS NOT NULL",
        ),
        "builds_this_month": kit.count(
            connection,
            "SELECT COUNT(*) FROM sb_runs WHERE status = 'done' AND started_at LIKE ?",
            (f"{month}%",),
        ),
        "paid_cents": kit.count(
            connection,
            "SELECT COALESCE(SUM(price_cents), 0) FROM sb_projects WHERE paid_at IS NOT NULL",
        ),
        "unpaid_cents": kit.count(
            connection,
            "SELECT COALESCE(SUM(price_cents), 0) FROM sb_projects WHERE paid_at IS NULL AND price_cents > 0",
        ),
        "default_provider": default_provider(connection),
    }


def bundle_for(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    with_kit: bool,
    target: str,
    version: int | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    row = store.project(connection, project_id)
    files = (
        store.version(connection, project_id, version)["files"]
        if version
        else store.files(connection, project_id)
    )
    if not files:
        raise ValueError("Build the site first: it has no files yet.")
    return row, deploy.bundle(
        files,
        _asset_bytes(connection, project_id),
        name=row["name"],
        with_kit=with_kit,
        target=target,
    )


def record_deploy(
    connection: sqlite3.Connection,
    project_id: str,
    target: str,
    status: str,
    *,
    url: str = "",
    detail: str = "",
    version: int | None = None,
    actor: str = "",
    remember: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deploy_id = kit.new_id()
    connection.execute(
        "INSERT INTO sb_deploys (id, project_id, target, status, url, detail, version, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            deploy_id,
            project_id,
            target,
            status,
            url[:500],
            detail[:1000],
            version,
            actor[:120],
            kit.now(),
        ),
    )
    if remember:
        row = store.project(connection, project_id)
        saved = kit.loads(row.get("deploy_json"), {})
        saved[target] = {**(saved.get(target) or {}), **remember}
        connection.execute(
            "UPDATE sb_projects SET deploy_json = ? WHERE id = ?",
            (kit.dumps(saved), project_id),
        )
    if status == "done":
        kit.log(
            connection,
            APP,
            "site_deployed",
            f"Deployed to {deploy.TARGET_LABELS.get(target, target)}",
            detail=url,
            ref_type="site",
            ref_id=project_id,
            actor=actor,
        )
    connection.commit()
    return {
        "id": deploy_id,
        "target": target,
        "status": status,
        "url": url,
        "detail": detail,
    }
