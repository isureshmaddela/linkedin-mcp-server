"""SQLite persistence for the API console.

Every function takes the connection to the signed-in user's database (or, for
the key index, to the accounts database) so the module stays testable without
the HTTP server. Search results are stored zlib-compressed; the SerpApi key is
stored only inside :class:`SecretStore` as Fernet ciphertext. Nothing here ever
receives the plaintext key except ``SecretStore.put``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import uuid
import zlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

SEARCH_COLUMNS = (
    "id",
    "serpapi_id",
    "engine",
    "params_json",
    "params_hash",
    "status",
    "source",
    "label",
    "created_at",
    "duration_ms",
    "http_status",
    "error",
    "note",
    "result_count",
    "result_bytes",
    "cache_hit",
    "credits",
    "pinned",
    "output",
    "options_json",
    "api_key_id",
    "result_truncated",
)
_SUMMARY_SQL = ", ".join(SEARCH_COLUMNS) + (
    ", (result_blob IS NOT NULL) AS has_result, (html_blob IS NOT NULL) AS has_html"
)
MUTABLE_SEARCH_FIELDS = frozenset(
    {
        "serpapi_id",
        "status",
        "duration_ms",
        "http_status",
        "error",
        "note",
        "result_count",
        "result_bytes",
        "credits",
        "pinned",
        "label",
        "result_truncated",
    }
)


class SecretError(ValueError):
    """A stored secret cannot be decrypted (the key file changed or data is damaged)."""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the API tables. Additive and idempotent."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_searches (
            id TEXT PRIMARY KEY, serpapi_id TEXT NOT NULL DEFAULT '',
            engine TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
            params_hash TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ok',
            source TEXT NOT NULL DEFAULT 'playground', label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, duration_ms INTEGER NOT NULL DEFAULT 0,
            http_status INTEGER, error TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '', result_count INTEGER NOT NULL DEFAULT 0,
            result_bytes INTEGER NOT NULL DEFAULT 0, result_blob BLOB, html_blob BLOB,
            cache_hit INTEGER NOT NULL DEFAULT 0, credits INTEGER NOT NULL DEFAULT 1,
            pinned INTEGER NOT NULL DEFAULT 0, output TEXT NOT NULL DEFAULT 'json',
            options_json TEXT NOT NULL DEFAULT '{}', api_key_id TEXT NOT NULL DEFAULT '',
            result_truncated INTEGER NOT NULL DEFAULT 0
        )"""
    )
    for name, columns in (
        ("created", "created_at"),
        ("engine", "engine, created_at"),
        ("hash", "params_hash, created_at"),
        ("status", "status, created_at"),
        ("key", "api_key_id, created_at"),
    ):
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_airaapi_searches_{name} ON airaapi_searches({columns})"
        )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_saved (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, engine TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}', tags TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL, last_run_at TEXT, last_search_id TEXT,
            pinned INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_monitors (
            id TEXT PRIMARY KEY, saved_id TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at TEXT NOT NULL, last_run_at TEXT, last_search_id TEXT,
            notify_on_change INTEGER NOT NULL DEFAULT 1,
            last_change_summary TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_airaapi_monitors_due ON airaapi_monitors(enabled, next_run_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_usage (
            day TEXT NOT NULL, engine TEXT NOT NULL, source TEXT NOT NULL,
            searches INTEGER NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0, credits INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, engine, source)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_secrets (
            name TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_api_keys (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
            last_used_at TEXT, revoked_at TEXT, hourly_limit INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_cache (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at TEXT NOT NULL
        )"""
    )
    # Ids of searches that were deleted or pruned, so an archive lookup can answer
    # 410 (gone) like SerpApi instead of 404 for a month after the fact.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_gone (
            id TEXT PRIMARY KEY, serpapi_id TEXT NOT NULL DEFAULT '',
            gone_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_airaapi_gone_serpapi ON airaapi_gone(serpapi_id)"
    )
    # X-Ray: the pixel-position copy of an archived search, fetched once on request.
    _add_column(connection, "airaapi_searches", "pixel_blob", "BLOB")
    # Monitor budgets: a cap on live searches per calendar month (0 = none), the
    # month the counter belongs to, and whether a failed run pauses the monitor.
    _add_column(
        connection, "airaapi_monitors", "monthly_budget", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(
        connection, "airaapi_monitors", "budget_month", "TEXT NOT NULL DEFAULT ''"
    )
    _add_column(
        connection, "airaapi_monitors", "month_runs", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(
        connection, "airaapi_monitors", "pause_on_error", "INTEGER NOT NULL DEFAULT 0"
    )
    # Full-text index over the readable text of stored results (titles, snippets,
    # links), so History can search inside them. FTS5 when SQLite has it.
    if not _table_exists(connection, "airaapi_search_text"):
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE airaapi_search_text USING fts5("
                "id UNINDEXED, body, tokenize = 'unicode61 remove_diacritics 2')"
            )
        except sqlite3.OperationalError:
            connection.execute(
                "CREATE TABLE airaapi_search_text (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
    _extras(connection)


# Tables and columns added after migration 15. Databases already past that
# migration get them lazily: every function that reads them calls
# ``ensure_extras`` first (one sqlite_master lookup when they exist).
EXTRA_TABLES = (
    "airaapi_key_usage",
    "airaapi_prefs",
    "airaapi_rank_projects",
    "airaapi_rank_keywords",
    "airaapi_rank_checks",
    "airaapi_rank_usage",
)


def _extras(connection: sqlite3.Connection) -> None:
    # Reselling: what a key's holder (a client) pays, how much it may use, which engines.
    _add_column(
        connection, "airaapi_api_keys", "client_name", "TEXT NOT NULL DEFAULT ''"
    )
    _add_column(
        connection, "airaapi_api_keys", "price_per_1000", "REAL NOT NULL DEFAULT 0"
    )
    _add_column(
        connection, "airaapi_api_keys", "monthly_quota", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(
        connection, "airaapi_api_keys", "allowed_engines", "TEXT NOT NULL DEFAULT '[]'"
    )
    # Per-key usage by day and engine. Unlike stored searches it is never pruned,
    # so a month's statement survives the history retention window.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_key_usage (
            day TEXT NOT NULL, key_id TEXT NOT NULL, engine TEXT NOT NULL,
            calls INTEGER NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0, credits INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, key_id, engine)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_prefs (
            name TEXT PRIMARY KEY, value TEXT NOT NULL
        )"""
    )
    # Rank tracker: a website, the keywords it should rank for, and every check.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_rank_projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
            client_name TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
            gl TEXT NOT NULL DEFAULT '', hl TEXT NOT NULL DEFAULT '',
            device TEXT NOT NULL DEFAULT '', interval_minutes INTEGER NOT NULL DEFAULT 0,
            monthly_fee REAL NOT NULL DEFAULT 0, notify INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_rank_keywords (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, keyword TEXT NOT NULL,
            saved_id TEXT NOT NULL, monitor_id TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE (project_id, keyword)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_airaapi_rank_keywords_saved "
        "ON airaapi_rank_keywords(saved_id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_rank_checks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, keyword_id TEXT NOT NULL,
            checked_at TEXT NOT NULL, position INTEGER, url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', depth INTEGER NOT NULL DEFAULT 0,
            search_id TEXT NOT NULL DEFAULT '', competitors TEXT NOT NULL DEFAULT '[]'
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_airaapi_rank_checks_keyword "
        "ON airaapi_rank_checks(keyword_id, checked_at)"
    )
    # A deleted project is kept (hidden) so the months it was billed still add up on the Earnings tab.
    _add_column(connection, "airaapi_rank_projects", "deleted_at", "TEXT")
    # Checks a project ran per month. Never pruned and kept when keywords or the project go, like key usage.
    created = not _table_exists(connection, "airaapi_rank_usage")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_rank_usage (
            project_id TEXT NOT NULL, month TEXT NOT NULL,
            checks INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (project_id, month)
        )"""
    )
    if created:
        connection.execute(
            "INSERT INTO airaapi_rank_usage (project_id, month, checks) "
            "SELECT project_id, substr(checked_at, 1, 7), COUNT(*) FROM airaapi_rank_checks "
            "GROUP BY project_id, substr(checked_at, 1, 7) "
            "ON CONFLICT(project_id, month) DO NOTHING"
        )


def ensure_extras(connection: sqlite3.Connection) -> None:
    """Add the post-migration tables and columns when this database lacks them."""
    marks = ", ".join("?" for _ in EXTRA_TABLES)
    row = connection.execute(
        f"SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ({marks})",
        EXTRA_TABLES,
    ).fetchone()
    if row and int(row[0]) == len(EXTRA_TABLES):
        return
    _extras(connection)
    connection.commit()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()
        is not None
    )


def _add_column(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_key_index(connection: sqlite3.Connection) -> None:
    """Create the accounts-database table that maps key hashes to account ids."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS airaapi_key_index (
            key_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_airaapi_key_index_user ON airaapi_key_index(user_id)"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def new_id() -> str:
    return uuid.uuid4().hex


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def pack(value: Any) -> bytes:
    """Serialise and compress a JSON value for storage."""
    return zlib.compress(json.dumps(value, ensure_ascii=False).encode("utf-8"), 6)


def pack_text(value: str) -> bytes:
    return zlib.compress(value.encode("utf-8"), 6)


# The largest body Aira ever stores is the result cap (50 MB at most); anything
# that inflates past this is a damaged or hostile blob, not a search result.
MAX_INFLATED_BYTES = 64 * 1024 * 1024


class BlobError(ValueError):
    """A stored result cannot be read back (damaged, or larger than any result Aira stores)."""


def _inflate(blob: bytes) -> bytes:
    """Decompress ``blob`` without ever allocating more than ``MAX_INFLATED_BYTES``."""
    inflater = zlib.decompressobj()
    try:
        data = inflater.decompress(bytes(blob), MAX_INFLATED_BYTES + 1)
    except zlib.error:
        raise BlobError("The stored result is damaged and cannot be read.") from None
    if len(data) > MAX_INFLATED_BYTES or inflater.unconsumed_tail:
        raise BlobError("The stored result is larger than Monosage stores and was skipped.")
    return data


def unpack(blob: bytes | None) -> Any:
    if blob is None:
        return None
    try:
        return json.loads(_inflate(blob).decode("utf-8"))
    except (UnicodeDecodeError, RecursionError, json.JSONDecodeError):
        raise BlobError("The stored result is damaged and cannot be read.") from None


def unpack_text(blob: bytes | None) -> str | None:
    if blob is None:
        return None
    try:
        return _inflate(blob).decode("utf-8")
    except UnicodeDecodeError:
        raise BlobError("The stored page is damaged and cannot be read.") from None


def _query(
    connection: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> list[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return cursor.execute(sql, tuple(params)).fetchall()
    finally:
        cursor.close()


def _one(
    connection: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> sqlite3.Row | None:
    rows = _query(connection, sql, params)
    return rows[0] if rows else None


def restrict_to_owner(path: os.PathLike[str] | str) -> None:
    """Best effort: make a freshly created secret file readable by its owner only.

    POSIX file creation already uses mode 0600. On Windows a file inherits the
    folder's access list, which on a data directory outside the user profile
    (for example ``AUTO_APPLY_DB_PATH`` on another drive) often includes other local users, so the
    inherited entries are dropped and only the current user and SYSTEM keep access.
    """
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    if not user:
        return
    account = f"{domain}{chr(92)}{user}" if domain else user
    try:
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{account}:F",
                "*S-1-5-18:F",
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# searches
# ---------------------------------------------------------------------------


def insert_search(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    result_blob: bytes | None = None,
    html_blob: bytes | None = None,
) -> str:
    """Insert one search record. ``row`` supplies any of ``SEARCH_COLUMNS``."""
    values = {name: row[name] for name in SEARCH_COLUMNS if name in row}
    values.setdefault("id", new_id())
    unknown = set(row) - set(SEARCH_COLUMNS)
    if unknown:
        raise ValueError(f"Unknown search columns: {sorted(unknown)}")
    values["result_blob"] = result_blob
    values["html_blob"] = html_blob
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO airaapi_searches ({columns}) VALUES ({marks})",
        tuple(values.values()),
    )
    return str(values["id"])


def get_search_row(
    connection: sqlite3.Connection, search_id: str
) -> sqlite3.Row | None:
    return _one(
        connection,
        f"SELECT {_SUMMARY_SQL} FROM airaapi_searches WHERE id = ?",
        (search_id,),
    )


def get_search_row_by_serpapi(
    connection: sqlite3.Connection, serpapi_id: str, api_key_id: str | None = None
) -> sqlite3.Row | None:
    """The newest record for a SerpApi search id (cache-hit copies share the id).

    With ``api_key_id`` only that key's own records count."""
    if not serpapi_id:
        return None
    scope = " AND api_key_id = ?" if api_key_id is not None else ""
    args: tuple[Any, ...] = (
        (serpapi_id, api_key_id) if api_key_id is not None else (serpapi_id,)
    )
    return _one(
        connection,
        f"SELECT {_SUMMARY_SQL} FROM airaapi_searches WHERE serpapi_id = ?{scope} "
        "ORDER BY cache_hit ASC, created_at DESC, rowid DESC LIMIT 1",
        args,
    )


def get_blobs(
    connection: sqlite3.Connection, search_id: str
) -> tuple[bytes | None, bytes | None]:
    row = _one(
        connection,
        "SELECT result_blob, html_blob FROM airaapi_searches WHERE id = ?",
        (search_id,),
    )
    if row is None:
        return None, None
    return row["result_blob"], row["html_blob"]


def set_blobs(
    connection: sqlite3.Connection,
    search_id: str,
    *,
    result_blob: bytes | None = None,
    html_blob: bytes | None = None,
) -> None:
    if result_blob is not None:
        connection.execute(
            "UPDATE airaapi_searches SET result_blob = ? WHERE id = ?",
            (result_blob, search_id),
        )
    if html_blob is not None:
        connection.execute(
            "UPDATE airaapi_searches SET html_blob = ? WHERE id = ?",
            (html_blob, search_id),
        )


def update_search(
    connection: sqlite3.Connection, search_id: str, fields: Mapping[str, Any]
) -> bool:
    if not fields:
        return False
    bad = set(fields) - MUTABLE_SEARCH_FIELDS
    if bad:
        raise ValueError(f"Cannot update search columns: {sorted(bad)}")
    assignments = ", ".join(f"{name} = ?" for name in fields)
    cursor = connection.execute(
        f"UPDATE airaapi_searches SET {assignments} WHERE id = ?",
        (*fields.values(), search_id),
    )
    return cursor.rowcount > 0


def list_searches(
    connection: sqlite3.Connection,
    *,
    engine: str = "",
    status: str = "",
    source: str = "",
    text: str = "",
    pinned: bool | None = None,
    date_from: str = "",
    date_to: str = "",
    cache_hit: bool | None = None,
    in_results: bool = False,
    limit: int = 25,
    offset: int = 0,
    api_key_id: str | None = None,
) -> tuple[list[sqlite3.Row], int]:
    """A page of searches, newest first. ``in_results`` also matches ``text`` inside stored results.

    ``api_key_id`` keeps only the searches made with that API key (what a client's key may see)."""
    clauses: list[str] = []
    args: list[Any] = []
    if api_key_id is not None:
        clauses.append("api_key_id = ?")
        args.append(api_key_id)
    if engine:
        clauses.append("engine = ?")
        args.append(engine)
    if status:
        clauses.append("status = ?")
        args.append(status)
    if source:
        clauses.append("source = ?")
        args.append(source)
    if pinned is not None:
        clauses.append("pinned = ?")
        args.append(1 if pinned else 0)
    if cache_hit is not None:
        clauses.append("cache_hit = ?")
        args.append(1 if cache_hit else 0)
    if date_from:
        clauses.append("created_at >= ?")
        args.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        args.append(date_to)
    if text:
        pattern = f"%{like_escape(text.lower())}%"
        metadata = (
            "lower(label) LIKE ? ESCAPE '\\' OR lower(params_json) LIKE ? ESCAPE '\\' "
            "OR lower(engine) LIKE ? ESCAPE '\\' OR lower(error) LIKE ? ESCAPE '\\'"
        )
        args.extend([pattern] * 4)
        inside = _text_match_sql(connection, text) if in_results else None
        if inside:
            clauses.append(f"({metadata} OR id IN ({inside[0]}))")
            args.append(inside[1])
        else:
            clauses.append(f"({metadata})")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    total_row = _one(
        connection, f"SELECT COUNT(*) AS n FROM airaapi_searches{where}", args
    )
    total = int(total_row["n"]) if total_row else 0
    rows = _query(
        connection,
        f"SELECT {_SUMMARY_SQL} FROM airaapi_searches{where} "
        "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (*args, limit, offset),
    )
    return rows, total


# ---------------------------------------------------------------------------
# full-text index of stored results
# ---------------------------------------------------------------------------

# Parts of a body that describe the request rather than the results.
_UNINDEXED_KEYS = frozenset(
    {"search_metadata", "search_parameters", "serpapi_pagination", "pagination"}
)
TEXT_INDEX_CHARS = 200_000
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SNIPPET_OPEN, SNIPPET_CLOSE = "\x02", "\x03"


def result_text(result: Any, limit: int = TEXT_INDEX_CHARS) -> str:
    """The readable strings of a result (titles, snippets, links, names), capped at ``limit`` characters."""
    parts: list[str] = []
    size = 0
    stack: list[Any] = [result]
    while stack and size < limit:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(
                child
                for key, child in reversed(list(value.items()))
                if key not in _UNINDEXED_KEYS and key != "rectangle"
            )
        elif isinstance(value, list):
            stack.extend(reversed(value))
        elif isinstance(value, str):
            text = value.strip()
            # thumbnails arrive as data URIs; they are not text anyone searches for
            if text and not text.startswith("data:"):
                piece = text[:2000]
                parts.append(piece)
                size += len(piece) + 1
    return "\n".join(parts)[:limit]


def _is_fts(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'airaapi_search_text'"
    ).fetchone()
    return bool(row and "fts5" in str(row[0]).lower())


def _fts_query(text: str) -> str:
    """Every word must appear; the last one may be a prefix. Quoting keeps FTS syntax out of user input."""
    tokens = _TOKEN_RE.findall(text)[:12]
    if not tokens:
        return ""
    quoted = [f'"{token}"' for token in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


def _text_match_sql(
    connection: sqlite3.Connection, text: str
) -> tuple[str, str] | None:
    """(sub-select of matching ids, its argument), or None when ``text`` has no searchable word."""
    if _is_fts(connection):
        query = _fts_query(text)
        if not query:
            return None
        return (
            "SELECT id FROM airaapi_search_text WHERE airaapi_search_text MATCH ?",
            query,
        )
    if not text.strip():
        return None
    return (
        "SELECT id FROM airaapi_search_text WHERE lower(body) LIKE ? ESCAPE '\\'",
        f"%{like_escape(text.strip().lower())}%",
    )


def index_text(connection: sqlite3.Connection, search_id: str, result: Any) -> None:
    """(Re)index the readable text of one stored result."""
    connection.execute("DELETE FROM airaapi_search_text WHERE id = ?", (search_id,))
    body = result_text(result)
    if body:
        connection.execute(
            "INSERT INTO airaapi_search_text (id, body) VALUES (?, ?)",
            (search_id, body),
        )


def drop_orphan_text(connection: sqlite3.Connection) -> None:
    """Remove index rows whose search was deleted."""
    connection.execute(
        "DELETE FROM airaapi_search_text WHERE id NOT IN (SELECT id FROM airaapi_searches)"
    )


def text_snippets(
    connection: sqlite3.Connection, ids: Sequence[str], text: str
) -> dict[str, str]:
    """A short excerpt around the match for each id whose stored result contains ``text``.

    Matched words are wrapped in ``SNIPPET_OPEN``/``SNIPPET_CLOSE`` so the browser
    can highlight them without ever parsing the excerpt as HTML.
    """
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    if _is_fts(connection):
        query = _fts_query(text)
        if not query:
            return {}
        rows = _query(
            connection,
            "SELECT id, snippet(airaapi_search_text, 1, ?, ?, '…', 16) AS excerpt "
            f"FROM airaapi_search_text WHERE airaapi_search_text MATCH ? AND id IN ({marks})",
            (SNIPPET_OPEN, SNIPPET_CLOSE, query, *ids),
        )
        return {row["id"]: " ".join(str(row["excerpt"]).split()) for row in rows}
    needle = text.strip().lower()
    if not needle:
        return {}
    out: dict[str, str] = {}
    for row in _query(
        connection,
        f"SELECT id, body FROM airaapi_search_text WHERE id IN ({marks})",
        tuple(ids),
    ):
        body = str(row["body"])
        at = body.lower().find(needle)
        if at < 0:
            continue
        start, end = max(0, at - 60), min(len(body), at + len(needle) + 60)
        excerpt = (
            ("…" if start else "")
            + body[start:at]
            + SNIPPET_OPEN
            + body[at : at + len(needle)]
            + SNIPPET_CLOSE
            + body[at + len(needle) : end]
            + ("…" if end < len(body) else "")
        )
        out[row["id"]] = " ".join(excerpt.split())
    return out


def unindexed_result_ids(connection: sqlite3.Connection, limit: int) -> list[str]:
    """Newest stored JSON results that have no text index row yet (for the backfill)."""
    rows = _query(
        connection,
        "SELECT id FROM airaapi_searches WHERE result_blob IS NOT NULL AND output = 'json' "
        "AND id NOT IN (SELECT id FROM airaapi_search_text) "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (limit,),
    )
    return [row["id"] for row in rows]


def backfill_text_index(connection: sqlite3.Connection, limit: int = 5000) -> int:
    """Index the newest stored results that predate the text index. Returns how many were indexed."""
    done = 0
    for search_id in unindexed_result_ids(connection, limit):
        result_blob, _html = get_blobs(connection, search_id)
        try:
            result = unpack(result_blob)
        except BlobError:
            continue  # a damaged blob stays unsearchable; reading it is reported elsewhere
        index_text(connection, search_id, result)
        done += 1
    return done


def get_pixel_blob(connection: sqlite3.Connection, search_id: str) -> bytes | None:
    row = _one(
        connection, "SELECT pixel_blob FROM airaapi_searches WHERE id = ?", (search_id,)
    )
    return row["pixel_blob"] if row else None


def set_pixel_blob(connection: sqlite3.Connection, search_id: str, blob: bytes) -> None:
    connection.execute(
        "UPDATE airaapi_searches SET pixel_blob = ? WHERE id = ?", (blob, search_id)
    )


GONE_DAYS = 31


def _tombstone(
    connection: sqlite3.Connection, where: str, args: Sequence[Any] = ()
) -> None:
    """Remember the ids about to be deleted by ``DELETE ... WHERE where``."""
    moment = now_utc()
    connection.execute(
        "INSERT OR REPLACE INTO airaapi_gone (id, serpapi_id, gone_at) "
        f"SELECT id, serpapi_id, ? FROM airaapi_searches WHERE {where}",
        (to_iso(moment), *args),
    )
    connection.execute(
        "DELETE FROM airaapi_gone WHERE gone_at < ?",
        (to_iso(moment - timedelta(days=GONE_DAYS)),),
    )


def is_gone(connection: sqlite3.Connection, search_id: str) -> bool:
    """Whether ``search_id`` (an Aira or SerpApi id) was deleted or pruned recently."""
    return (
        _one(
            connection,
            "SELECT 1 AS hit FROM airaapi_gone WHERE id = ? OR serpapi_id = ? LIMIT 1",
            (search_id, search_id),
        )
        is not None
    )


def delete_search(connection: sqlite3.Connection, search_id: str) -> bool:
    _tombstone(connection, "id = ?", (search_id,))
    cursor = connection.execute(
        "DELETE FROM airaapi_searches WHERE id = ?", (search_id,)
    )
    connection.execute("DELETE FROM airaapi_search_text WHERE id = ?", (search_id,))
    if cursor.rowcount:
        connection.execute(
            "UPDATE airaapi_saved SET last_search_id = NULL WHERE last_search_id = ?",
            (search_id,),
        )
        connection.execute(
            "UPDATE airaapi_monitors SET last_search_id = NULL WHERE last_search_id = ?",
            (search_id,),
        )
    return cursor.rowcount > 0


def delete_where(
    connection: sqlite3.Connection,
    *,
    before: str = "",
    engine: str = "",
    include_pinned: bool = False,
) -> tuple[int, int]:
    """Bulk delete. Returns (deleted, kept_because_pinned)."""
    clauses: list[str] = []
    args: list[Any] = []
    if before:
        clauses.append("created_at < ?")
        args.append(before)
    if engine:
        clauses.append("engine = ?")
        args.append(engine)
    where = " AND ".join(clauses) or "1 = 1"
    kept = 0
    if not include_pinned:
        pinned = _one(
            connection,
            f"SELECT COUNT(*) AS n FROM airaapi_searches WHERE {where} AND pinned = 1",
            args,
        )
        kept = int(pinned["n"]) if pinned else 0
        where += " AND pinned = 0"
    _tombstone(connection, where, args)
    cursor = connection.execute(f"DELETE FROM airaapi_searches WHERE {where}", args)
    deleted = cursor.rowcount
    drop_orphan_text(connection)
    for table in ("airaapi_saved", "airaapi_monitors"):
        connection.execute(
            f"UPDATE {table} SET last_search_id = NULL WHERE last_search_id IS NOT NULL "
            "AND last_search_id NOT IN (SELECT id FROM airaapi_searches)"
        )
    return deleted, kept


def find_cached(
    connection: sqlite3.Connection, params_hash: str, newer_than: str
) -> sqlite3.Row | None:
    """The newest live, successful run with this hash that is still fresh."""
    return _one(
        connection,
        f"SELECT {_SUMMARY_SQL} FROM airaapi_searches "
        "WHERE params_hash = ? AND status = 'ok' AND cache_hit = 0 AND created_at >= ? "
        "AND (result_blob IS NOT NULL OR html_blob IS NOT NULL) "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (params_hash, newer_than),
    )


def count_live_since(
    connection: sqlite3.Connection,
    since: str,
    *,
    api_key_id: str = "",
    exclude_engines: Sequence[str] = (),
) -> int:
    """Calls made since ``since``: live SerpApi calls, or every call for one Aira key.

    ``exclude_engines`` leaves out engines Aira answers itself, which cost no
    SerpApi searches (it applies to the account count, not to a key's count).
    """
    if api_key_id:
        row = _one(
            connection,
            "SELECT COUNT(*) AS n FROM airaapi_searches WHERE api_key_id = ? AND created_at >= ?",
            (api_key_id, since),
        )
    else:
        marks = ", ".join("?" for _ in exclude_engines)
        row = _one(
            connection,
            "SELECT COUNT(*) AS n FROM airaapi_searches WHERE cache_hit = 0 AND created_at >= ?"
            + (f" AND engine NOT IN ({marks})" if exclude_engines else ""),
            (since, *exclude_engines),
        )
    return int(row["n"]) if row else 0


def count_searches(connection: sqlite3.Connection) -> int:
    row = _one(connection, "SELECT COUNT(*) AS n FROM airaapi_searches")
    return int(row["n"]) if row else 0


def count_status(connection: sqlite3.Connection, status: str) -> int:
    row = _one(
        connection,
        "SELECT COUNT(*) AS n FROM airaapi_searches WHERE status = ?",
        (status,),
    )
    return int(row["n"]) if row else 0


def prune(
    connection: sqlite3.Connection,
    *,
    retention_days: int,
    max_records: int,
    now: datetime,
) -> int:
    """Delete old and surplus records; pinned and monitor/saved baselines survive."""
    # An async search that nobody collected within two days is abandoned; keeping
    # "running" rows forever would let a flood of async calls grow the table unbounded.
    stale_running = to_iso(now - timedelta(days=2))
    protected = (
        "pinned = 0 AND id NOT IN (SELECT last_search_id FROM airaapi_saved "
        "WHERE last_search_id IS NOT NULL) AND id NOT IN (SELECT last_search_id "
        f"FROM airaapi_monitors WHERE last_search_id IS NOT NULL) AND (status != 'running' OR created_at < '{stale_running}')"
    )
    deleted = 0
    if retention_days > 0:
        cutoff = to_iso(now - timedelta(days=retention_days))
        _tombstone(connection, f"created_at < ? AND {protected}", (cutoff,))
        deleted += connection.execute(
            f"DELETE FROM airaapi_searches WHERE created_at < ? AND {protected}",
            (cutoff,),
        ).rowcount
    if max_records > 0:
        total = _one(connection, "SELECT COUNT(*) AS n FROM airaapi_searches")
        excess = (int(total["n"]) if total else 0) - max_records
        if excess > 0:
            # cache-hit copies go first: the live record they copy is what keeps the cache warm
            victims = (
                "id IN (SELECT id FROM airaapi_searches "
                f"WHERE {protected} ORDER BY cache_hit DESC, created_at ASC, rowid ASC LIMIT ?)"
            )
            _tombstone(connection, victims, (excess,))
            deleted += connection.execute(
                f"DELETE FROM airaapi_searches WHERE {victims}", (excess,)
            ).rowcount
    if deleted:
        drop_orphan_text(connection)
    return deleted


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------


def bump_usage(
    connection: sqlite3.Connection,
    day: str,
    engine: str,
    source: str,
    *,
    searches: int = 1,
    cached: int = 0,
    errors: int = 0,
    credits: int = 0,
) -> None:
    connection.execute(
        """INSERT INTO airaapi_usage (day, engine, source, searches, cached, errors, credits)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(day, engine, source) DO UPDATE SET
            searches = searches + excluded.searches, cached = cached + excluded.cached,
            errors = errors + excluded.errors, credits = credits + excluded.credits""",
        (day, engine, source, searches, cached, errors, credits),
    )


def usage_since(connection: sqlite3.Connection, day_from: str) -> list[sqlite3.Row]:
    return _query(
        connection,
        "SELECT day, engine, source, searches, cached, errors, credits "
        "FROM airaapi_usage WHERE day >= ? ORDER BY day",
        (day_from,),
    )


def latency_by_engine(connection: sqlite3.Connection, since: str) -> dict[str, float]:
    rows = _query(
        connection,
        "SELECT engine, AVG(duration_ms) AS avg_ms FROM airaapi_searches "
        "WHERE cache_hit = 0 AND status = 'ok' AND created_at >= ? GROUP BY engine",
        (since,),
    )
    return {row["engine"]: float(row["avg_ms"] or 0) for row in rows}


# ---------------------------------------------------------------------------
# saved searches and monitors
# ---------------------------------------------------------------------------


def insert_saved(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    connection.execute(
        "INSERT INTO airaapi_saved (id, name, engine, params_json, tags, created_at, pinned) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            row["name"],
            row["engine"],
            row["params_json"],
            row["tags"],
            row["created_at"],
            row.get("pinned", 0),
        ),
    )


def get_saved(connection: sqlite3.Connection, saved_id: str) -> sqlite3.Row | None:
    return _one(connection, "SELECT * FROM airaapi_saved WHERE id = ?", (saved_id,))


def list_saved(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return _query(
        connection,
        "SELECT * FROM airaapi_saved ORDER BY pinned DESC, created_at DESC, rowid DESC",
    )


def update_saved(
    connection: sqlite3.Connection, saved_id: str, fields: Mapping[str, Any]
) -> bool:
    allowed = {"name", "params_json", "tags", "pinned", "last_run_at", "last_search_id"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Cannot update saved columns: {sorted(bad)}")
    if not fields:
        return False
    assignments = ", ".join(f"{name} = ?" for name in fields)
    return (
        connection.execute(
            f"UPDATE airaapi_saved SET {assignments} WHERE id = ?",
            (*fields.values(), saved_id),
        ).rowcount
        > 0
    )


def delete_saved(connection: sqlite3.Connection, saved_id: str) -> bool:
    connection.execute("DELETE FROM airaapi_monitors WHERE saved_id = ?", (saved_id,))
    return (
        connection.execute(
            "DELETE FROM airaapi_saved WHERE id = ?", (saved_id,)
        ).rowcount
        > 0
    )


def insert_monitor(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    connection.execute(
        "INSERT INTO airaapi_monitors (id, saved_id, interval_minutes, enabled, next_run_at, "
        "notify_on_change, monthly_budget, pause_on_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            row["saved_id"],
            row["interval_minutes"],
            row.get("enabled", 1),
            row["next_run_at"],
            row.get("notify_on_change", 1),
            row.get("monthly_budget", 0),
            row.get("pause_on_error", 0),
        ),
    )


_MONITOR_SQL = (
    "SELECT m.*, s.name AS saved_name, s.engine AS engine, s.params_json AS params_json "
    "FROM airaapi_monitors m JOIN airaapi_saved s ON s.id = m.saved_id"
)


def get_monitor(connection: sqlite3.Connection, monitor_id: str) -> sqlite3.Row | None:
    return _one(connection, f"{_MONITOR_SQL} WHERE m.id = ?", (monitor_id,))


def list_monitors(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return _query(connection, f"{_MONITOR_SQL} ORDER BY m.next_run_at")


def due_monitors(connection: sqlite3.Connection, now_iso: str) -> list[sqlite3.Row]:
    return _query(
        connection,
        f"{_MONITOR_SQL} WHERE m.enabled = 1 AND m.next_run_at <= ? ORDER BY m.next_run_at",
        (now_iso,),
    )


def update_monitor(
    connection: sqlite3.Connection, monitor_id: str, fields: Mapping[str, Any]
) -> bool:
    allowed = {
        "interval_minutes",
        "enabled",
        "next_run_at",
        "last_run_at",
        "last_search_id",
        "notify_on_change",
        "last_change_summary",
        "monthly_budget",
        "budget_month",
        "month_runs",
        "pause_on_error",
    }
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Cannot update monitor columns: {sorted(bad)}")
    if not fields:
        return False
    assignments = ", ".join(f"{name} = ?" for name in fields)
    return (
        connection.execute(
            f"UPDATE airaapi_monitors SET {assignments} WHERE id = ?",
            (*fields.values(), monitor_id),
        ).rowcount
        > 0
    )


def delete_monitor(connection: sqlite3.Connection, monitor_id: str) -> bool:
    return (
        connection.execute(
            "DELETE FROM airaapi_monitors WHERE id = ?", (monitor_id,)
        ).rowcount
        > 0
    )


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def insert_api_key(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    connection.execute(
        "INSERT INTO airaapi_api_keys (id, name, prefix, key_hash, created_at, hourly_limit, "
        "client_name, price_per_1000, monthly_quota, allowed_engines) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            row["name"],
            row["prefix"],
            row["key_hash"],
            row["created_at"],
            row["hourly_limit"],
            row.get("client_name", ""),
            row.get("price_per_1000", 0),
            row.get("monthly_quota", 0),
            row.get("allowed_engines", "[]"),
        ),
    )


KEY_TERMS = frozenset(
    {
        "name",
        "hourly_limit",
        "client_name",
        "price_per_1000",
        "monthly_quota",
        "allowed_engines",
    }
)


def update_api_key(
    connection: sqlite3.Connection, key_id: str, fields: Mapping[str, Any]
) -> bool:
    ensure_extras(connection)
    bad = set(fields) - KEY_TERMS
    if bad:
        raise ValueError(f"Cannot update key columns: {sorted(bad)}")
    if not fields:
        return False
    assignments = ", ".join(f"{name} = ?" for name in fields)
    return (
        connection.execute(
            f"UPDATE airaapi_api_keys SET {assignments} WHERE id = ?",
            (*fields.values(), key_id),
        ).rowcount
        > 0
    )


def bump_key_usage(
    connection: sqlite3.Connection,
    day: str,
    key_id: str,
    engine: str,
    *,
    calls: int = 0,
    cached: int = 0,
    errors: int = 0,
    credits: int = 0,
) -> None:
    ensure_extras(connection)
    connection.execute(
        "INSERT INTO airaapi_key_usage (day, key_id, engine, calls, cached, errors, credits) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(day, key_id, engine) DO UPDATE SET "
        "calls = calls + excluded.calls, cached = cached + excluded.cached, "
        "errors = errors + excluded.errors, credits = credits + excluded.credits",
        (day, key_id, engine, calls, cached, errors, credits),
    )


def key_usage_between(
    connection: sqlite3.Connection, day_from: str, day_to: str, key_id: str = ""
) -> list[sqlite3.Row]:
    """Usage rows for ``day_from`` <= day <= ``day_to`` (inclusive, ``YYYY-MM-DD``)."""
    ensure_extras(connection)
    sql = "SELECT * FROM airaapi_key_usage WHERE day >= ? AND day <= ?"
    params: list[Any] = [day_from, day_to]
    if key_id:
        sql += " AND key_id = ?"
        params.append(key_id)
    return _query(connection, sql + " ORDER BY day, key_id, engine", params)


def key_usage_months(connection: sqlite3.Connection) -> list[str]:
    ensure_extras(connection)
    rows = _query(
        connection,
        "SELECT DISTINCT substr(day, 1, 7) AS month FROM airaapi_key_usage "
        "ORDER BY month DESC LIMIT 36",
    )
    return [str(row["month"]) for row in rows]


def bump_rank_usage(
    connection: sqlite3.Connection, project_id: str, month: str, checks: int = 1
) -> None:
    ensure_extras(connection)
    connection.execute(
        "INSERT INTO airaapi_rank_usage (project_id, month, checks) VALUES (?, ?, ?) "
        "ON CONFLICT(project_id, month) DO UPDATE SET checks = checks + excluded.checks",
        (project_id, month, checks),
    )


def rank_usage(connection: sqlite3.Connection, project_id: str, month: str) -> int:
    ensure_extras(connection)
    row = _one(
        connection,
        "SELECT checks FROM airaapi_rank_usage WHERE project_id = ? AND month = ?",
        (project_id, month),
    )
    return int(row["checks"]) if row else 0


def rank_usage_months(connection: sqlite3.Connection) -> list[str]:
    ensure_extras(connection)
    rows = _query(
        connection,
        "SELECT DISTINCT month FROM airaapi_rank_usage ORDER BY month DESC LIMIT 36",
    )
    return [str(row["month"]) for row in rows]


def get_pref(connection: sqlite3.Connection, name: str) -> Any:
    ensure_extras(connection)
    row = _one(connection, "SELECT value FROM airaapi_prefs WHERE name = ?", (name,))
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return None


def set_pref(connection: sqlite3.Connection, name: str, value: Any) -> None:
    ensure_extras(connection)
    connection.execute(
        "INSERT INTO airaapi_prefs (name, value) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
        (name, json.dumps(value)),
    )


_KEY_COLUMNS = (
    "id, name, prefix, created_at, last_used_at, revoked_at, hourly_limit, "
    "client_name, price_per_1000, monthly_quota, allowed_engines"
)


def list_api_keys(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_extras(connection)
    return _query(
        connection,
        f"SELECT {_KEY_COLUMNS} FROM airaapi_api_keys ORDER BY created_at DESC, rowid DESC",
    )


def get_api_key(connection: sqlite3.Connection, key_id: str) -> sqlite3.Row | None:
    ensure_extras(connection)
    return _one(
        connection,
        f"SELECT {_KEY_COLUMNS}, key_hash FROM airaapi_api_keys WHERE id = ?",
        (key_id,),
    )


def get_api_key_by_hash(
    connection: sqlite3.Connection, key_hash: str
) -> sqlite3.Row | None:
    ensure_extras(connection)
    return _one(
        connection,
        f"SELECT {_KEY_COLUMNS}, key_hash FROM airaapi_api_keys WHERE key_hash = ?",
        (key_hash,),
    )


def revoke_api_key(connection: sqlite3.Connection, key_id: str, at: str) -> bool:
    return (
        connection.execute(
            "UPDATE airaapi_api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (at, key_id),
        ).rowcount
        > 0
    )


def touch_api_key(connection: sqlite3.Connection, key_id: str, at: str) -> None:
    connection.execute(
        "UPDATE airaapi_api_keys SET last_used_at = ? WHERE id = ?", (at, key_id)
    )


# key index (accounts database)


def index_key(connection: sqlite3.Connection, key_hash: str, user_id: str) -> None:
    ensure_key_index(connection)
    connection.execute(
        "INSERT INTO airaapi_key_index (key_hash, user_id) VALUES (?, ?) "
        "ON CONFLICT(key_hash) DO UPDATE SET user_id = excluded.user_id",
        (key_hash, user_id),
    )


def unindex_key(connection: sqlite3.Connection, key_hash: str) -> None:
    ensure_key_index(connection)
    connection.execute("DELETE FROM airaapi_key_index WHERE key_hash = ?", (key_hash,))


def unindex_user(connection: sqlite3.Connection, user_id: str) -> None:
    ensure_key_index(connection)
    connection.execute("DELETE FROM airaapi_key_index WHERE user_id = ?", (user_id,))


def lookup_key_index(connection: sqlite3.Connection, key_hash: str) -> str | None:
    ensure_key_index(connection)
    row = _one(
        connection,
        "SELECT user_id FROM airaapi_key_index WHERE key_hash = ?",
        (key_hash,),
    )
    return str(row["user_id"]) if row else None


# ---------------------------------------------------------------------------
# generic cache
# ---------------------------------------------------------------------------


def cache_get(connection: sqlite3.Connection, key: str, now: datetime) -> Any | None:
    row = _one(
        connection,
        "SELECT value FROM airaapi_cache WHERE key = ? AND expires_at > ?",
        (key, to_iso(now)),
    )
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return None


def cache_set(
    connection: sqlite3.Connection,
    key: str,
    value: Any,
    ttl_seconds: int,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO airaapi_cache (key, value, expires_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
        (
            key,
            json.dumps(value, ensure_ascii=False),
            to_iso(now + timedelta(seconds=ttl_seconds)),
        ),
    )


def cache_delete(connection: sqlite3.Connection, prefix: str) -> None:
    connection.execute(
        "DELETE FROM airaapi_cache WHERE key LIKE ? ESCAPE '\\'",
        (like_escape(prefix) + "%",),
    )


def cache_trim(connection: sqlite3.Connection, prefix: str, keep: int) -> int:
    """Keep only the ``keep`` newest cache rows whose key starts with ``prefix``."""
    pattern = like_escape(prefix) + "%"
    return connection.execute(
        "DELETE FROM airaapi_cache WHERE key LIKE ? ESCAPE '\\' AND key NOT IN ("
        "SELECT key FROM airaapi_cache WHERE key LIKE ? ESCAPE '\\' "
        "ORDER BY expires_at DESC, rowid DESC LIMIT ?)",
        (pattern, pattern, keep),
    ).rowcount


def cache_expire(connection: sqlite3.Connection, now: datetime) -> None:
    connection.execute(
        "DELETE FROM airaapi_cache WHERE expires_at <= ?", (to_iso(now),)
    )


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------


class SecretStore:
    """Encrypt small secrets with a Fernet key and keep them in ``airaapi_secrets``."""

    def __init__(self, key: bytes) -> None:
        try:
            self._fernet = Fernet(bytes(key))
        except (ValueError, TypeError) as error:
            raise SecretError(
                "The secret encryption key is not a valid Fernet key."
            ) from error

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(bytes(ciphertext)).decode("utf-8")
        except InvalidToken as error:
            raise SecretError(
                "The stored secret cannot be decrypted with the current key file."
            ) from error

    def put(
        self, connection: sqlite3.Connection, name: str, plaintext: str, now: datetime
    ) -> None:
        connection.execute(
            "INSERT INTO airaapi_secrets (name, ciphertext, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext = excluded.ciphertext, "
            "updated_at = excluded.updated_at",
            (name, self.encrypt(plaintext), to_iso(now)),
        )

    def get(self, connection: sqlite3.Connection, name: str) -> str | None:
        row = _one(
            connection, "SELECT ciphertext FROM airaapi_secrets WHERE name = ?", (name,)
        )
        return self.decrypt(row["ciphertext"]) if row else None

    def delete(self, connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "DELETE FROM airaapi_secrets WHERE name = ?", (name,)
            ).rowcount
            > 0
        )

    def exists(self, connection: sqlite3.Connection, name: str) -> bool:
        return (
            _one(
                connection, "SELECT 1 AS x FROM airaapi_secrets WHERE name = ?", (name,)
            )
            is not None
        )
