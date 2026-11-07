"""API persistence: searches, usage, saved items, keys, cache and secrets."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from aira import schema
from aira.airaapi import store

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    # A plain connection on purpose: the store must not depend on row_factory.
    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    yield connection
    connection.close()


def add(db, *, engine="google", age_minutes=0, **overrides):
    row = {
        "id": store.new_id(),
        "engine": engine,
        "params_json": '{"q": "coffee"}',
        "params_hash": "hash-a",
        "status": "ok",
        "source": "playground",
        "created_at": store.to_iso(NOW - timedelta(minutes=age_minutes)),
        "credits": 1,
    }
    row.update(overrides)
    blob = store.pack({"organic_results": [{"title": "x"}]})
    store.insert_search(db, row, result_blob=blob)
    return row["id"]


def test_schema_is_idempotent_and_lists_every_table(db):
    store.ensure_schema(db)
    names = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "airaapi_searches",
        "airaapi_saved",
        "airaapi_monitors",
        "airaapi_usage",
        "airaapi_secrets",
        "airaapi_api_keys",
        "airaapi_cache",
    } <= names


def test_migration_runs_once_on_a_fresh_user_database():
    connection = sqlite3.connect(":memory:")
    applied = schema.migrate(connection)
    assert 12 in applied
    assert schema.migrate(connection) == []
    assert (
        connection.execute("SELECT COUNT(*) FROM airaapi_searches").fetchone()[0] == 0
    )
    name = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = 12"
    ).fetchone()[0]
    assert name == "airaapi"


def test_time_helpers_round_trip():
    assert store.to_iso(NOW) == "2026-10-22T12:00:00Z"
    assert store.from_iso("2026-10-22T12:00:00Z") == NOW
    assert store.from_iso("2026-10-22T14:00:00+02:00") == NOW
    assert store.now_utc().tzinfo is not None


def test_pack_round_trips_unicode_and_compresses():
    payload = {"title": "Café ☕ 日本語", "items": ["x" * 100] * 50}
    blob = store.pack(payload)
    assert store.unpack(blob) == payload
    assert len(blob) < len(str(payload))
    assert store.unpack(None) is None
    assert store.unpack_text(store.pack_text("<p>héllo</p>")) == "<p>héllo</p>"
    assert store.unpack_text(None) is None


def test_insert_get_and_blobs(db):
    search_id = add(db, label="first", serpapi_id="s-1")
    row = store.get_search_row(db, search_id)
    assert row is not None and row["label"] == "first" and row["serpapi_id"] == "s-1"
    assert row["has_result"] == 1 and row["has_html"] == 0
    result_blob, html_blob = store.get_blobs(db, search_id)
    assert store.unpack(result_blob)["organic_results"][0]["title"] == "x"
    assert html_blob is None
    store.set_blobs(db, search_id, html_blob=store.pack_text("<b>page</b>"))
    assert store.unpack_text(store.get_blobs(db, search_id)[1]) == "<b>page</b>"
    assert store.get_blobs(db, "missing") == (None, None)
    assert store.get_search_row(db, "missing") is None
    with pytest.raises(ValueError, match="Unknown search columns"):
        store.insert_search(
            db, {"id": "x", "engine": "g", "created_at": "t", "bogus": 1}
        )


def test_update_search_whitelists_columns(db):
    search_id = add(db)
    assert store.update_search(db, search_id, {"pinned": 1, "label": "keep"})
    row = store.get_search_row(db, search_id)
    assert row is not None and row["pinned"] == 1 and row["label"] == "keep"
    with pytest.raises(ValueError, match="Cannot update"):
        store.update_search(db, search_id, {"engine": "other"})
    assert not store.update_search(db, search_id, {})
    assert not store.update_search(db, "missing", {"label": "x"})


def test_list_filters_ordering_and_paging(db):
    ids = [
        add(db, engine="google", age_minutes=50, label="Coffee roasters"),
        add(
            db,
            engine="bing",
            age_minutes=40,
            source="api",
            status="error",
            error="boom",
        ),
        add(db, engine="google_jobs", age_minutes=30, pinned=1),
        add(
            db, engine="google", age_minutes=20, cache_hit=1, params_json='{"q": "tea"}'
        ),
        add(db, engine="google", age_minutes=10),
    ]
    rows, total = store.list_searches(db)
    assert total == 5 and [row["id"] for row in rows] == ids[::-1]
    page, total = store.list_searches(db, limit=2, offset=2)
    assert total == 5 and [row["id"] for row in page] == [ids[2], ids[1]]
    assert store.list_searches(db, engine="google")[1] == 3
    assert store.list_searches(db, status="error")[1] == 1
    assert store.list_searches(db, source="api")[1] == 1
    assert store.list_searches(db, pinned=True)[1] == 1
    assert store.list_searches(db, pinned=False)[1] == 4
    assert store.list_searches(db, cache_hit=True)[1] == 1
    assert store.list_searches(db, text="TEA")[1] == 1
    assert store.list_searches(db, text="roasters")[1] == 1
    assert store.list_searches(db, text="boom")[1] == 1
    assert store.list_searches(db, text="%")[1] == 0  # LIKE wildcards are escaped
    assert store.list_searches(db, text="g_ogle")[1] == 0
    cut = store.to_iso(NOW - timedelta(minutes=35))
    assert store.list_searches(db, date_from=cut)[1] == 3
    assert store.list_searches(db, date_to=cut)[1] == 2
    assert store.list_searches(db, engine="google", status="ok", pinned=False)[1] == 3


def test_find_cached_only_returns_fresh_successful_originals(db):
    old = add(db, age_minutes=120)
    error = add(db, age_minutes=5, status="error", params_json="{}")
    hit = add(db, age_minutes=2, cache_hit=1)
    fresh_cut = store.to_iso(NOW - timedelta(minutes=60))
    assert store.find_cached(db, "hash-a", fresh_cut) is None
    original = add(db, age_minutes=30)
    found = store.find_cached(db, "hash-a", fresh_cut)
    assert found is not None and found["id"] == original
    assert found["id"] not in {old, error, hit}
    assert store.find_cached(db, "other-hash", fresh_cut) is None
    no_blob = store.new_id()
    store.insert_search(
        db,
        {
            "id": no_blob,
            "engine": "google",
            "params_hash": "hash-b",
            "status": "ok",
            "created_at": store.to_iso(NOW),
        },
    )
    assert store.find_cached(db, "hash-b", fresh_cut) is None


def test_count_live_since_distinguishes_live_calls_and_keys(db):
    add(db, age_minutes=10)
    add(db, age_minutes=10, cache_hit=1, api_key_id="k1")
    add(db, age_minutes=10, api_key_id="k1")
    add(db, age_minutes=90, api_key_id="k1")
    since = store.to_iso(NOW - timedelta(hours=1))
    assert store.count_live_since(db, since) == 2
    assert store.count_live_since(db, since, api_key_id="k1") == 2
    assert store.count_live_since(db, since, api_key_id="nope") == 0
    assert store.count_status(db, "ok") == 4


def test_delete_clears_references(db):
    search_id = add(db)
    store.insert_saved(
        db,
        {
            "id": "s1",
            "name": "n",
            "engine": "google",
            "params_json": "{}",
            "tags": "[]",
            "created_at": "t",
        },
    )
    store.update_saved(db, "s1", {"last_search_id": search_id})
    store.insert_monitor(
        db, {"id": "m1", "saved_id": "s1", "interval_minutes": 60, "next_run_at": "t"}
    )
    store.update_monitor(db, "m1", {"last_search_id": search_id})
    assert store.delete_search(db, search_id)
    assert store.get_saved(db, "s1")["last_search_id"] is None
    assert store.get_monitor(db, "m1")["last_search_id"] is None
    assert not store.delete_search(db, search_id)


def test_delete_where_keeps_pinned_unless_asked(db):
    add(db, engine="google", age_minutes=300)
    add(db, engine="google", age_minutes=300, pinned=1)
    add(db, engine="bing", age_minutes=300)
    add(db, engine="google", age_minutes=1)
    cutoff = store.to_iso(NOW - timedelta(minutes=60))
    assert store.delete_where(db, before=cutoff, engine="google") == (1, 1)
    assert store.delete_where(db, engine="bing") == (1, 0)
    assert store.delete_where(db, include_pinned=True) == (2, 0)
    assert store.list_searches(db)[1] == 0


def test_prune_by_age_and_count_protects_pins_and_baselines(db):
    stale = add(db, age_minutes=60 * 24 * 40)
    pinned = add(db, age_minutes=60 * 24 * 40, pinned=1)
    baseline = add(db, age_minutes=60 * 24 * 40)
    running = add(
        db, age_minutes=60, status="running"
    )  # an async search still in progress
    abandoned = add(
        db, age_minutes=60 * 24 * 3, status="running"
    )  # nobody collected it
    store.insert_saved(
        db,
        {
            "id": "s1",
            "name": "n",
            "engine": "google",
            "params_json": "{}",
            "tags": "[]",
            "created_at": "t",
        },
    )
    store.update_saved(db, "s1", {"last_search_id": baseline})
    recent = [add(db, age_minutes=index) for index in range(1, 6)]
    deleted = store.prune(db, retention_days=30, max_records=0, now=NOW)
    assert deleted == 1
    remaining = {row["id"] for row in store.list_searches(db, limit=50)[0]}
    assert stale not in remaining
    assert {pinned, baseline, running, abandoned, *recent} <= remaining
    # an async search that is more than two days old and still "running" is abandoned
    # (a flood of async calls must not pin rows forever); a fresh one is kept
    deleted = store.prune(db, retention_days=0, max_records=7, now=NOW)
    assert (
        deleted == 2
    )  # 9 rows - 7, oldest unprotected first: `abandoned`, then the oldest recent run
    remaining = {row["id"] for row in store.list_searches(db, limit=50)[0]}
    assert abandoned not in remaining
    assert {pinned, baseline, running} <= remaining
    assert recent[0] in remaining and recent[-1] not in remaining
    remaining = {row["id"] for row in store.list_searches(db, limit=50)[0]}
    assert {pinned, baseline, running} <= remaining
    assert recent[0] in remaining and recent[-1] not in remaining


def test_usage_upserts_accumulate(db):
    store.bump_usage(db, "2026-10-22", "google", "api", credits=1)
    store.bump_usage(db, "2026-10-22", "google", "api", cached=1, searches=1)
    store.bump_usage(db, "2026-10-22", "google", "playground", errors=1)
    store.bump_usage(db, "2026-10-01", "bing", "api")
    rows = {
        (row["day"], row["engine"], row["source"]): row
        for row in store.usage_since(db, "2026-10-10")
    }
    assert set(rows) == {
        ("2026-10-22", "google", "api"),
        ("2026-10-22", "google", "playground"),
    }
    api = rows[("2026-10-22", "google", "api")]
    assert (api["searches"], api["cached"], api["credits"]) == (2, 1, 1)
    assert rows[("2026-10-22", "google", "playground")]["errors"] == 1
    assert len(store.usage_since(db, "2026-01-01")) == 3


def test_latency_averages_only_live_successes(db):
    add(db, duration_ms=100)
    add(db, duration_ms=300)
    add(db, duration_ms=9000, status="error")
    add(db, duration_ms=0, cache_hit=1)
    assert store.latency_by_engine(db, store.to_iso(NOW - timedelta(days=1))) == {
        "google": 200.0
    }


def test_saved_and_monitor_crud_with_cascade(db):
    store.insert_saved(
        db,
        {
            "id": "s1",
            "name": "Alpha",
            "engine": "google",
            "params_json": "{}",
            "tags": "[]",
            "created_at": "2026-10-01",
        },
    )
    store.insert_saved(
        db,
        {
            "id": "s2",
            "name": "Beta",
            "engine": "bing",
            "params_json": "{}",
            "tags": "[]",
            "created_at": "2026-10-02",
            "pinned": 1,
        },
    )
    assert [row["id"] for row in store.list_saved(db)] == ["s2", "s1"]
    assert store.update_saved(db, "s1", {"name": "Alpha 2"})
    assert store.get_saved(db, "s1")["name"] == "Alpha 2"
    with pytest.raises(ValueError):
        store.update_saved(db, "s1", {"engine": "x"})
    store.insert_monitor(
        db,
        {
            "id": "m1",
            "saved_id": "s1",
            "interval_minutes": 30,
            "next_run_at": "2026-10-22T11:00:00Z",
        },
    )
    store.insert_monitor(
        db,
        {
            "id": "m2",
            "saved_id": "s2",
            "interval_minutes": 60,
            "next_run_at": "2026-10-22T13:00:00Z",
            "enabled": 0,
        },
    )
    monitor = store.get_monitor(db, "m1")
    assert monitor["saved_name"] == "Alpha 2" and monitor["engine"] == "google"
    assert [row["id"] for row in store.list_monitors(db)] == ["m1", "m2"]
    assert [row["id"] for row in store.due_monitors(db, "2026-10-22T12:00:00Z")] == [
        "m1"
    ]
    assert store.due_monitors(db, "2026-10-22T10:00:00Z") == []
    assert store.update_monitor(db, "m2", {"enabled": 1})
    with pytest.raises(ValueError):
        store.update_monitor(db, "m1", {"saved_id": "s2"})
    assert store.delete_saved(db, "s1")
    assert store.get_monitor(db, "m1") is None
    assert store.delete_monitor(db, "m2")
    assert not store.delete_monitor(db, "m2")
    assert not store.delete_saved(db, "s1")


def test_api_keys_store_only_hashes_and_revoke_once(db):
    store.insert_api_key(
        db,
        {
            "id": "k1",
            "name": "script",
            "prefix": "aira_ab12",
            "key_hash": "h" * 64,
            "created_at": "t",
            "hourly_limit": 5,
        },
    )
    listing = store.list_api_keys(db)
    assert [row["id"] for row in listing] == ["k1"]
    assert "key_hash" not in listing[0].keys()
    assert store.get_api_key(db, "k1")["key_hash"] == "h" * 64
    assert store.get_api_key_by_hash(db, "h" * 64)["id"] == "k1"
    assert store.get_api_key_by_hash(db, "x") is None
    store.touch_api_key(db, "k1", "2026-10-22T12:00:00Z")
    assert store.get_api_key(db, "k1")["last_used_at"] == "2026-10-22T12:00:00Z"
    assert store.revoke_api_key(db, "k1", "2026-10-22T12:01:00Z")
    assert not store.revoke_api_key(db, "k1", "2026-10-22T12:02:00Z")
    assert store.get_api_key(db, "k1")["revoked_at"] == "2026-10-22T12:01:00Z"
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_api_key(
            db,
            {
                "id": "k2",
                "name": "dup",
                "prefix": "aira_zzzz",
                "key_hash": "h" * 64,
                "created_at": "t",
                "hourly_limit": 0,
            },
        )


def test_key_index_lives_in_the_accounts_database():
    accounts = sqlite3.connect(":memory:")
    assert store.lookup_key_index(accounts, "abc") is None
    store.index_key(accounts, "abc", "user-1")
    store.index_key(accounts, "def", "user-1")
    store.index_key(accounts, "ghi", "user-2")
    assert store.lookup_key_index(accounts, "abc") == "user-1"
    store.index_key(accounts, "abc", "user-3")
    assert store.lookup_key_index(accounts, "abc") == "user-3"
    store.unindex_key(accounts, "abc")
    assert store.lookup_key_index(accounts, "abc") is None
    store.unindex_user(accounts, "user-1")
    assert store.lookup_key_index(accounts, "def") is None
    assert store.lookup_key_index(accounts, "ghi") == "user-2"


def test_cache_get_set_expire_and_delete(db):
    assert store.cache_get(db, "k", NOW) is None
    store.cache_set(db, "account:1", {"plan": "x"}, 60, NOW)
    store.cache_set(db, "account:2", [1, 2], 600, NOW)
    store.cache_set(db, "locations:1:a", [], 600, NOW)
    assert store.cache_get(db, "account:1", NOW + timedelta(seconds=59)) == {
        "plan": "x"
    }
    assert store.cache_get(db, "account:1", NOW + timedelta(seconds=61)) is None
    store.cache_set(db, "account:1", {"plan": "y"}, 60, NOW)
    assert store.cache_get(db, "account:1", NOW) == {"plan": "y"}
    store.cache_expire(db, NOW + timedelta(seconds=120))
    assert db.execute("SELECT COUNT(*) FROM airaapi_cache").fetchone()[0] == 2
    store.cache_delete(db, "account:")
    assert db.execute("SELECT key FROM airaapi_cache").fetchall() == [
        ("locations:1:a",)
    ]
    db.execute("UPDATE airaapi_cache SET value = '{broken'")
    assert store.cache_get(db, "locations:1:a", NOW) is None


def test_secret_store_encrypts_at_rest_and_round_trips(tmp_path):
    key = Fernet.generate_key()
    secrets_store = store.SecretStore(key)
    path = tmp_path / "secrets.sqlite3"
    connection = sqlite3.connect(path)
    store.ensure_schema(connection)
    plaintext = "super-secret-serpapi-key-0123456789"
    assert not secrets_store.exists(connection, "serpapi_api_key")
    secrets_store.put(connection, "serpapi_api_key", plaintext, NOW)
    connection.commit()
    assert secrets_store.exists(connection, "serpapi_api_key")
    assert secrets_store.get(connection, "serpapi_api_key") == plaintext
    ciphertext = connection.execute(
        "SELECT ciphertext FROM airaapi_secrets"
    ).fetchone()[0]
    assert plaintext.encode() not in ciphertext
    connection.close()
    assert plaintext.encode() not in path.read_bytes()
    connection = sqlite3.connect(path)
    secrets_store.put(connection, "serpapi_api_key", "replacement", NOW)
    assert secrets_store.get(connection, "serpapi_api_key") == "replacement"
    assert connection.execute("SELECT COUNT(*) FROM airaapi_secrets").fetchone()[0] == 1
    assert secrets_store.get(connection, "missing") is None
    assert secrets_store.delete(connection, "serpapi_api_key")
    assert not secrets_store.delete(connection, "serpapi_api_key")


def test_secret_store_detects_a_changed_key_and_invalid_keys(db):
    first = store.SecretStore(Fernet.generate_key())
    first.put(db, "name", "value", NOW)
    second = store.SecretStore(Fernet.generate_key())
    with pytest.raises(store.SecretError, match="cannot be decrypted"):
        second.get(db, "name")
    with pytest.raises(store.SecretError, match="not a valid Fernet key"):
        store.SecretStore(b"not-a-key")


# -- security regressions (adversarial review) ------------------------------------


def test_unpack_refuses_a_decompression_bomb_without_inflating_it():
    import zlib

    packer = zlib.compressobj(6)
    chunk = b"\0" * (1 << 20)
    bomb = b"".join(
        [
            packer.compress(chunk)
            for _ in range(store.MAX_INFLATED_BYTES // (1 << 20) + 8)
        ]
        + [packer.flush()]
    )
    assert len(bomb) < 300_000  # tiny on disk, huge when inflated
    with pytest.raises(store.BlobError):
        store.unpack(bomb)
    with pytest.raises(store.BlobError):
        store.unpack_text(bomb)


def test_unpack_reports_damaged_blobs_as_a_clean_error():
    import zlib

    for broken in (
        b"not zlib at all",
        zlib.compress(b"\xff\xfe not utf8"),
        zlib.compress(b"{broken"),
    ):
        with pytest.raises(store.BlobError):
            store.unpack(broken)
    assert store.unpack(store.pack({"a": [1, 2]})) == {"a": [1, 2]}
    assert store.unpack_text(store.pack_text("hello é")) == "hello é"
    assert store.unpack(None) is None and store.unpack_text(None) is None
    assert issubclass(store.BlobError, ValueError)


def test_unpack_survives_absurdly_nested_json():
    import zlib

    nested = zlib.compress(b"[" * 200_000 + b"]" * 200_000)
    with pytest.raises(store.BlobError):
        store.unpack(nested)


def test_prune_drops_cache_hit_copies_before_the_live_record(db):
    live = add(db, age_minutes=100)
    copies = [add(db, age_minutes=index, cache_hit=1) for index in range(1, 6)]
    assert store.prune(db, retention_days=0, max_records=3, now=NOW) == 3
    remaining = {row["id"] for row in store.list_searches(db, limit=50)[0]}
    assert live in remaining  # the record that keeps the cache warm survives
    assert copies[0] in remaining  # newest copies are kept over older ones


def test_cache_trim_keeps_the_newest_rows_of_one_prefix(db):
    for index in range(10):
        store.cache_set(db, f"locations:10:q{index}", [index], 100 + index, NOW)
    store.cache_set(db, "account:abc", {"plan": "x"}, 100, NOW)
    assert store.cache_trim(db, "locations:", 3) == 7
    keys = {row[0] for row in db.execute("SELECT key FROM airaapi_cache")}
    assert keys == {
        "account:abc",
        "locations:10:q7",
        "locations:10:q8",
        "locations:10:q9",
    }


def test_restrict_to_owner_drops_inherited_access(tmp_path):
    import os
    import subprocess

    target = tmp_path / "airaapi.key"
    target.write_bytes(b"secret")
    store.restrict_to_owner(target)  # must never raise, on any platform
    assert target.read_bytes() == b"secret"
    if os.name == "nt":
        listing = subprocess.run(
            ["icacls", str(target)], capture_output=True, text=True
        ).stdout
        for broad in ("Everyone", "Authenticated Users", "BUILTIN" + chr(92) + "Users"):
            assert broad not in listing
        assert "(I)" not in listing  # nothing is inherited any more
