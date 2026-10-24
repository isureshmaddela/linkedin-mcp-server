"""The PostgreSQL control plane: the same services and SQL as SQLite, behind a connection adapter.

Two connection pools, two roles:

* the **tenant** pool connects as ``aira_app``, a role that is neither owner nor ``BYPASSRLS``. A
  transaction opened inside a tenant scope (``db.tenant_scope(org_id)``) sets ``app.org_id`` with
  ``SET LOCAL`` semantics, and every tenant table's policy compares its organization column to it;
* the **platform** pool connects as ``aira_platform`` (``BYPASSRLS``) for code that legitimately spans
  organizations: sign-in, sign-up, webhooks, the scheduler tick, operator actions, backups.

``read()`` and ``write()`` pick the pool from the calling thread's scope, so tenant request and job
code is under row-level security without the call sites changing, and a statement that forgets its
``org_id`` filter returns nothing instead of another organization's rows.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
import psycopg_pool
from psycopg.adapt import Dumper, Loader
from psycopg.postgres import types as pg_types

from aira_cloud import dialect
from aira_cloud.db import ControlDB, _statements, current_scope
from aira_cloud.schema import MIGRATIONS

logger = logging.getLogger("aira_cloud.pgdb")

WRITE_LOCK = 0x41495241  # "AIRA": one writer at a time, like SQLite's BEGIN IMMEDIATE
MIGRATE_LOCK = 0x41495242


class Row(tuple):  # noqa: SLOT001 - a tuple with names, like sqlite3.Row
    """A result row readable by name and by position, convertible with ``dict(row)``."""

    _index: dict[str, int]
    _names: list[str]

    def __new__(cls, values: Sequence[Any], names: list[str], index: dict[str, int]):
        row = super().__new__(cls, values)
        row._index = index
        row._names = names
        return row

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._names)


def _row_factory(cursor: Any) -> Any:
    names = [column.name for column in cursor.description] if cursor.description else []
    index = {name: i for i, name in enumerate(names)}

    def make(values: Sequence[Any]) -> Row:
        return Row(values, names, index)

    return make


class _NumericLoader(Loader):
    """``SUM`` of integers is NUMERIC in PostgreSQL; give back an int (or a float with a fraction)."""

    def load(self, data: Any) -> Any:
        text = bytes(data).decode()
        return float(text) if "." in text else int(text)


class _FlagDumper(Dumper):
    """The control plane stores flags as 0 and 1; a Python bool is sent as an integer."""

    oid = pg_types["int8"].oid

    def dump(self, obj: Any) -> bytes:
        return b"1" if obj else b"0"


def _sql(text: str) -> LiteralString:
    """psycopg types queries as literal strings to discourage interpolation; ours are translated, never user input."""
    return cast("LiteralString", text)


def _configure(connection: psycopg.Connection[Any]) -> None:
    connection.adapters.register_loader("numeric", _NumericLoader)
    connection.adapters.register_dumper(bool, _FlagDumper)
    connection.row_factory = _row_factory  # type: ignore[assignment]


class PgCursor:
    """What ``connection.execute`` returns: the subset of the sqlite3 cursor the services use."""

    def __init__(self, cursor: Any, connection: PgConnection) -> None:
        self._cursor = cursor
        self._connection = connection

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    def fetchmany(self, size: int = 1) -> list[Any]:
        return self._cursor.fetchmany(size)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._cursor.fetchall())

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def lastrowid(self) -> int | None:
        """The identity value the last INSERT in this session generated (``lastval()``)."""
        try:
            with self._connection.raw.transaction():
                row = self._connection.raw.execute("SELECT lastval()").fetchone()
        except psycopg.Error:
            return None
        return int(row[0]) if row else None


class _CursorHandle:
    """``connection.cursor()`` as the shared domain code uses it: settable ``row_factory``, execute, fetch, close."""

    row_factory: Any = None

    def __init__(self, connection: PgConnection) -> None:
        self._connection = connection
        self._result: PgCursor | None = None

    def execute(self, sql: str, params: Any = ()) -> _CursorHandle:
        self._result = self._connection.execute(sql, params)
        return self

    def fetchone(self) -> Any:
        return self._result.fetchone() if self._result else None

    def fetchall(self) -> list[Any]:
        return self._result.fetchall() if self._result else []

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self._result.rowcount if self._result else -1

    def close(self) -> None:
        self._result = None


class PgConnection:
    """One transaction on PostgreSQL, speaking the sqlite3 connection API the services were written to."""

    dialect = "postgres"
    in_transaction = True

    def __init__(
        self, raw: psycopg.Connection[Any], db: PostgresDB, *, tenant: bool = False
    ) -> None:
        self.raw = raw
        self._db = db
        self.tenant = tenant  # True on the row-level-security pool

    def execute(self, sql: str, params: Any = ()) -> PgCursor:
        named = isinstance(params, Mapping)
        statement = _translated(sql, self._db.primary_keys_key, named)
        return PgCursor(
            self.raw.execute(_sql(statement), dict(params) if named else tuple(params)),
            self,
        )

    def cursor(self) -> _CursorHandle:
        return _CursorHandle(self)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        statement = _translated(sql, self._db.primary_keys_key, False)
        with self.raw.cursor() as cursor:
            cursor.executemany(_sql(statement), [tuple(r) for r in rows])


_PRIMARY_KEYS: dict[str, dict[str, tuple[str, ...]]] = {}


@lru_cache(maxsize=4096)
def _translated_cached(sql: str, key: str, named: bool) -> str:
    return dialect.translate(sql, _PRIMARY_KEYS.get(key, {}), named=named)


def _translated(sql: str, key: str, named: bool = False) -> str:
    return _translated_cached(sql, key, named)


class PostgresDB(ControlDB):
    dialect = "postgres"

    def __init__(
        self,
        app_url: str,
        platform_url: str = "",
        admin_url: str = "",
        *,
        pool_size: int = 24,
    ) -> None:
        super().__init__(Path("postgresql"))
        if not app_url:
            raise ValueError("AIRA_CLOUD_DATABASE_URL is required for PostgreSQL.")
        self.app_url = app_url
        self.platform_url = platform_url or app_url
        self.admin_url = admin_url
        self.separated = bool(platform_url) and platform_url != app_url
        self.pool_size = pool_size
        self.primary_keys_key = hashlib.sha1(  # noqa: S324 - a cache key, not security
            f"{app_url}|{platform_url}".encode()
        ).hexdigest()
        self._pools: dict[str, psycopg_pool.ConnectionPool[Any]] = {}
        self._pool_lock = threading.Lock()
        if not self.separated:
            logger.warning(
                "No separate platform database role is configured: row-level security cannot protect tenant requests."
            )

    # -- pools ---------------------------------------------------------------------------------

    def _pool(self, name: str) -> psycopg_pool.ConnectionPool[Any]:
        pool = self._pools.get(name)
        if pool is not None:
            return pool
        with self._pool_lock:
            pool = self._pools.get(name)
            if pool is None:
                url = self.app_url if name == "tenant" else self.platform_url
                pool = psycopg_pool.ConnectionPool(
                    url,
                    min_size=0,
                    max_size=self.pool_size,
                    timeout=30,
                    configure=_configure,
                    open=True,
                    name=f"aira-{name}",
                )
                self._pools[name] = pool
        return pool

    def close(self) -> None:
        with self._pool_lock:
            pools, self._pools = list(self._pools.values()), {}
        for pool in pools:
            pool.close()

    # -- migration -------------------------------------------------------------------------------

    def migrate(self) -> int:
        with self._migrate_lock:
            if not self.admin_url:
                return self._verify_schema()
            return self._apply(self.admin_url)

    def _verify_schema(self) -> int:
        try:
            version = self.version(_checked=False)
        except psycopg.Error:
            version = 0
        if version < MIGRATIONS[-1][0]:
            raise RuntimeError(
                f"The database schema is at version {version} but the software needs {MIGRATIONS[-1][0]}. "
                "Run 'python -m aira_cloud migrate' with AIRA_CLOUD_DATABASE_ADMIN_URL set."
            )
        self._ready = True
        self._load_primary_keys()
        return 0

    def _apply(self, admin_url: str) -> int:
        applied = 0
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute("SELECT pg_advisory_lock(%s)", (MIGRATE_LOCK,))
            try:
                admin.execute(
                    "CREATE TABLE IF NOT EXISTS cloud_migrations (version BIGINT PRIMARY KEY, name TEXT NOT NULL, applied_at BIGINT NOT NULL)"
                )
                done = {
                    r[0] for r in admin.execute("SELECT version FROM cloud_migrations")
                }
                for version, name, script in MIGRATIONS:
                    if version in done:
                        continue
                    with admin.transaction():
                        for statement in _statements(script):
                            if dialect.is_trigger(statement):
                                continue
                            admin.execute(_sql(dialect.ddl(statement)))
                        admin.execute(
                            "INSERT INTO cloud_migrations (version, name, applied_at) VALUES (%s, %s, extract(epoch from now())::bigint)",
                            (version, name),
                        )
                    applied += 1
                self._setup(admin)
            finally:
                admin.execute("SELECT pg_advisory_unlock(%s)", (MIGRATE_LOCK,))
        self._ready = True
        self._load_primary_keys()
        return applied

    def _setup(self, admin: psycopg.Connection[Any]) -> None:
        """Audit guards, grants and row-level security for the live schema (idempotent)."""
        for statement in dialect.EXTRA_DDL:
            admin.execute(_sql(statement))
        columns: dict[str, list[str]] = {}
        for table, column in admin.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name IN (SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE') "
            "ORDER BY table_name, ordinal_position"
        ):
            columns.setdefault(table, []).append(column)
        scoped = dialect.tenant_columns(columns)
        roles = {
            r[0]
            for r in admin.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                ([dialect.APP_ROLE, dialect.PLATFORM_ROLE],),
            )
        }
        two_roles = {dialect.APP_ROLE, dialect.PLATFORM_ROLE} <= roles
        statements = [*dialect.AUDIT_GUARDS]
        statements += dialect.rls_statements(scoped, force=two_roles)
        for role in sorted(roles):
            tenant = role == dialect.APP_ROLE
            statements.append(f"GRANT USAGE ON SCHEMA public TO {role}")
            for table in sorted(columns):
                if tenant and table in dialect.PLATFORM_ONLY_TABLES:
                    statements.append(f"REVOKE ALL ON {table} FROM {role}")
                else:
                    statements.append(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"
                    )
            # setval (a restore moves identity sequences past restored rows) needs UPDATE: platform role only
            privileges = "USAGE, SELECT" if tenant else "USAGE, SELECT, UPDATE"
            statements.append(
                f"GRANT {privileges} ON ALL SEQUENCES IN SCHEMA public TO {role}"
            )
        digest = hashlib.sha256("\n".join(statements).encode()).hexdigest()
        row = admin.execute(
            "SELECT value FROM cloud_kv WHERE key = 'pg_setup'"
        ).fetchone()
        if row and row[0] == digest:
            return
        with admin.transaction():
            for statement in statements:
                admin.execute(_sql(statement))
            admin.execute(
                "INSERT INTO cloud_kv (key, value) VALUES ('pg_setup', %s) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (digest,),
            )

    def _load_primary_keys(self) -> None:
        with self._pool("platform").connection() as raw:
            rows = raw.execute(
                "SELECT c.relname AS t, a.attname AS col FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indisprimary AND c.relnamespace = 'public'::regnamespace "
                "ORDER BY c.relname, array_position(i.indkey, a.attnum)"
            ).fetchall()
        keys: dict[str, list[str]] = {}
        for row in rows:
            keys.setdefault(row[0], []).append(row[1])
        _PRIMARY_KEYS[self.primary_keys_key] = {t: tuple(c) for t, c in keys.items()}
        _translated_cached.cache_clear()

    def version(self, *, _checked: bool = True) -> int:  # type: ignore[override]
        if _checked and not self._ready:
            self.migrate()
        with self._pool("platform").connection() as raw:
            row = raw.execute("SELECT MAX(version) FROM cloud_migrations").fetchone()
        return int((row[0] if row else 0) or 0)

    # -- transactions ----------------------------------------------------------------------------

    @contextmanager
    def _transaction(self, *, exclusive: bool) -> Iterator[PgConnection]:
        if not self._ready:
            self.migrate()
        scope = current_scope()
        name = "tenant" if scope and self.separated else "platform"
        with self._pool(name).connection() as raw:
            if name == "tenant":
                raw.execute("SELECT set_config('app.org_id', %s, true)", (scope,))
            if exclusive:
                raw.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
            yield PgConnection(raw, self, tenant=name == "tenant")

    @contextmanager
    def read(self) -> Iterator[Any]:
        with self._transaction(exclusive=False) as connection:
            yield connection

    @contextmanager
    def write(self, *, exclusive: bool = True) -> Iterator[Any]:
        """One atomic write transaction. ``exclusive`` serializes writers (SQLite's behaviour); code that
        takes its own finer locks (the job lease, usage counters) passes ``exclusive=False``."""
        with self._transaction(exclusive=exclusive) as connection:
            yield connection


__all__ = ["PostgresDB", "PgConnection", "Row", "Decimal"]
