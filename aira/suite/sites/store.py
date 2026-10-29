"""Sites tables (prefixed ``sb_``: site builder).

A project's working copy is ``sb_files`` (text files) plus ``sb_assets`` (uploaded images, base64 so both database
engines keep them alike). Every change that matters makes a row in ``sb_versions`` holding all text files as JSON,
so a version can be previewed, restored or deployed on its own.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from aira.suite import kit

APP = "sites"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sb_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        stack TEXT NOT NULL DEFAULT 'html',
        provider TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        spec_json TEXT NOT NULL DEFAULT '{}',
        version INTEGER NOT NULL DEFAULT 0,
        published_version INTEGER,
        published_at TEXT,
        client TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL DEFAULT 0,
        paid_at TEXT,
        color TEXT NOT NULL DEFAULT '',
        deploy_json TEXT NOT NULL DEFAULT '{}',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sb_projects_updated ON sb_projects (updated_at)",
    """CREATE TABLE IF NOT EXISTS sb_files (
        project_id TEXT NOT NULL,
        path TEXT NOT NULL,
        content TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (project_id, path)
    )""",
    """CREATE TABLE IF NOT EXISTS sb_assets (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        name TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS sb_assets_name ON sb_assets (project_id, name)",
    """CREATE TABLE IF NOT EXISTS sb_versions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        number INTEGER NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'ai',
        files_json TEXT NOT NULL,
        changed_json TEXT NOT NULL DEFAULT '[]',
        run_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS sb_versions_number ON sb_versions (project_id, number)",
    """CREATE TABLE IF NOT EXISTS sb_messages (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        run_id TEXT NOT NULL DEFAULT '',
        version INTEGER,
        meta_json TEXT NOT NULL DEFAULT '{}',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sb_messages_project ON sb_messages (project_id, position)",
    """CREATE TABLE IF NOT EXISTS sb_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        phase TEXT NOT NULL DEFAULT 'thinking',
        provider TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        prompt TEXT NOT NULL,
        output TEXT NOT NULL DEFAULT '',
        current_file TEXT NOT NULL DEFAULT '',
        files_json TEXT NOT NULL DEFAULT '[]',
        error TEXT NOT NULL DEFAULT '',
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        version INTEGER,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS sb_runs_project ON sb_runs (project_id, started_at)",
    """CREATE TABLE IF NOT EXISTS sb_providers (
        id TEXT PRIMARY KEY,
        base_url TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sb_deploys (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        target TEXT NOT NULL,
        status TEXT NOT NULL,
        url TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        version INTEGER,
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sb_deploys_project ON sb_deploys (project_id, created_at)",
)

# Paths a site may hold: relative, plain characters, no parent steps, a known text type.
TEXT_EXTENSIONS = (
    "html",
    "htm",
    "css",
    "js",
    "mjs",
    "jsx",
    "json",
    "svg",
    "txt",
    "md",
    "xml",
    "webmanifest",
    "map",
)
_PATH_RE = re.compile(r"[A-Za-z0-9_\-][A-Za-z0-9_\-./]{0,119}")
MAX_FILES = 60
MAX_FILE_CHARS = 600_000
MAX_SITE_CHARS = 3_000_000


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "sites.v1", SCHEMA)


def clean_path(value: Any) -> str:
    """A site file's path as stored (``about.html``, ``css/site.css``); ValueError for anything else."""
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.strip("/")
    while "//" in path:
        path = path.replace("//", "/")
    if (
        not path
        or not _PATH_RE.fullmatch(path)
        or ".." in path.split("/")
        or any(
            part.startswith(".") and part != ".well-known" for part in path.split("/")
        )
    ):
        raise ValueError(
            "A file name uses letters, numbers, dashes, dots and slashes, like about.html or css/site.css."
        )
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if extension not in TEXT_EXTENSIONS:
        raise ValueError(
            f"{path} is not a website text file. Use one of: .{', .'.join(TEXT_EXTENSIONS[:8])}."
        )
    return path


def project(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "sb_projects", project_id, "site")


def files(connection: sqlite3.Connection, project_id: str) -> dict[str, str]:
    rows = kit.fetch_all(
        connection,
        "SELECT path, content FROM sb_files WHERE project_id = ? ORDER BY path",
        (project_id,),
    )
    return {row["path"]: row["content"] for row in rows}


def write_files(
    connection: sqlite3.Connection,
    project_id: str,
    changed: dict[str, str],
    deleted: list[str] | tuple[str, ...] = (),
) -> None:
    """Write files into the working copy (whole files) and delete others; validated before anything changes."""
    current = files(connection, project_id)
    for path in deleted:
        current.pop(path, None)
    current.update(changed)
    if len(current) > MAX_FILES:
        raise ValueError(f"A site holds at most {MAX_FILES} files.")
    if sum(len(text) for text in current.values()) > MAX_SITE_CHARS:
        raise ValueError(
            "The site is too large (over 3 MB of text). Split it or remove files."
        )
    stamp = kit.now()
    for path in deleted:
        connection.execute(
            "DELETE FROM sb_files WHERE project_id = ? AND path = ?", (project_id, path)
        )
    for path, content in changed.items():
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f"{path} is too large (over 600 KB).")
        connection.execute(
            "DELETE FROM sb_files WHERE project_id = ? AND path = ?", (project_id, path)
        )
        connection.execute(
            "INSERT INTO sb_files (project_id, path, content, updated_at) VALUES (?, ?, ?, ?)",
            (project_id, path, content, stamp),
        )


def assets(connection: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT id, name, mime, size, created_at FROM sb_assets WHERE project_id = ? ORDER BY name",
        (project_id,),
    )


def asset(
    connection: sqlite3.Connection, project_id: str, name: str
) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection,
        "SELECT * FROM sb_assets WHERE project_id = ? AND name = ?",
        (project_id, name),
    )


