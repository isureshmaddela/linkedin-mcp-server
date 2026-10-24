"""Move an installation's control plane from SQLite to PostgreSQL.

``python -m aira_cloud migrate-to-postgres`` copies every table of ``control.sqlite3`` into an
empty PostgreSQL database (created and migrated with ``AIRA_CLOUD_DATABASE_ADMIN_URL``), keeps the
identity sequences ahead of the copied rows, and compares row counts table by table. Workspaces and
documents are files and do not move. Stop the web tier and the workers first: a copy taken while the
application writes is not consistent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from aira_cloud.pgdb import PostgresDB

BATCH = 500
# not copied: PostgreSQL keeps its own migration record and setup marker
SKIP = {"cloud_migrations"}
SKIP_KV = {"pg_setup"}


class MigrationError(RuntimeError):
    pass


def _sqlite_tables(source: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        if r[0] not in SKIP
    ]


def migrate(
    sqlite_path: Path,
    app_url: str,
    platform_url: str,
    admin_url: str,
    *,
    verify_only: bool = False,
) -> dict[str, Any]:
    """Copy the control plane, or with ``verify_only`` just compare counts (no writes)."""
    if not Path(sqlite_path).exists():
        raise MigrationError(f"No SQLite control plane at {sqlite_path}.")
    source = sqlite3.connect(f"file:{Path(sqlite_path).as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = PostgresDB(app_url, platform_url, admin_url, pool_size=2)
    try:
        target.migrate()  # creates the schema, the guards and row-level security
        tables = _sqlite_tables(source)
        copied: dict[str, int] = {}
        with target._pool("platform").connection() as raw:
            existing = {
                row[0]
                for row in raw.execute("SELECT org_id FROM organizations LIMIT 1")
            }
            if existing and not verify_only:
                raise MigrationError(
                    "The PostgreSQL database already holds organizations; migrate into an empty database."
                )
            if not verify_only:
                raw.execute("SET CONSTRAINTS ALL DEFERRED")
                for table in tables:
                    columns = [
                        r[1] for r in source.execute(f"PRAGMA table_info({table})")
                    ]
                    pg_columns = {
                        r[0]
                        for r in raw.execute(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
                            (table,),
                        )
                    }
                    use = [c for c in columns if c in pg_columns]
                    if not use:
                        raise MigrationError(
                            f"Table {table} does not exist in PostgreSQL."
                        )
                    # rows seeded by the application on first start would clash with the copy
                    if table in {
                        "plans",
                        "plan_versions",
                        "feature_flags",
                        "blocked_sources",
                    }:
                        raw.execute(f"DELETE FROM {table}")
                    insert = (
                        f"INSERT INTO {table} ({', '.join(use)}) VALUES ({', '.join(['%s'] * len(use))}) "
                        "ON CONFLICT DO NOTHING"
                    )
                    rows = source.execute(f"SELECT {', '.join(use)} FROM {table}")
                    count = 0
                    while True:
                        batch = rows.fetchmany(BATCH)
                        if not batch:
                            break
                        values = []
                        for row in batch:
                            record = tuple(row)
                            if table == "cloud_kv" and record[0] in SKIP_KV:
                                continue
                            values.append(record)
                        if values:
                            with raw.cursor() as cursor:
                                cursor.executemany(insert, values)
                        count += len(batch)
                    copied[table] = count
                    for row in raw.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s AND is_identity = 'YES'",
                        (table,),
                    ).fetchall():
                        column = row[0]
                        raw.execute(
                            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), COALESCE((SELECT MAX({column}) FROM {table}), 0) + 1, false)"
                        )
        mismatches: dict[str, tuple[int, int]] = {}
        with target._pool("platform").connection() as raw:
            for table in tables:
                if table == "cloud_kv":
                    continue  # one setup marker differs by design
                expected = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                actual = raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if expected != actual:
                    mismatches[table] = (expected, actual)
        if mismatches:
            raise MigrationError(
                "Row counts differ after the copy: "
                + ", ".join(
                    f"{t} sqlite={a} postgres={b}" for t, (a, b) in mismatches.items()
                )
            )
        return {
            "tables": len(tables),
            "rows": sum(copied.values()) if copied else 0,
            "verified": True,
            "applied": not verify_only,
        }
    finally:
        source.close()
        target.close()
