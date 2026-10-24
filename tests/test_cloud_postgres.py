"""PostgreSQL-only guarantees: row-level security, the append-only audit triggers, atomic counters,
the job lease under many workers, and the SQLite-to-PostgreSQL migration.

Skipped unless ``AIRA_TEST_DATABASE_URL`` points at a PostgreSQL superuser (see tests/pg_support.py).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pg_support
import pytest
from cloud_support import add_member, build_org, make_app, signup  # noqa: F401

pytestmark = pytest.mark.skipif(
    not pg_support.enabled(), reason="AIRA_TEST_DATABASE_URL is not set"
)

psycopg = pytest.importorskip("psycopg")

from aira_cloud import dialect  # noqa: E402
from aira_cloud.db import platform_scope, tenant_scope  # noqa: E402
from aira_cloud.entitlements import LimitReached  # noqa: E402


def connect(app, role: str = "app", **kwargs):
    url = {
        "app": app.config.database_url,
        "platform": app.config.database_platform_url,
        "owner": app.config.database_admin_url,
    }[role]
    return psycopg.connect(url, **kwargs)


@pytest.fixture
def two_orgs(cloud):
    one = build_org(cloud, "One Org", plan="team", seats=10, slug_email="one.test")
    two = build_org(cloud, "Two Org", plan="team", seats=10, slug_email="two.test")
    return one, two


# ----------------------------------------------------------------------------------- row-level security


def test_without_a_tenant_the_application_role_sees_no_rows_at_all(cloud, two_orgs):
    with connect(cloud) as connection:
        for table in (
            "members",
            "organizations",
            "subscriptions",
            "sessions",
            "audit_logs",
        ):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, (
                f"{table} leaked {count} rows to a connection with no tenant"
            )


def test_a_missing_where_clause_returns_only_the_callers_organization(cloud, two_orgs):
    one, two = two_orgs
    with connect(cloud) as connection:
        connection.execute("SELECT set_config('app.org_id', %s, false)", (one.org_id,))
        orgs = {r[0] for r in connection.execute("SELECT DISTINCT org_id FROM members")}
        assert orgs == {one.org_id}
        everything = connection.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        assert (
            everything == 7
        )  # owner, admin, coach, two candidates, billing contact, auditor
        # nor can it read the other tenant by asking for it
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM members WHERE org_id = %s", (two.org_id,)
            ).fetchone()[0]
            == 0
        )


def test_writes_for_another_organization_are_refused_or_touch_nothing(cloud, two_orgs):
    one, two = two_orgs
    with connect(cloud) as connection:
        connection.execute("SELECT set_config('app.org_id', %s, false)", (one.org_id,))
        updated = connection.execute(
            "UPDATE members SET name = 'hijacked' WHERE org_id = %s", (two.org_id,)
        )
        assert updated.rowcount == 0
        deleted = connection.execute(
            "DELETE FROM invoices WHERE org_id = %s", (two.org_id,)
        )
        assert deleted.rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "INSERT INTO org_notices (org_id, level, title, created_at) VALUES (%s, 'info', 'x', 1)",
                (two.org_id,),
            )
        connection.rollback()
        connection.execute("SELECT set_config('app.org_id', %s, false)", (one.org_id,))
        connection.execute(
            "INSERT INTO org_notices (org_id, level, title, created_at) VALUES (%s, 'info', 'mine', 1)",
            (one.org_id,),
        )
    names = {
        r[0]
        for r in connect(cloud, "platform").execute(
            "SELECT name FROM members WHERE org_id = %s", (two.org_id,)
        )
    }
    assert "hijacked" not in names


def test_the_adapter_scopes_tenant_code_and_leaves_platform_code_alone(cloud, two_orgs):
    one, two = two_orgs
    with tenant_scope(one.org_id), cloud.db.read() as connection:
        forgetful = connection.execute("SELECT org_id FROM members").fetchall()
        assert {r["org_id"] for r in forgetful} == {one.org_id}
    with (
        cloud.db.read() as connection
    ):  # no scope: sign-in, webhooks, the scheduler tick
        assert {
            r["org_id"] for r in connection.execute("SELECT org_id FROM members")
        } == {
            one.org_id,
            two.org_id,
        }
    with tenant_scope(one.org_id), platform_scope(), cloud.db.read() as connection:
        assert (
            connection.execute("SELECT COUNT(DISTINCT org_id) FROM members").fetchone()[
                0
            ]
            == 2
        )


def test_authenticated_requests_run_under_row_level_security(cloud, two_orgs):
    """The gateway enters the caller's scope, so a handler that forgets its filter still cannot cross."""
    from cloud_support import sign_in

    one, two = two_orgs
    owner = sign_in(cloud, one.owner)
    status, body = owner.call("GET", "/api/org/members")
    assert status == 200
    emails = {m["email"] for m in body["members"]}
    assert emails and all(e.endswith("@one.test") for e in emails)
    assert owner.call("GET", f"/api/org/members/{two.candidate.member_id}")[0] == 404