def versions(connection: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT id, number, label, source, changed_json, run_id, created_at, updated_at
        FROM sb_versions WHERE project_id = ? ORDER BY number DESC""",
        (project_id,),
    )
    for row in rows:
        row["changed"] = kit.loads(row.pop("changed_json"), [])
    return rows


def version(
    connection: sqlite3.Connection, project_id: str, number: int
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM sb_versions WHERE project_id = ? AND number = ?",
        (project_id, number),
    )
    if not row:
        raise LookupError(f"Version {number} was not found.")
    row["files"] = kit.loads(row.pop("files_json"), {})
    row["changed"] = kit.loads(row.pop("changed_json"), [])
    return row


def messages(
    connection: sqlite3.Connection, project_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT * FROM (SELECT * FROM sb_messages WHERE project_id = ? ORDER BY position DESC LIMIT ?)
        AS recent ORDER BY position""",
        (project_id, limit),
    )
    for row in rows:
        row["meta"] = kit.loads(row.pop("meta_json"), {})
    return rows


def add_message(
    connection: sqlite3.Connection,
    project_id: str,
    role: str,
    content: str,
    *,
    run_id: str = "",
    version: int | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_id = kit.new_id()
    # Messages of the same second keep the order they were written in (a request and its answer often share one).
    position = (
        kit.count(
            connection,
            "SELECT COALESCE(MAX(position), 0) FROM sb_messages WHERE project_id = ?",
            (project_id,),
        )
        + 1
    )
    connection.execute(
        """INSERT INTO sb_messages (id, project_id, role, content, run_id, version, meta_json, position, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id,
            project_id,
            role,
            content,
            run_id,
            version,
            kit.dumps(meta or {}),
            position,
            kit.now(),
        ),
    )
    return {
        "id": message_id,
        "project_id": project_id,
        "role": role,
        "content": content,
        "run_id": run_id,
        "version": version,
        "meta": meta or {},
    }


def run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM sb_runs WHERE id = ?", (run_id,))
    if not row:
        raise LookupError("That run was not found.")
    row["files"] = kit.loads(row.pop("files_json"), [])
    return row


def delete_project(connection: sqlite3.Connection, project_id: str) -> None:
    for table in (
        "sb_files",
        "sb_assets",
        "sb_versions",
        "sb_messages",
        "sb_runs",
        "sb_deploys",
    ):
        connection.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))  # noqa: S608 - fixed names
    connection.execute("DELETE FROM sb_projects WHERE id = ?", (project_id,))
