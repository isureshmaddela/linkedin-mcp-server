"""The accounts database on the signed-in request path: migrated once, read without writing, purged on a timer."""

from __future__ import annotations

import sqlite3
import time

import pytest

import auto_apply_assistant as app


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    return tmp_path / "accounts.sqlite3"


def _data_version(path) -> int:
    observer = sqlite3.connect(path)
    try:
        return observer.execute("PRAGMA data_version").fetchone()[0]
    finally:
        observer.close()


def test_the_schema_is_migrated_once_per_file(accounts, monkeypatch):
    user, token = app._create_account("owner@example.com", "a long app password 123")
    calls = []
    monkeypatch.setattr(app, "_migrate_auth_schema", lambda connection: calls.append(1))
    assert app._user_for_session(token)["user_id"] == user["user_id"]
    with app._auth_connect() as connection:
        connection.execute("SELECT 1 FROM users").fetchall()
    assert calls == [], "a known file is not migrated again"


def test_a_replaced_accounts_file_is_migrated_again(accounts):
    app._create_account("owner@example.com", "a long app password 123")
    accounts.unlink()
    for suffix in ("-wal", "-shm"):
        accounts.with_name(accounts.name + suffix).unlink(missing_ok=True)
    with app._auth_connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"users", "sessions", "password_resets"} <= tables


def test_an_older_accounts_file_gains_the_linked_account_columns(accounts):
    legacy = sqlite3.connect(accounts)
    legacy.execute(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, "
        "data_path TEXT NOT NULL, linkedin_port INTEGER, login_pid INTEGER, worker_pid INTEGER, "
        "created_at INTEGER NOT NULL)"
    )
    legacy.commit()
    legacy.close()
    with app._auth_connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    assert {
        "account_linked_at",
        "account_org_id",
        "account_org_name",
        "account_url",
    } <= columns


def test_the_accounts_database_uses_wal(accounts):
    app._create_account("owner@example.com", "a long app password 123")
    with app._auth_connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_session_lookup_writes_nothing(accounts):
    user, token = app._create_account("owner@example.com", "a long app password 123")
    assert (
        app._user_for_session(token)["user_id"] == user["user_id"]
    )  # this one may purge
    before = _data_version(accounts)
    for _ in range(5):
        assert app._user_for_session(token)["user_id"] == user["user_id"]
    assert _data_version(accounts) == before, "the lookups committed a change"


def test_expired_sessions_are_refused_and_purged_at_most_once_a_minute(accounts):
    user, token = app._create_account("owner@example.com", "a long app password 123")
    now = time.time()
    assert app._purge_expired_sessions(now) is True
    expired = app._issue_session(user["user_id"])
    with app._auth_connect() as connection:
        connection.execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
            (int(now) - 1, app.hashlib.sha256(expired.encode("ascii")).hexdigest()),
        )

    def stored() -> int:
        with app._auth_connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    assert app._user_for_session(expired) is None, "an expired session never signs in"
    assert stored() == 2, "it is not deleted on the request path"
    assert app._purge_expired_sessions(now + 30) is False
    assert stored() == 2, "a purge within the minute is skipped"
    assert app._purge_expired_sessions(now + app.SESSION_PURGE_INTERVAL_SECONDS + 1)
    assert stored() == 1
    assert app._user_for_session(token)["user_id"] == user["user_id"], (
        "live sessions stay"
    )