def test_the_application_role_cannot_switch_row_level_security_off(cloud, two_orgs):
    with connect(cloud, autocommit=True) as connection:
        for statement, error in (
            (
                "ALTER TABLE members DISABLE ROW LEVEL SECURITY",
                psycopg.errors.InsufficientPrivilege,
            ),
            (
                "ALTER TABLE members NO FORCE ROW LEVEL SECURITY",
                psycopg.errors.InsufficientPrivilege,
            ),
            (
                "DROP POLICY tenant_isolation ON members",
                psycopg.errors.InsufficientPrivilege,
            ),
            ("SET ROLE aira_platform", psycopg.errors.InsufficientPrivilege),
            (
                "SET row_security = off; SELECT * FROM members",
                psycopg.errors.InsufficientPrivilege,
            ),
            ("CREATE TABLE sneaky (x int)", psycopg.errors.InsufficientPrivilege),
            ("SELECT * FROM operators", psycopg.errors.InsufficientPrivilege),
            ("SELECT * FROM platform_audit_logs", psycopg.errors.InsufficientPrivilege),
            (
                "INSERT INTO platform_audit_logs (audit_id, at, action) VALUES ('x', 1, 'x')",
                psycopg.errors.InsufficientPrivilege,
            ),
        ):
            with pytest.raises(error):
                connection.execute(statement)
        role = connection.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert tuple(role) == (False, False)


def test_even_the_table_owner_is_subject_to_the_policies(cloud, two_orgs):
    with connect(cloud, "owner") as connection:
        assert connection.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0


def test_every_table_is_classified_for_row_level_security(cloud):
    """A new table must be named global, platform-only or carry an organization policy, or this fails."""
    with connect(cloud, "platform") as connection:
        tables = {
            r[0]: (r[1], r[2])
            for r in connection.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            )
        }
        policies = {
            r[0]
            for r in connection.execute(
                "SELECT tablename FROM pg_policies WHERE schemaname = 'public'"
            )
        }
        columns: dict[str, set[str]] = {}
        for table, column in connection.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
        ):
            columns.setdefault(table, set()).add(column)
    for table, (enabled, forced) in tables.items():
        if table in dialect.GLOBAL_TABLES:
            assert not enabled, f"{table} is global and carries no policy"
            continue
        assert enabled and forced, (
            f"{table} must have row-level security enabled and forced"
        )
        if table in dialect.PLATFORM_ONLY_TABLES:
            assert table not in policies, f"{table} is platform-only: no tenant policy"
        else:
            column = dialect.ORG_COLUMN_OVERRIDES.get(table, "org_id")
            assert column in columns[table], (
                f"{table} has no {column}: classify it in dialect.py"
            )
            assert table in policies, f"{table} has no tenant policy"


# ----------------------------------------------------------------------------------- audit triggers


