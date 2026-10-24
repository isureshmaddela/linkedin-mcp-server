"""Run the hosted-shell tests against a real PostgreSQL.

Set ``AIRA_TEST_DATABASE_URL`` to a superuser URL on an empty cluster, for example
``postgresql://postgres:secret@127.0.0.1:5432/postgres``. The first test in each process creates the
three roles (owner, app, platform) and a migrated template database; every test then gets a fresh
database copied from the template and dropped afterwards.
"""

from __future__ import annotations

import itertools
import os
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ADMIN_URL = os.environ.get("AIRA_TEST_DATABASE_URL", "")
PASSWORD = "aira-test-pw"
OWNER, APP, PLATFORM = "aira_owner", "aira_app", "aira_platform"

_lock = threading.Lock()
_counter = itertools.count(1)
_template: str | None = None
_created: list[str] = []


def enabled() -> bool:
    return bool(ADMIN_URL)


def _url(user: str, database: str, password: str = PASSWORD) -> str:
    parts = urlsplit(ADMIN_URL)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, f"/{database}", "", ""))


def admin_url(database: str = "postgres") -> str:
    parts = urlsplit(ADMIN_URL)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", ""))


def _ensure_roles(admin: Any) -> None:
    for role, attributes in (
        (OWNER, "LOGIN NOBYPASSRLS"),
        (APP, "LOGIN NOBYPASSRLS"),
        (PLATFORM, "LOGIN BYPASSRLS"),
    ):
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if not exists:
            admin.execute(f"CREATE ROLE {role} {attributes} PASSWORD '{PASSWORD}'")


def _template_database() -> str:
    global _template
    with _lock:
        if _template:
            return _template
        import psycopg

        from aira_cloud.pgdb import PostgresDB

        name = f"aira_tpl_{os.getpid()}"
        with psycopg.connect(admin_url(), autocommit=True) as admin:
            admin.execute("SELECT pg_advisory_lock(7331)")
            try:
                _ensure_roles(admin)
            finally:
                admin.execute("SELECT pg_advisory_unlock(7331)")
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {name} OWNER {OWNER}")
        db = PostgresDB(
            _url(APP, name), _url(PLATFORM, name), _url(OWNER, name), pool_size=2
        )
        db.migrate()
        db.close()
        _template = name
        return name


_by_root: dict[str, dict[str, Any]] = {}


def new_database(data_root: str = "") -> dict[str, Any]:
    """Configuration values for a fresh database copied from the migrated template.

    An app built again over the same data directory (a test that restarts "the process") gets the same
    database back, as it would on a real installation.
    """
    import psycopg

    if data_root and data_root in _by_root:
        return dict(_by_root[data_root])
    template = _template_database()
    name = f"aira_t_{os.getpid()}_{next(_counter)}"
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name} TEMPLATE {template} OWNER {OWNER}")
    with _lock:
        _created.append(name)
    config = {
        "db_backend": "postgres",
        "database_url": _url(APP, name),
        "database_platform_url": _url(PLATFORM, name),
        "database_admin_url": _url(OWNER, name),
        "database_pool_size": 8,
        "_name": name,
    }
    if data_root:
        _by_root[data_root] = dict(config)
    return config


_ENV = {
    "db_backend": "AIRA_CLOUD_DB",
    "database_url": "AIRA_CLOUD_DATABASE_URL",
    "database_platform_url": "AIRA_CLOUD_DATABASE_PLATFORM_URL",
    "database_admin_url": "AIRA_CLOUD_DATABASE_ADMIN_URL",
}


def export_env(config: dict[str, Any]) -> None:
    """Point the environment at the test's database too, for tests that run the command line (it builds its own config)."""
    for key, name in _ENV.items():
        os.environ[name] = str(config[key])


def drop_created(keep: frozenset[str] | set[str] = frozenset()) -> None:
    """Drop the databases this process created (after each test, and at exit).

    ``keep`` names databases that outlive one test: a module-scoped fixture's installation, made before the
    test started, is dropped at exit instead.
    """
    if not keep:
        for name in _ENV.values():
            os.environ.pop(name, None)
    if not _created:
        return
    import psycopg

    with _lock:
        names = [n for n in _created if n not in keep]
        _created[:] = [n for n in _created if n in keep]
        if not keep:
            _by_root.clear()
    if not names:
        return
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        for name in names:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def drop_template() -> None:
    global _template
    if _template:
        import psycopg

        with psycopg.connect(admin_url(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {_template} WITH (FORCE)")
        _template = None
