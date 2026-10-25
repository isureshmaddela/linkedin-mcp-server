"""Compat helpers: API keys, query splitting, generated samples and route loading."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from datetime import date

import pytest

from aira.airaapi import catalog, compat
from aira.airaapi import http as airaapi_http
from aira.airaapi.client import SerpApiError

LANGUAGES = {"curl", "python", "javascript", "ruby", "php", "go", "java", "csharp"}


def test_generated_keys_are_random_hashed_and_well_formed():
    first, prefix, digest = compat.generate_key()
    second, _, other_digest = compat.generate_key()
    assert compat.KEY_PATTERN.match(first) and len(first) == 45
    assert first != second and digest != other_digest
    assert prefix == first[:9] and compat.hash_key(first) == digest
    assert len(digest) == 64


def test_extract_key_prefers_bearer_over_query():
    assert (
        compat.extract_key({"Authorization": "Bearer aira_abc"}, {"api_key": ["q"]})
        == "aira_abc"
    )
    assert compat.extract_key({"Authorization": "bearer  aira_abc "}, {}) == "aira_abc"
    assert compat.extract_key({}, {"api_key": [" aira_q "]}) == "aira_q"
    assert (
        compat.extract_key({"Authorization": "Basic abc"}, {"api_key": ["fallback"]})
        == "fallback"
    )
    assert compat.extract_key({"Authorization": "Bearer "}, {}) is None
    assert compat.extract_key({}, {"api_key": ["  "]}) is None
    assert compat.extract_key({}, {}) is None


def test_split_query_separates_engine_params_and_options():
    engine, params, options = compat.split_query(
        {
            "engine": ["google_jobs"],
            "q": ["barista"],
            "api_key": ["aira_secret"],
            "no_cache": ["true"],
            "output": ["json"],
            "json_restrictor": ["jobs_results"],
            "allow_unknown": ["1"],
            "location": ["Austin, Texas"],
        }
    )
    assert engine == "google_jobs"
    assert params == {"q": "barista", "location": "Austin, Texas"}
    assert options == {
        "no_cache": "true",
        "output": "json",
        "json_restrictor": "jobs_results",
        "allow_unknown": "1",
    }
    assert compat.split_query({})[0] == "google"
    assert compat.split_query({"engine": [" "]})[0] == "google"


def test_aira_metadata_does_not_mutate_the_stored_result():
    record = {
        "id": "abc",
        "cache_hit": True,
        "result": {
            "search_metadata": {"id": "s1", "status": "Success"},
            "organic_results": [],
        },
    }
    body = compat.with_aira_metadata(record)
    assert body["search_metadata"] == {
        "id": "s1",
        "status": "Success",
        "aira": {"id": "abc", "cache_hit": True},
    }
    assert "aira" not in record["result"]["search_metadata"]
    assert (
        compat.with_aira_metadata({"id": "x", "cache_hit": False, "result": None})[
            "search_metadata"
        ]["aira"]["id"]
        == "x"
    )


def test_v1_account_shape_has_no_key_and_adds_the_sdk_field():
    body = compat.v1_account(
        {"plan_name": "Dev", "hourly_rate_limit": 50, "fetched_at": "now"}
    )
    assert body["account_rate_limit_per_hour"] == 50 and "fetched_at" not in body
    assert "api_key" not in body


def test_sample_params_cover_required_and_alternative_groups():
    today = date(2026, 10, 22)
    assert compat.sample_params("google_jobs", today) == {"q": "coffee"}
    flights = compat.sample_params("google_flights", today)
    assert flights == {"departure_id": "AUS", "arrival_id": "JFK"}
    hotels = compat.sample_params("google_hotels", today)
    assert hotels["q"] == "coffee" and hotels["check_in_date"] == "2026-11-21"
    assert hotels["check_out_date"] == "2026-12-06"
    assert compat.sample_params(
        "google_sports", today
    )  # nothing mandatory: first parameter
    with pytest.raises(LookupError):
        compat.sample_params("nope")


def test_snippets_are_complete_and_hold_only_placeholders():
    samples = compat.snippets(
        "http://127.0.0.1:8765/v1/search.json",
        {"engine": "google", "q": 'say "hi" $x #{y}'},
        "YOUR_AIRA_API_KEY",
    )
    assert set(samples) == LANGUAGES
    for language, code in samples.items():
        assert "YOUR_AIRA_API_KEY" in code and "/v1/search.json" in code, language
        assert "google" in code, language
    ast.parse(samples["python"])
    assert "\\#{y}" in samples["ruby"]  # no string interpolation in the Ruby sample
    assert "'say \"hi\" $x #{y}'" in samples["php"]  # single-quoted: no interpolation
    assert "requests.get" in samples["python"] and "fetch(" in samples["javascript"]
    assert (
        "http.Get" in samples["go"]
        and "HttpClient" in samples["java"]
        and "HttpClient" in samples["csharp"]
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_sample_is_valid_syntax(tmp_path):
    code = compat.snippets(
        "http://x/v1/search.json", {"engine": "google", "q": "a b"}, "KEY"
    )["javascript"]
    target = tmp_path / "sample.mjs"
    target.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_every_engine_gets_aira_and_direct_examples():
    for found in catalog.engines().values():
        examples = compat.engine_examples(
            found.id, "http://127.0.0.1:8765", date(2026, 10, 22)
        )
        assert LANGUAGES <= set(examples), found.id
        assert (
            f"engine={found.id}" in examples["aira_v1"]
            and "YOUR_AIRA_API_KEY" in examples["aira_v1"]
        )
        assert "YOUR_SERPAPI_KEY" in examples["serpapi"]["curl"]
        assert examples["serpapi"]["url"].startswith("https://serpapi.com/search.json?")
        assert examples["params"], found.id


def test_docs_payload_is_generated_from_the_catalog():
    docs = compat.docs_payload("http://127.0.0.1:8765")
    assert docs["base_url"] == "http://127.0.0.1:8765/v1"
    assert {item["code"] for item in docs["errors"]} >= {
        "invalid_aira_key",
        "rate_limited",
        "not_configured",
    }
    assert docs["upstream_errors"] and docs["parameters"] and docs["categories"]
    assert set(docs["snippets"]) == LANGUAGES


def test_authenticate_key_error_codes():
    import sqlite3

    from aira.airaapi import store

    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    now = store.now_utc()
    for presented in (None, "", "aira_short", "x" * 45):
        with pytest.raises(SerpApiError) as caught:
            compat.authenticate_key(connection, presented, now)
        assert (
            caught.value.code == "invalid_aira_key"
            and caught.value.response_status == 401
        )

    class Index:
        def __init__(self):
            self.added, self.removed = [], []

        def add(self, key_hash):
            self.added.append(key_hash)

        def remove(self, key_hash):
            self.removed.append(key_hash)

    index = Index()
    created = compat.create_key(connection, index, "ci", 5, now)
    secret = created["secret"]
    row = compat.authenticate_key(connection, secret, now)
    assert row["id"] == created["key"]["id"] and index.added == [
        compat.hash_key(secret)
    ]
    assert store.get_api_key(connection, row["id"])["last_used_at"] == store.to_iso(now)
    revoked = compat.revoke_key(connection, index, row["id"], now)
    assert revoked["revoked_at"] and index.removed == [compat.hash_key(secret)]
    with pytest.raises(SerpApiError, match="revoked"):
        compat.authenticate_key(connection, secret, now)
    with pytest.raises(LookupError):
        compat.revoke_key(connection, index, "missing", now)


def test_key_limits_and_hourly_enforcement():
    import sqlite3

    from aira.airaapi import store

    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    now = store.now_utc()

    class Index:
        def add(self, key_hash):
            pass

        def remove(self, key_hash):
            pass

    for index in range(compat.MAX_KEYS):
        compat.create_key(connection, Index(), f"k{index}", 0, now)
    with pytest.raises(ValueError, match="up to 25 active keys"):
        compat.create_key(connection, Index(), "one too many", 0, now)
    keys = compat.list_keys(connection, now)
    assert len(keys) == compat.MAX_KEYS and keys[0]["used_this_hour"] == 0
    compat.enforce_hourly_limit(
        connection, {"id": keys[0]["id"], "hourly_limit": 0}, now
    )
    store.insert_search(
        connection,
        {
            "id": "s1",
            "engine": "google",
            "created_at": store.to_iso(now),
            "api_key_id": keys[0]["id"],
        },
    )
    with pytest.raises(SerpApiError) as caught:
        compat.enforce_hourly_limit(
            connection, {"id": keys[0]["id"], "hourly_limit": 1}, now
        )
    assert caught.value.code == "rate_limited" and caught.value.response_status == 429


# -- route registration -----------------------------------------------------------------


def test_route_modules_load_and_optional_ones_may_be_absent(monkeypatch):
    airaapi_http.load_route_modules()
    names = {entry.attr for entry in airaapi_http.AIRA_ROUTES}
    assert "airaapi__routes__search" in names and "airaapi__routes__v1_search" in names
    monkeypatch.setattr(
        airaapi_http,
        "ROUTE_MODULES",
        ("aira.airaapi.routes", "aira.airaapi.feature_not_built_yet"),
    )
    airaapi_http.load_route_modules()
    monkeypatch.setattr(
        airaapi_http, "ROUTE_MODULES", ("aira.airaapi.feature_not_built_yet",)
    )
    with pytest.raises(ModuleNotFoundError):
        airaapi_http.load_route_modules()


def test_a_broken_optional_module_is_not_hidden(monkeypatch, tmp_path):
    (tmp_path / "airaapi_broken_feature.py").write_text(
        "import a_dependency_that_does_not_exist\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        airaapi_http, "ROUTE_MODULES", ("aira.airaapi.routes", "airaapi_broken_feature")
    )
    with pytest.raises(ModuleNotFoundError, match="a_dependency_that_does_not_exist"):
        airaapi_http.load_route_modules()
    sys.modules.pop("airaapi_broken_feature", None)


def test_registering_a_handler_name_twice_is_rejected():
    def duplicate(request):
        return 200, {}

    duplicate.__module__ = "aira.airaapi.routes"
    duplicate.__name__ = "search"
    with pytest.raises(RuntimeError, match="Duplicate Aira route handler"):
        airaapi_http.aira_route("GET", r"/x")(duplicate)


def test_request_query_helpers():
    import re

    request = airaapi_http.Request(
        match=re.match("", ""),
        payload={},
        query={
            "n": ["5"],
            "bad": ["x"],
            "big": ["500"],
            "flag": ["yes"],
            "off": ["0"],
            "weird": ["maybe"],
            "text": ["  hi "],
        },
        user=None,
        headers={"Host": "127.0.0.1:8765"},
        client="local",
        connect=lambda: None,  # type: ignore[arg-type, return-value]
        setting=lambda _name: "",
        secret_store=None,  # type: ignore[arg-type]
        key_index=None,  # type: ignore[arg-type]
    )
    assert request.q("text") == "hi" and request.q("missing", "dflt") == "dflt"
    assert request.qint("n", 1, 0, 10) == 5 and request.qint("absent", 7, 0, 10) == 7
    for name in ("bad", "big"):
        with pytest.raises(ValueError):
            request.qint(name, 1, 0, 10)
    assert (
        request.qbool("flag") is True
        and request.qbool("off") is False
        and request.qbool("absent") is None
    )
    with pytest.raises(ValueError):
        request.qbool("weird")
    assert request.base_url() == "http://127.0.0.1:8765"
    request.headers = {"Host": "evil host/with spaces"}
    assert request.base_url() == "http://127.0.0.1:8765"
    assert request.header("Missing", "fallback") == "fallback"


# -- security regressions (adversarial review) ------------------------------------


def test_key_pattern_rejects_a_trailing_newline():
    good = "aira_" + "A" * 40
    assert compat.KEY_PATTERN.match(good) and compat.KEY_PATTERN.fullmatch(good)
    assert not compat.KEY_PATTERN.match(good + "\n")
    assert not compat.KEY_PATTERN.match(good + "x")


def test_curl_sample_is_single_quoted_so_a_value_never_runs_in_the_shell():
    samples = compat.snippets(
        "http://127.0.0.1:8765/v1/search.json",
        {"q": "$(touch /tmp/pwned) `id` 'quoted' !x"},
        "YOUR_AIRA_API_KEY",
    )
    curl = samples["curl"]
    assert "--data-urlencode 'q=$(touch /tmp/pwned) `id` '\\''quoted'\\'' !x'" in curl
    assert '"q=' not in curl  # no double-quoted argument where $() would expand


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_curl_sample_round_trips_through_a_real_shell():
    value = "a $(echo x) `echo y` 'z' !w"
    samples = compat.snippets("http://h/v1", {"q": value}, "K")
    script = (
        samples["curl"]
        .replace("curl -G", "printf '%s\\n' --")
        .replace("--data-urlencode ", "")
    )
    out = subprocess.run(
        ["sh", "-c", script.replace(" \\\n", " ")], capture_output=True, text=True
    ).stdout
    assert f"q={value}" in out


def test_scrub_request_line_hides_keys_however_the_query_spells_them():
    secret = "aira_" + "Zx9-_" * 8
    spellings = [
        f'"GET /v1/search.json?q=a&api_key={secret} HTTP/1.1" 200 -',
        f'"GET /search.json?api%5Fkey={secret} HTTP/1.1" 200 -',
        f'"GET /search.json?%61pi_key={secret} HTTP/1.1" 200 -',
        f'"GET /search.json?api_key={secret.replace("_", "%5F")} HTTP/1.1" 200 -',
        f'"GET /search.json?x=1;api_key={secret} HTTP/1.1" 200 -',
        f'"GET /search.json?token={secret} HTTP/1.1" 200 -',
        f"Authorization: Bearer {secret}",
    ]
    for line in spellings:
        cleaned = airaapi_http.scrub_request_line(line)
        assert secret not in cleaned and "Zx9-_Zx9-_" not in cleaned, cleaned
    assert "q=coffee" in airaapi_http.scrub_request_line('"GET /x?q=coffee HTTP/1.1"')


def test_saved_pages_are_served_sandboxed():
    page = airaapi_http.Raw(200, b"<script>1</script>", "text/html; charset=utf-8")
    headers = dict(airaapi_http.extra_headers(page))
    assert headers["Content-Security-Policy"].startswith("sandbox")
    assert "form-action 'none'" in headers["Content-Security-Policy"]
    svg = airaapi_http.Raw(200, b"<svg/>", "image/svg+xml")
    assert "Content-Security-Policy" in dict(airaapi_http.extra_headers(svg))
    plain = airaapi_http.Raw(200, b"{}", "application/json", headers=(("X-A", "1"),))
    assert airaapi_http.extra_headers(plain) == (("X-A", "1"),)


def test_hourly_slot_counts_calls_still_in_flight():
    import sqlite3
    from datetime import datetime, timezone

    from aira.airaapi import store

    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    key = {"id": "k1", "hourly_limit": 2, "name": "n"}
    first = compat.hourly_slot(connection, key, now)
    second = compat.hourly_slot(connection, key, now)
    first.__enter__()
    second.__enter__()
    with pytest.raises(
        SerpApiError
    ) as caught:  # the third arrives while two are running
        with compat.hourly_slot(connection, key, now):
            pass
    assert caught.value.code == "rate_limited"
    second.__exit__(None, None, None)
    with compat.hourly_slot(connection, key, now):  # a slot freed up
        pass
    first.__exit__(None, None, None)
    assert compat._key_inflight == {}
    with compat.hourly_slot(connection, {"id": "k2", "hourly_limit": 0}, now):
        pass  # no limit, no accounting


def test_host_is_local_accepts_loopback_names_only(monkeypatch):
    monkeypatch.delenv(airaapi_http.ALLOWED_HOSTS_ENV, raising=False)
    for good in (
        "127.0.0.1",
        "127.0.0.1:8765",
        "127.1.2.3:80",
        "localhost",
        "LOCALHOST:8765",
        "aira.localhost:9",
        "[::1]:8765",
        "",
    ):
        assert airaapi_http.host_is_local(good), good
    for bad in (
        "evil.example",
        "evil.example:8765",
        "127.0.0.1.evil.example",
        "127.0.0.1@evil.example",
        "evil.example/127.0.0.1",
        "10.0.0.5:8765",
        "192.168.1.9",
        "0.0.0.0",
        "[::2]",
        "localhost.evil.example",
        "127.0.0.1:99999999",
        "a b",
    ):
        assert not airaapi_http.host_is_local(bad), bad
    monkeypatch.setenv(airaapi_http.ALLOWED_HOSTS_ENV, "aira.lan, Tunnel.Example")
    assert airaapi_http.host_is_local("aira.lan:8765")
    assert airaapi_http.host_is_local("tunnel.example")
    assert not airaapi_http.host_is_local("other.example")