def test_the_audit_triggers_refuse_update_and_delete(cloud):
    from aira_cloud.db import INTEGRITY_ERRORS

    org = build_org(cloud, "Audit Org", plan="starter", seats=5)
    for statement in (
        "UPDATE audit_logs SET action = 'x' WHERE org_id = ?",
        "DELETE FROM audit_logs WHERE org_id = ?",
    ):
        with pytest.raises(INTEGRITY_ERRORS):
            with cloud.db.write() as connection:
                connection.execute(statement, (org.org_id,))
    cloud.platform_audit.record("test.platform_event", org_id=org.org_id)
    for statement in (
        "UPDATE platform_audit_logs SET action = 'x'",
        "DELETE FROM platform_audit_logs",
    ):
        with pytest.raises(INTEGRITY_ERRORS):
            with cloud.db.write() as connection:
                connection.execute(statement)
    assert cloud.audit.verify(org.org_id)["ok"]
    # maintenance (pruning, deleting an organization) is the only way rows leave
    cloud.clock.advance(days=400)
    cloud.audit.record(org.org_id, "test.fresh")
    assert cloud.audit.prune(org.org_id, cloud.clock.now() - 1) >= 1
    assert cloud.audit.verify(org.org_id)["ok"]


# ----------------------------------------------------------------------------------- atomic counters


def test_usage_reservations_stay_atomic_under_threads(cloud):
    org = build_org(cloud, "Counter Org", plan="team", seats=10)
    limit, attempts = 40, 160
    granted, refused, errors = [], [], []

    def reserve(_: int) -> None:
        try:
            cloud.usage.reserve(org.org_id, "applications", 1, monthly_limit=limit)
            granted.append(1)
        except LimitReached:
            refused.append(1)
        except Exception as error:  # noqa: BLE001 - the test reports whatever else happened
            errors.append(repr(error))

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(reserve, range(attempts)))
    assert not errors, errors[:3]
    assert len(granted) == limit and len(refused) == attempts - limit
    assert cloud.usage.used(org.org_id, "applications") == limit


def test_concurrent_audit_records_keep_one_unbroken_chain(cloud):
    org = build_org(cloud, "Chain Org", plan="team", seats=10)

    def record(i: int) -> None:
        cloud.audit.record(org.org_id, f"test.event.{i}")

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(record, range(60)))
    result = cloud.audit.verify(org.org_id)
    assert result["ok"], result


# ----------------------------------------------------------------------------------- the job lease


def _enqueue(cloud, org, candidate_id: str, n: int) -> list[str]:
    ids = []
    for i in range(n):
        job_id = cloud.queue.enqueue(
            org.org_id,
            "autopilot.exec",
            candidate_id=candidate_id,
            payload={"i": i},
            dedupe_key=f"{candidate_id}:{i}",
        )
        assert job_id
        ids.append(job_id)
    return ids


def test_many_workers_never_lease_the_same_job_or_break_the_caps(cloud):
    orgs = [
        build_org(
            cloud, f"Lease Org {i}", plan="team", seats=10, slug_email=f"lease{i}.test"
        )
        for i in range(4)
    ]
    cap = cloud.config.per_org_concurrency
    for org in orgs:
        for candidate in (org.candidate, org.other):
            _enqueue(cloud, org, candidate.member_id, 6)
    leased: list[dict] = []
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(index: int) -> None:
        barrier.wait()
        for _ in range(40):
            job = cloud.queue.lease(f"w{index}")
            if job is None:
                time.sleep(0.01)
                continue
            with guard:
                leased.append(job)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))
    ids = [j["job_id"] for j in leased]
    assert len(ids) == len(set(ids)), "a job was leased twice"
    by_org: dict[str, int] = {}
    by_candidate: dict[tuple[str, str], int] = {}
    for job in leased:
        by_org[job["org_id"]] = by_org.get(job["org_id"], 0) + 1
        key = (job["org_id"], job["candidate_id"])
        by_candidate[key] = by_candidate.get(key, 0) + 1
    assert all(n <= cap for n in by_org.values()), by_org
    assert all(n == 1 for n in by_candidate.values()), by_candidate
    assert len(by_org) == 4, "every organization got work"


def test_a_job_another_worker_is_claiming_is_skipped_not_waited_for(cloud):
    org = build_org(cloud, "Skip Org", plan="team", seats=10, slug_email="skip.test")
    first, second = org.candidate, org.other
    _enqueue(cloud, org, first.member_id, 1)
    _enqueue(cloud, org, second.member_id, 1)
    with connect(cloud, "platform") as holder:
        rows = holder.execute(
            "SELECT job_id FROM queue_jobs WHERE org_id = %s ORDER BY created_at, job_id FOR UPDATE",
            (org.org_id,),
        ).fetchall()
        assert len(rows) == 2
        # both rows are locked by the other session: the lease must come back at once with nothing,
        # instead of blocking behind the lock until it is released
        started = time.monotonic()
        job = cloud.queue.lease("skipper")
        assert job is None
        assert time.monotonic() - started < 5
        holder.rollback()
    assert cloud.queue.lease("skipper") is not None


