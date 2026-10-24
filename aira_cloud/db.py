"""The control-plane database: organizations, members, plans, usage, audit, queue.

SQLite in WAL mode by default, PostgreSQL (``aira_cloud.pgdb``) when ``AIRA_CLOUD_DB=postgres``.
Every tenant table carries ``org_id`` as a mandatory indexed column, and callers set it from the
session, never from a request body. The SQL sticks to a portable subset (``?`` placeholders, no
SQLite-only functions on the hot paths); the PostgreSQL adapter translates it at the connection
boundary and adds row-level security on top.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from aira_cloud.schema import MIGRATIONS

logger = logging.getLogger("aira_cloud.db")

# -- tenant scope ---------------------------------------------------------------------------------
# PostgreSQL row-level security needs to know which organization a transaction works for. The gateway
# and the worker enter ``tenant_scope`` once the organization is known; ``platform_scope`` is the
# explicit, greppable escape for code inside a tenant scope that must read across organizations.

_scope = threading.local()


def current_scope() -> str:
    """The organization the calling thread works for, or ``""`` (platform / not yet known)."""
    if getattr(_scope, "platform", False):
        return ""
    return str(getattr(_scope, "org_id", "") or "")


@contextmanager
def tenant_scope(org_id: str) -> Iterator[None]:
    previous = (getattr(_scope, "org_id", ""), getattr(_scope, "platform", False))
    _scope.org_id, _scope.platform = org_id or "", False
    try:
        yield
    finally:
        _scope.org_id, _scope.platform = previous


def set_scope(org_id: str) -> None:
    """Enter a tenant scope for the rest of the request (the gateway calls ``clear_scope`` when it ends)."""
    _scope.org_id, _scope.platform = org_id or "", False


def clear_scope() -> None:
    _scope.org_id, _scope.platform = "", False


@contextmanager
def platform_scope() -> Iterator[None]:
    previous = getattr(_scope, "platform", False)
    _scope.platform = True
    try:
        yield
    finally:
        _scope.platform = previous


try:  # PostgreSQL is optional: the ``cloud`` extra installs psycopg
    import psycopg as _psycopg

    INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (
        sqlite3.IntegrityError,
        _psycopg.errors.IntegrityError,
    )
except ImportError:  # pragma: no cover - psycopg is part of the cloud extra
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    )


def loads(value: str | bytes | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class _Connection(sqlite3.Connection):
    """Autocommit connection. ``with`` commits an open transaction (or rolls it back) and closes."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> Literal[False]:
        try:
            if self.in_transaction:
                if exc_type is None:
                    self.execute("COMMIT")
                else:
                    self.execute("ROLLBACK")
        finally:
            self.close()
        return False


class ControlDB:
    dialect = "sqlite"

    def close(self) -> None:
        """SQLite opens a connection per call; there is nothing to release."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._migrate_lock = threading.Lock()
        self._ready = False

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path, timeout=15, factory=_Connection, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.DatabaseError:
            logger.debug("WAL is unavailable for %s", self.path.name)
        return connection

    def migrate(self) -> int:
        with self._migrate_lock:
            connection = self._open()
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS cloud_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL)"
                )
                done = {
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM cloud_migrations"
                    )
                }
                applied = 0
                for version, name, script in MIGRATIONS:
                    if version in done:
                        continue
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in _statements(script):
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO cloud_migrations (version, name, applied_at) VALUES (?, ?, strftime('%s','now'))",
                            (version, name),
                        )
                        connection.execute("COMMIT")
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    applied += 1
                self._ready = True
                return applied
            finally:
                connection.close()

    def version(self) -> int:
        with self.read() as connection:
            row = connection.execute(
                "SELECT MAX(version) FROM cloud_migrations"
            ).fetchone()
            return int(row[0] or 0)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        if not self._ready:
            self.migrate()
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self, *, exclusive: bool = True) -> Iterator[sqlite3.Connection]:
        """One atomic write transaction (the write lock is taken up front; ``exclusive`` only matters on PostgreSQL)."""
        if not self._ready:
            self.migrate()
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            else:
                if connection.in_transaction:
                    connection.execute("COMMIT")
        finally:
            connection.close()


def open_db(config: Any) -> ControlDB:
    """The control-plane database the configuration asks for."""
    if getattr(config, "db_backend", "sqlite") == "postgres":
        from aira_cloud.pgdb import PostgresDB

        return PostgresDB(
            config.database_url,
            config.database_platform_url,
            config.database_admin_url,
            pool_size=config.database_pool_size,
        )
    return ControlDB(config.db_path)


def _dialect(connection: Any) -> str:
    return str(getattr(connection, "dialect", "sqlite"))


def table_names(connection: Any) -> list[str]:
    """Base tables in the control-plane database."""
    if _dialect(connection) == "postgres":
        return [
            r[0]
            for r in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            ).fetchall()
        ]
    return [
        r[0]
        for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]


def table_columns(connection: Any, table: str) -> list[str]:
    if _dialect(connection) == "postgres":
        return [
            r[0]
            for r in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = ? ORDER BY ordinal_position",
                (table,),
            ).fetchall()
        ]
    return [r[1] for r in connection.execute(f"PRAGMA table_info({table})").fetchall()]


def defer_foreign_keys(connection: Any) -> None:
    """Check foreign keys at commit instead of per statement (deleting or restoring a whole organization)."""
    if _dialect(connection) == "postgres":
        connection.execute("SET CONSTRAINTS ALL DEFERRED")
    else:
        connection.execute("PRAGMA defer_foreign_keys = ON")


def lock(connection: Any, name: str) -> None:
    """A transaction-scoped lock on ``name`` (a no-op on SQLite, whose write lock already serializes)."""
    if _dialect(connection) == "postgres":
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (name,))


def try_lock(connection: Any, name: str) -> bool:
    """Like ``lock`` but never waits: False when another transaction holds ``name`` (always True on SQLite).

    A transaction that needs several locks takes them with this, so two of them cannot wait on each other.
    """
    if _dialect(connection) != "postgres":
        return True
    row = connection.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext(?))", (name,)
    ).fetchone()
    return bool(row[0])


def sync_sequences(connection: Any, table: str) -> None:
    """After rows were inserted with explicit identity values, move the sequence past them."""
    if _dialect(connection) != "postgres":
        return
    for row in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = ? AND is_identity = 'YES'",
        (table,),
    ).fetchall():
        column = row[0]
        connection.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), COALESCE((SELECT MAX({column}) FROM {table}), 0) + 1, false)"
        )


@contextmanager
def savepoint(connection: Any) -> Iterator[None]:
    """A failed statement inside does not poison the surrounding transaction (PostgreSQL aborts it otherwise)."""
    connection.execute("SAVEPOINT aira_sp")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT aira_sp")
        raise
    finally:
        connection.execute("RELEASE SAVEPOINT aira_sp")


def _statements(script: str) -> list[str]:
    """Split a migration script on ``;`` at line ends, keeping trigger bodies whole."""
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        upper = stripped.upper()
        if upper.startswith("CREATE TRIGGER"):
            depth = 1
        if depth and upper.rstrip(";") == "END":
            statements.append("\n".join(current))
            current, depth = [], 0
            continue
        if not depth and stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def one(
    connection: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> dict[str, Any] | None:
    row = connection.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def many(
    connection: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]
