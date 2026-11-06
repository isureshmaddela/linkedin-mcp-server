"""Bring a single-user Monosage installation into Monosage Cloud: ``python -m monosage_cloud import-local``.

The single-user build keeps one SQLite database per local account (``~/.linkedin-mcp/auto_apply``). Monosage Cloud
keeps the same records in two places: the member's own workspace (the careers records: profile, jobs, applications,
interviews, inbox, documents) and the organization's database (the apps: Life, Planner, AI, Sites, News, Stocks...,
each person's rows scoped by their profile). The import puts each local table where Monosage Cloud reads it:

* a new workspace becomes a copy of the local database (so does one in use, with ``replace=True``); a workspace in
  use gets the local rows added, keeping its own where both have the same record. Owner ids move to the member, and
  the migrations bring it up to date;
* documents are decrypted with the local key and encrypted again with the organization's data key;
* the apps' tables are added to the organization's database (rows already there are kept, so a second run adds
  nothing twice). The local owner's profile becomes the member's profile.

Not brought over, because they only work where they were made: keys and passwords encrypted with the local key
(SerpApi and Anthropic keys, Workday sign-ins), the API console's history, shared public links, and the Social and
Videos accounts (their own databases). The import reports each of them.

The member must already have a workspace: a candidate gets one when invited, an owner or admin starts theirs in the
account menu (that is where they agree to automation on their own behalf), so the import never gives that consent.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken

from aira.documents import LocalBlobBackend
from monosage_cloud.db import one
from monosage_cloud.errors import NotFound, ValidationFailed

if TYPE_CHECKING:
    from monosage_cloud.app import CloudApp

# Encrypted with the local installation's key: unreadable anywhere else, so they are left behind (and cleared from
# the workspace copy) and the person enters them again.
SECRET_TABLES = ("airaapi_secrets", "workday_accounts")
# The API console's history, cache and keys belong to the installation; public links are indexed by it.
SKIPPED_PREFIXES = ("airaapi_",)
SKIPPED_TABLES = ("suite_public_links",)
# Careers records that say whose they are with the local account id.
OWNER_COLUMNS = ("owner_id", "user_id")


@dataclass
class Report:
    email: str
    org_id: str
    member_id: str
    mode: str = ""
    workspace_tables: int = 0
    workspace_rows: int = 0
    documents: int = 0
    documents_missing: int = 0
    app_tables: int = 0
    app_rows: int = 0
    profile: str = ""
    left_behind: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "org_id": self.org_id,
            "member_id": self.member_id,
            "workspace": {
                "mode": self.mode,
                "tables": self.workspace_tables,
                "rows": self.workspace_rows,
            },
            "documents": {"copied": self.documents, "missing": self.documents_missing},
            "apps": {"tables": self.app_tables, "rows_added": self.app_rows},
            "profile": self.profile,
            "left_behind": self.left_behind,
        }


def default_source() -> Path:
    """Where the single-user build keeps its data (the same default as ``auto_apply_assistant.DB_PATH``)."""
    configured = os.getenv("AUTO_APPLY_DB_PATH", "").strip()
    if configured:
        return Path(configured).parent
    return Path.home() / ".linkedin-mcp" / "auto_apply"


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15
    )
    connection.row_factory = sqlite3.Row
    return connection


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def stored_file_name(stored: str) -> str:
    """The file name in a path the single-user build stored, whichever system wrote it.

    A Windows installation stores ``D:\\...`` or ``D:/...``; read on Linux, ``Path`` would treat the whole string as
    one name. Backslashes or a drive letter mean a Windows path; anything else is a POSIX one.
    """
    if "\\" in stored or _WINDOWS_DRIVE.match(stored):
        return PureWindowsPath(stored).name
    return PurePosixPath(stored).name


def _local_account(source: Path, email: str) -> tuple[str, Path]:
    """The local account's id and database. The path is taken under ``source`` so a moved folder still works."""
    accounts = source / "accounts.sqlite3"
    if not accounts.is_file():
        raise NotFound(
            f"No Monosage installation at {source} (accounts.sqlite3 is missing)."
        )
    connection = _read_only(accounts)
    try:
        row = connection.execute(
            "SELECT user_id, data_path FROM users WHERE lower(email) = lower(?)",
            (email,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise NotFound(f"No local account for {email} at {source}.")
    name = stored_file_name(str(row["data_path"]))
    path = (
        source / name if name == "applications.sqlite3" else source / "accounts" / name
    )
    if not path.is_file():
        raise NotFound(f"The local account's database is missing: {path}.")
    return str(row["user_id"]), path


def _member(app: CloudApp, email: str, org_id: str) -> dict[str, Any]:
    with app.db.read() as connection:
        rows = connection.execute(
            "SELECT member_id, org_id, role, status FROM members WHERE lower(email) = lower(?)",
            (email,),
        ).fetchall()
    rows = [dict(row) for row in rows if row["status"] == "active"]
    if org_id:
        rows = [row for row in rows if row["org_id"] == org_id]
    if not rows:
        raise NotFound(
            f"No active Monosage Cloud member {email}"
            + (f" in {org_id}." if org_id else ".")
        )
    if len(rows) > 1:
        choices = ", ".join(row["org_id"] for row in rows)
        raise ValidationFailed(
            f"{email} belongs to several organizations; choose one with --org ({choices})."
        )
    return rows[0]


def _snapshot(path: Path, into: Path) -> None:
    """A consistent copy, even while the single-user build is running and writing."""
    source = _read_only(path)
    target = sqlite3.connect(into)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def _core_tables() -> set[str]:
    """The tables the careers migrations create: they stay in the workspace. Every other table is an app's."""
    from aira.schema import migrate

    connection = sqlite3.connect(":memory:")
    try:
        migrate(connection)
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()


def _tables(connection: sqlite3.Connection) -> dict[str, str]:
    """Ordinary tables and their CREATE statements; virtual (search) tables and their shadow tables are left out."""
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    virtual = [
        name
        for name, sql in rows
        if str(sql or "").upper().startswith("CREATE VIRTUAL")
    ]
    return {
        name: sql
        for name, sql in rows
        if name not in virtual and not any(name.startswith(f"{v}_") for v in virtual)
    }


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _workspace_is_empty(path: Path) -> bool:
    if not path.is_file():
        return True
    connection = sqlite3.connect(path)
    try:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        return all(
            _count(connection, table) == 0
            for table in ("jobs", "applications", "documents", "settings")
            if table in names
        )
    finally:
        connection.close()


def _prepare(snapshot: Path, local_id: str, member_id: str) -> None:
    """In the copy: the member owns what the local account owned, and the locally encrypted secrets are gone."""
    connection = sqlite3.connect(snapshot)
    try:
        for table in _tables(connection):
            columns = _columns(connection, table)
            for column in OWNER_COLUMNS:
                if column in columns:
                    connection.execute(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                        (member_id, local_id),
                    )
            if table in SECRET_TABLES:
                connection.execute(f'DELETE FROM "{table}"')
        connection.commit()
    finally:
        connection.close()


def _copy_rows(
    source: sqlite3.Connection, target: sqlite3.Connection, table: str, create: str
) -> int:
    """Add ``table``'s rows to ``target`` (making the table and its indexes if missing); rows already there win."""
    if table not in _tables(target):
        target.execute(create)
        for (index_sql,) in source.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
            (table,),
        ):
            target.execute(
                index_sql.replace(
                    "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1
                ).replace(
                    "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1
                )
            )
    present = set(_columns(target, table))
    shared = [column for column in _columns(source, table) if column in present]
    if not shared:
        return 0
    names = ", ".join(f'"{column}"' for column in shared)
    marks = ", ".join("?" for _ in shared)
    before = target.total_changes
    target.executemany(
        f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({marks})',
        source.execute(f'SELECT {names} FROM "{table}"'),
    )
    return target.total_changes - before


def _fill_workspace(
    snapshot: Path, workspace: Path, *, merge: bool, report: Report
) -> None:
    """A new workspace (or ``replace``) becomes the local database; one in use gets the local rows added."""
    source = sqlite3.connect(snapshot)
    target = sqlite3.connect(workspace, timeout=15)
    try:
        target.execute("PRAGMA busy_timeout = 10000")
        if not merge:
            source.backup(target)
            for table in _tables(target):
                report.workspace_tables += 1
                report.workspace_rows += _count(target, table)
            return
        for table, create in _tables(source).items():
            if table == "schema_migrations" or not _count(source, table):
                continue
            added = _copy_rows(source, target, table, create)
            if added:
                report.workspace_tables += 1
                report.workspace_rows += added
        target.commit()
    finally:
        source.close()
        target.close()


def _copy_documents(
    app: CloudApp,
    source: Path,
    snapshot: Path,
    local_id: str,
    org_id: str,
    member_id: str,
    report: Report,
) -> None:
    connection = sqlite3.connect(snapshot)
    try:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "documents" not in names:
            return
        ids = [
            row[0]
            for row in connection.execute(
                # The copy already names the member; the local files are still under the local account's folder.
                "SELECT document_id FROM documents WHERE owner_id = ? AND deleted_at IS NULL",
                (member_id,),
            )
        ]
    finally:
        connection.close()
    if not ids:
        return
    override = os.getenv("AIRA_DOCUMENT_KEY", "").strip()
    key_path = source / "documents.key"
    if not override and not key_path.is_file():
        report.documents_missing += len(ids)
        report.left_behind.append("documents (the local documents.key is missing)")
        return
    local = Fernet(
        override.encode("ascii") if override else key_path.read_bytes().strip()
    )
    blobs = LocalBlobBackend(source / "documents")
    hosted_backend = app.storage.backend(org_id)
    hosted_key = app.secrets.fernet(org_id)
    for document_id in ids:
        try:
            plaintext = local.decrypt(blobs.get(local_id, document_id))
        except (OSError, InvalidToken):
            report.documents_missing += 1
            continue
        hosted_backend.put(member_id, document_id, hosted_key.encrypt(plaintext))
        report.documents += 1


def _merge_apps(
    snapshot: Path, org_db: Path, core: set[str], report: Report
) -> list[str]:
    """Add the apps' tables and rows to the organization's database; rows already there win."""
    org_db.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(snapshot)
    target = sqlite3.connect(org_db, timeout=15)
    added: list[str] = []
    try:
        target.execute("PRAGMA busy_timeout = 10000")
        for table, create in _tables(source).items():
            if table in core or table in SECRET_TABLES or table in SKIPPED_TABLES:
                continue
            if table.startswith(SKIPPED_PREFIXES):
                continue
            if not _count(source, table):
                continue
            rows = _copy_rows(source, target, table, create)
            if rows:
                report.app_tables += 1
                report.app_rows += rows
                added.append(table)
        target.commit()
    finally:
        source.close()
        target.close()
    return added


def _replace_text(
    connection: sqlite3.Connection, old: str, new: str, tables: list[str]
) -> None:
    """Point every imported row that names ``old`` (a profile id) at ``new``."""
    for table in tables:
        for column in _columns(connection, table):
            connection.execute(
                f'UPDATE OR IGNORE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                (new, old),
            )


def _attach_profile(
    snapshot: Path, org_db: Path, member_id: str, tables: list[str], report: Report
) -> None:
    """The local owner's profile becomes the member's: their own if they already have one, else the local one."""
    source = sqlite3.connect(snapshot)
    source.row_factory = sqlite3.Row
    try:
        names = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "life_profiles" not in names:
            return
        local = source.execute(
            "SELECT * FROM life_profiles WHERE archived_at IS NULL ORDER BY owner DESC, created_at LIMIT 1"
        ).fetchone()
    finally:
        source.close()
    if local is None:
        return
    connection = sqlite3.connect(org_db, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        mine = connection.execute(
            "SELECT * FROM life_profiles WHERE member_ref = ?", (member_id,)
        ).fetchone()
        imported = [table for table in tables if table != "life_profiles"]
        if mine is not None and mine["id"] != local["id"]:
            _replace_text(connection, local["id"], mine["id"], imported)
            connection.execute(
                "DELETE FROM life_profiles WHERE id = ? AND member_ref IS NULL",
                (local["id"],),
            )
            if not mine["birthday"] and local["birthday"]:
                connection.execute(
                    "UPDATE life_profiles SET birthday = ? WHERE id = ?",
                    (local["birthday"], mine["id"]),
                )
            report.profile = "joined to your Monosage Cloud profile"
        else:
            connection.execute(
                "UPDATE life_profiles SET member_ref = ? WHERE id = ?",
                (member_id, local["id"]),
            )
            report.profile = "your local profile is now your Monosage Cloud profile"
        # The people of a household who had no sign-in come over as people, never as the organization's owner.
        connection.execute(
            "UPDATE life_profiles SET owner = 0 WHERE member_ref IS NULL"
        )
        connection.commit()
    finally:
        connection.close()


def _note_left_behind(snapshot: Path, report: Report) -> None:
    reader = sqlite3.connect(snapshot)
    try:
        present = _tables(reader)
        notes = {
            "airaapi_secrets": "saved keys (SerpApi, Anthropic): enter them again in Settings",
            "workday_accounts": "Workday sign-ins: add them again in Settings",
            "suite_public_links": "shared public links: share those pages again",
        }
        for table, note in notes.items():
            if table in present and _count(reader, table):
                report.left_behind.append(note)
        if any(
            name.startswith(SKIPPED_PREFIXES)
            and name not in notes
            and _count(reader, name)
            for name in present
        ):
            report.left_behind.append("the API console's search history and cache")
    finally:
        reader.close()
    report.left_behind.append("Social and Videos accounts (they keep their own data)")


def run(
    app: CloudApp,
    *,
    email: str,
    source: Path | None = None,
    org_id: str = "",
    replace: bool = False,
) -> dict[str, Any]:
    source = Path(source) if source else default_source()
    local_id, local_db = _local_account(source, email)
    member = _member(app, email, org_id)
    org_id, member_id = member["org_id"], member["member_id"]
    report = Report(email=email, org_id=org_id, member_id=member_id)
    with app.db.read() as connection:
        workspace_row = one(
            connection,
            "SELECT data_path FROM workspaces WHERE candidate_id = ? AND org_id = ?",
            (member_id, org_id),
        )
    if workspace_row is None:
        raise ValidationFailed(
            "This member has no workspace yet. An owner or admin starts one in Monosage Cloud (account menu > "
            "My workspace); then run the import again."
        )
    workspace = Path(workspace_row["data_path"])
    merge = not replace and not _workspace_is_empty(workspace)
    report.mode = "added to the workspace" if merge else "copied into the workspace"
    core = _core_tables()
    with tempfile.TemporaryDirectory(prefix="monosage-import-") as scratch:
        snapshot = Path(scratch) / "local.sqlite3"
        _snapshot(local_db, snapshot)
        _note_left_behind(snapshot, report)
        _prepare(snapshot, local_id, member_id)
        _fill_workspace(snapshot, workspace, merge=merge, report=report)
        app.workspaces._migrate(workspace)
        _copy_documents(app, source, snapshot, local_id, org_id, member_id, report)
        org_db = app.workspaces.org_api_path(org_id)
        added = _merge_apps(snapshot, org_db, core, report)
        _attach_profile(snapshot, org_db, member_id, added, report)
    app.workspaces.invalidate_snapshot(org_id, member_id)
    app.audit.record(
        org_id,
        "workspace.import_local",
        actor_type="system",
        actor_label="import-local",
        candidate_id=member_id,
        target_type="member",
        target_id=member_id,
        detail={
            "workspace_rows": report.workspace_rows,
            "documents": report.documents,
            "app_rows": report.app_rows,
            "merge": merge,
        },
        source="cli",
    )
    return report.as_dict()