# ----------------------------------------------------------------------------------- SQLite to PostgreSQL


def test_the_sqlite_control_plane_moves_to_postgres_intact(tmp_path):
    from aira_cloud import migrate_pg
    from cloud_support import PASSWORD, sign_in

    source = make_app(tmp_path / "sqlite-install", db_backend="sqlite")
    org = build_org(source, "Mover Org", plan="team", seats=10, slug_email="mover.test")
    sign_in(source, org.owner)
    source.audit.record(org.org_id, "test.before_move")
    audit_rows = source.audit.count(org.org_id)
    source.close()

    database = pg_support.new_database()
    summary = migrate_pg.migrate(
        tmp_path / "sqlite-install" / "cloud" / "control.sqlite3",
        database["database_url"],
        database["database_platform_url"],
        database["database_admin_url"],
    )
    assert summary["verified"] and summary["rows"] > 20
    again = migrate_pg.migrate(
        tmp_path / "sqlite-install" / "cloud" / "control.sqlite3",
        database["database_url"],
        database["database_platform_url"],
        database["database_admin_url"],
        verify_only=True,
    )
    assert again["applied"] is False

    moved = make_app(
        tmp_path / "sqlite-install",
        **{k: v for k, v in database.items() if not k.startswith("_")},
    )
    try:
        assert moved.audit.count(org.org_id) == audit_rows
        assert moved.audit.verify(org.org_id)["ok"]
        assert moved.identity.login(org.owner.email, PASSWORD, org=org.org_id)["token"]
        # identity sequences continue after the copied rows
        before = moved.audit.count(org.org_id)
        moved.audit.record(org.org_id, "test.after_move")
        assert moved.audit.count(org.org_id) == before + 1
        assert moved.audit.verify(org.org_id)["ok"]
    finally:
        moved.close()


def test_without_the_owner_url_the_application_only_checks_the_schema(cloud):
    from aira_cloud.pgdb import PostgresDB

    config = cloud.config
    checking = PostgresDB(
        config.database_url, config.database_platform_url, "", pool_size=2
    )
    try:
        assert checking.migrate() == 0 and checking.version() >= 3
    finally:
        checking.close()

    import psycopg as pg

    with pg.connect(pg_support.admin_url(), autocommit=True) as admin:
        admin.execute("CREATE DATABASE aira_empty_check OWNER aira_owner")
    try:
        empty = PostgresDB(
            pg_support._url(pg_support.APP, "aira_empty_check"),
            pg_support._url(pg_support.PLATFORM, "aira_empty_check"),
            "",
            pool_size=2,
        )
        with pytest.raises(RuntimeError, match="migrate"):
            empty.migrate()
        empty.close()
    finally:
        with pg.connect(pg_support.admin_url(), autocommit=True) as admin:
            admin.execute("DROP DATABASE IF EXISTS aira_empty_check WITH (FORCE)")


def test_the_migrate_to_postgres_command_copies_and_verifies(
    tmp_path, monkeypatch, capsys
):
    from aira_cloud.__main__ import main

    source = make_app(tmp_path / "install", db_backend="sqlite")
    org = build_org(
        source, "Cli Mover", plan="team", seats=10, slug_email="climover.test"
    )
    source.close()
    database = pg_support.new_database()
    pg_support.export_env(database)
    data = str(tmp_path / "install" / "cloud")
    assert (
        main(["--data", data, "migrate-to-postgres", "--verify-only"]) == 1
    )  # nothing copied yet
    capsys.readouterr()
    assert main(["--data", data, "migrate-to-postgres"]) == 0
    assert '"verified": true' in capsys.readouterr().out
    assert main(["--data", data, "migrate-to-postgres"]) == 1, (
        "a second copy into a non-empty database is refused"
    )
    with psycopg.connect(database["database_platform_url"]) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM organizations WHERE org_id = %s", (org.org_id,)
            ).fetchone()[0]
            == 1
        )
