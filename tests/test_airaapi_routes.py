"""HTTP journeys for the API console and the /v1 endpoint.

Each test starts the real request handler on a loopback port with scratch
SQLite databases and points Aira at the stand-in SerpApi server
(``tests/serpapi_stub.py``) through ``AIRA_SERPAPI_BASE_URL``. Nothing here
touches the network, the user's real database or a real SerpApi account.
"""

from __future__ import annotations

import csv
import hashlib
import http.cookiejar
import io
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import auto_apply_assistant as app
from aira.airaapi import client as client_module
from serpapi_stub import DEFAULT_KEY, SerpApiStub, digest_of

KEY = DEFAULT_KEY
KEY2 = "second_valid_key_0123456789abcd"
BAD_KEY = "rejected_key_0123456789abcdefgh"
PASSWORD = "a long enough password 123"
KEY_PATTERN = re.compile(r"^aira_[A-Za-z0-9_-]{40}$")


class Api:
    """A browser-like client (cookie jar, CSRF headers) that keeps a transcript."""

    def __init__(self, base: str, transcript: list[bytes]) -> None:
        self.base = base
        self.transcript = transcript
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, path, body=None, *, headers=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Requested-With", "aira")
        request.add_header("Origin", self.base)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self.opener.open(request, timeout=60) as response:
                status, payload, out = (
                    response.status,
                    response.read(),
                    response.headers,
                )
        except urllib.error.HTTPError as error:
            status, payload, out = error.code, error.read(), error.headers
        self.transcript.append(payload)
        if raw:
            return status, payload, out
        return status, (json.loads(payload.decode("utf-8")) if payload else {}), out

    def get(self, path, **kwargs):
        return self.call("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.call("POST", path, {} if body is None else body, **kwargs)

    def ok(self, method, path, body=None, expect=200):
        status, payload, _ = self.call(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload


class Env:
    def __init__(
        self, tmp_path: Path, stub: SerpApiStub, base: str, transcript
    ) -> None:
        self.tmp_path = tmp_path
        self.stub = stub
        self.base = base
        self.transcript = transcript

    def client(self) -> Api:
        return Api(self.base, self.transcript)

    def v1(self, path, *, key=None, bearer=None, headers=None):
        """A stateless GET like a script would make (no cookies, no CSRF headers)."""
        url = self.base + path
        if key:
            url += ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(key)
        request = urllib.request.Request(url)
        if bearer:
            request.add_header("Authorization", f"Bearer {bearer}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, payload, out = (
                    response.status,
                    response.read(),
                    response.headers,
                )
        except urllib.error.HTTPError as error:
            status, payload, out = error.code, error.read(), error.headers
        self.transcript.append(payload)
        kind = out.get("Content-Type", "")
        body = (
            json.loads(payload)
            if "json" in kind and payload
            else payload.decode("utf-8", "replace")
        )
        return status, body, out

    def database_bytes(self) -> bytes:
        blob = b""
        for path in self.tmp_path.rglob("*"):
            if path.is_file() and path.suffix in {
                ".sqlite3",
                ".sqlite3-wal",
                ".sqlite3-shm",
            }:
                blob += path.read_bytes()
        return blob

    def user_db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.tmp_path / "applications.sqlite3")

    def accounts_db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.tmp_path / "accounts.sqlite3")


@pytest.fixture
def env(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    stub = SerpApiStub(valid_keys=(KEY, KEY2)).start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", stub.base_url)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("AIRA_SECRET_KEY", raising=False)
    monkeypatch.setattr(client_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(app, "_airaapi_key_cache", {})
    from collections import OrderedDict

    from aira.airaapi import routes as airaapi_routes

    monkeypatch.setattr(airaapi_routes, "_keyless_cache", OrderedDict())
    monkeypatch.setattr(airaapi_routes, "_keyless_hits", {})
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=50, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    monkeypatch.setattr(
        app,
        "_airaapi_key_limiter",
        app.RateLimiter(limit=8, window_seconds=300, lockout_seconds=300),
    )
    monkeypatch.setattr(app, "_ollama_available", lambda: False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    thread = threading.Thread(
        target=lambda: httpd.serve_forever(poll_interval=0.05), daemon=True
    )
    thread.start()
    transcript: list[bytes] = []
    try:
        yield Env(
            tmp_path, stub, f"http://127.0.0.1:{httpd.server_address[1]}", transcript
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        stub.stop()


def register(api: Api, email="user@example.com") -> None:
    status, body, _ = api.post(
        "/api/auth/register", {"email": email, "password": PASSWORD}
    )
    assert status == 201, body


def connect_key(api: Api, key=KEY) -> dict:
    return api.ok("POST", "/api/airaapi/key", {"api_key": key})


@pytest.fixture
def api(env):
    client = env.client()
    register(client)
    return client


@pytest.fixture
def connected(env, api):
    connect_key(api)
    return api


def assert_secret_absent(
    env: Env, caplog, *secrets: str, responses_allowed: int = 0
) -> None:
    """The secrets must be in no database file, no log record and (at most N) responses."""
    for secret in secrets:
        needle = secret.encode()
        seen = sum(needle in body for body in env.transcript)
        assert seen <= responses_allowed, f"secret in {seen} responses"
        assert needle not in env.database_bytes(), "secret in a database file"
        logs = "\n".join(
            [record.getMessage() for record in caplog.records]
            + [str(record.exc_text or "") for record in caplog.records]
        )
        assert secret not in logs, "secret in a log record"


# -- access control -------------------------------------------------------------


def test_console_requires_a_session_and_v1_requires_an_aira_key(env):
    anonymous = env.client()
    for method, path in (
        ("GET", "/api/airaapi/overview"),
        ("GET", "/api/airaapi/engines"),
        ("POST", "/api/airaapi/search"),
        ("GET", "/api/airaapi/keys"),
        ("DELETE", "/api/airaapi/key"),
    ):
        status, body, headers = anonymous.call(
            method, path, {} if method == "POST" else None
        )
        assert status == 401 and body == {"error": "Sign in to continue."}, path
        assert headers.get("X-Request-ID")
    for path in (
        "/v1/search.json?engine=google&q=x",
        "/v1/account.json",
        "/v1/engines.json",
    ):
        status, body, _ = env.v1(path)
        assert status == 401 and body["error"].startswith("Invalid API key.")
        assert body["code"] == "invalid_aira_key"
    assert env.stub.search_calls == 0


def test_session_cookie_does_not_open_v1_and_aira_keys_do_not_open_the_console(
    env, api
):
    status, _, _ = api.get("/v1/engines.json")
    assert status == 401  # cookies are ignored by /v1
    created = api.ok("POST", "/api/airaapi/keys", {"name": "script"}, expect=201)
    status, body, _ = env.v1("/api/airaapi/overview", key=created["secret"])
    assert status == 401 and body == {"error": "Sign in to continue."}
    status, body, _ = env.v1("/api/airaapi/keys", bearer=created["secret"])
    assert status == 401


def test_cross_origin_writes_are_refused(env, api):
    status, body, _ = api.post(
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "x"}},
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403 and "Cross-origin" in body["error"]
    status, _, _ = api.call(
        "POST",
        "/api/airaapi/saved",
        {"name": "x"},
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403
    assert env.stub.search_calls == 0


# -- unconfigured console --------------------------------------------------------


def test_console_works_without_a_key_and_says_what_is_missing(env, api):
    overview = api.ok("GET", "/api/airaapi/overview")
    assert overview["configured"] is False and overview["key"] == {
        "source": None,
        "last4": None,
    }
    assert overview["account"] is None and overview["account_error"] is None
    assert overview["engines"] == 137 and overview["saved"] == 0
    assert (
        overview["usage"]["last_30_days"] == 0
        and len(overview["usage"]["by_day"]) == 30
    )
    assert overview["monitors"] == {"active": 0, "due_next": None}
    listing = api.ok("GET", "/api/airaapi/engines")
    assert listing["total"] == 137 and listing["categories"] and listing["families"]
    jobs = api.ok("GET", "/api/airaapi/engines?category=jobs")
    assert {item["id"] for item in jobs["engines"]} == {
        "aira_jobs",
        "google_jobs",
        "google_jobs_listing",
    }
    assert api.ok("GET", "/api/airaapi/engines?q=flights")["total"] >= 2
    detail = api.ok("GET", "/api/airaapi/engines/google_jobs")
    assert detail["id"] == "google_jobs" and detail["params"][0]["name"] == "q"
    assert detail["common_parameters"]
    examples = detail["examples"]
    assert set(examples) >= {
        "curl",
        "python",
        "javascript",
        "ruby",
        "php",
        "go",
        "java",
        "csharp",
        "aira_v1",
        "serpapi",
    }
    assert (
        "YOUR_AIRA_API_KEY" in examples["curl"]
        and "/v1/search.json" in examples["curl"]
    )
    assert examples["aira_v1"].startswith(
        env.base + "/v1/search.json?engine=google_jobs&q="
    )
    assert (
        "YOUR_SERPAPI_KEY" in examples["serpapi"]["python"]
        and "serpapi.com" in examples["serpapi"]["url"]
    )
    status, body, _ = api.get("/api/airaapi/engines/google_job")
    assert status == 404 and "Did you mean google_jobs" in body["error"]
    status, body, _ = api.post(
        "/api/airaapi/search", {"engine": "google", "params": {"q": "coffee"}}
    )
    assert (
        status == 409
        and body["code"] == "not_configured"
        and "SERPAPI_API_KEY" in body["error"]
    )
    status, body, _ = api.get("/api/airaapi/account")
    assert status == 409 and body["code"] == "not_configured"
    assert env.stub.search_calls == 0
    api.ok("DELETE", "/api/airaapi/key", expect=404)


def test_locations_need_no_key_and_are_cached(env, api):
    first = api.ok("GET", "/api/airaapi/locations?q=austin&limit=3")
    assert first["locations"][0]["canonical_name"] == "Austin, TX,Texas,United States"
    assert first["locations"][0]["gps"] == [-97.7430608, 30.267153]
    api.ok("GET", "/api/airaapi/locations?q=austin&limit=3")
    assert env.stub.count("/locations.json") == 1
    status, body, _ = api.get("/api/airaapi/locations?q=a")
    assert status == 400 and "at least 2" in body["error"]
    status, body, _ = api.get("/api/airaapi/locations?q=austin&limit=99")
    assert status == 400 and "between 1 and 10" in body["error"]
    assert api.ok("GET", "/api/airaapi/locations?q=zzzz-nowhere")["locations"] == []


def test_docs_describe_v1_with_generated_samples(env, api):
    docs = api.ok("GET", "/api/airaapi/docs")
    assert docs["base_url"] == env.base + "/v1"
    assert {item["path"].split("?")[0] for item in docs["endpoints"]} >= {
        "/search.json",
        "/account.json",
        "/locations.json",
        "/v1/searches.json",
        "/v1/engines.json",
    }
    assert set(docs["snippets"]) == {
        "curl",
        "python",
        "javascript",
        "ruby",
        "php",
        "go",
        "java",
        "csharp",
    }
    assert all("YOUR_AIRA_API_KEY" in code for code in docs["snippets"].values())
    assert {item["http_status"] for item in docs["errors"]} >= {401, 400, 429, 409, 402}
    assert docs["parameters"] and docs["migrate"] and docs["upstream_errors"]
    assert (
        docs["root_url"] == env.base
        and "BASE_DOMAIN" in docs["sdk"]["python (serpapi)"]
    )


def test_platform_reference_serves_plans_billing_rules_and_sdks(env, api):
    platform = api.ok("GET", "/api/airaapi/platform")
    assert {"plans", "billing_rules", "sdks", "dashboard_pages"} <= set(platform)
    assert any(plan["name"] == "Free" for plan in platform["plans"])
    assert {page["id"] for page in platform["dashboard_pages"]} >= {
        "dashboard",
        "playground",
        "my-searches",
        "locations-tool",
    }
    # Research sources and anything else outside the reference sections stay on the server.
    assert "sources" not in platform and "_meta" not in platform


# -- connecting the SerpApi key -----------------------------------------------------


def test_connecting_a_key_validates_stores_encrypted_and_never_echoes(env, api, caplog):
    status, body, _ = api.post("/api/airaapi/key", {"api_key": "short"})
    assert status == 400 and "does not look like a SerpApi key" in body["error"]
    status, body, _ = api.post("/api/airaapi/key", {"api_key": 12345})
    assert status == 400
    # A key SerpApi rejects: not 401, so the browser app does not sign the user out.
    status, body, _ = api.post("/api/airaapi/key", {"api_key": BAD_KEY})
    assert status == 422 and body["code"] == "invalid_api_key"
    assert BAD_KEY not in json.dumps(body)
    assert api.ok("GET", "/api/airaapi/overview")["configured"] is False
    connected = connect_key(api)
    assert connected["configured"] is True and connected["source"] == "app"
    assert connected["last4"] == KEY[-4:]
    assert connected["account"]["plan_name"] == "Developer Plan"
    assert "api_key" not in connected["account"] and "api_key" not in json.dumps(
        connected
    )
    overview = api.ok("GET", "/api/airaapi/overview")
    assert overview["configured"] and overview["key"] == {
        "source": "app",
        "last4": KEY[-4:],
    }
    assert (
        overview["account"]["total_searches_left"] > 0
        and overview["account_error"] is None
    )
    account = api.ok("GET", "/api/airaapi/account")
    assert account["fetched_at"] and "api_key" not in account
    api.ok("GET", "/api/airaapi/account?refresh=1")
    # rejected key, validation, overview, forced refresh; the plain GET was cached
    assert env.stub.count("/account.json") == 4
    assert_secret_absent(env, caplog, KEY, BAD_KEY)
    with env.user_db() as connection:
        row = connection.execute(
            "SELECT name, ciphertext FROM airaapi_secrets"
        ).fetchone()
    assert row[0] == "serpapi_api_key" and KEY.encode() not in row[1]


def test_a_rejected_replacement_leaves_the_old_key_untouched(env, connected, caplog):
    before = connected.ok("GET", "/api/airaapi/overview")["key"]
    status, body, _ = connected.post("/api/airaapi/key", {"api_key": BAD_KEY})
    assert status == 422 and body["code"] == "invalid_api_key"
    assert connected.ok("GET", "/api/airaapi/overview")["key"] == before
    record = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "still works"}},
    )
    assert record["status"] == "ok"
    assert env.stub.calls[-1].key_digest == digest_of(KEY)
    replaced = connect_key(connected, KEY2)
    assert replaced["last4"] == KEY2[-4:]
    connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "after replace"}},
    )
    assert env.stub.calls[-1].key_digest == digest_of(KEY2)
    assert_secret_absent(env, caplog, KEY, KEY2, BAD_KEY)


def test_removing_the_key_stops_live_searches_but_not_cached_ones(env, connected):
    connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "keep"}}
    )
    removed = connected.ok("DELETE", "/api/airaapi/key")
    assert removed == {"configured": False, "source": None, "last4": None}
    cached = connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "keep"}}
    )
    assert cached["cache_hit"] is True
    status, body, _ = connected.post(
        "/api/airaapi/search", {"engine": "google", "params": {"q": "fresh"}}
    )
    assert status == 409 and body["code"] == "not_configured"
    assert env.stub.search_calls == 1


def test_environment_key_and_account_key_precedence(env, api, monkeypatch, caplog):
    monkeypatch.setenv("SERPAPI_API_KEY", KEY)
    overview = api.ok("GET", "/api/airaapi/overview")
    assert (
        overview["key"] == {"source": "env", "last4": KEY[-4:]}
        and overview["configured"]
    )
    assert overview["account"]["plan_name"] == "Developer Plan"
    api.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "from env"}}
    )
    assert env.stub.calls[-1].key_digest == digest_of(KEY)
    status, body, _ = api.get("/api/airaapi/status")
    assert status == 200
    # An env key cannot be removed from the app.
    status, body, _ = api.call("DELETE", "/api/airaapi/key")
    assert status == 409 and body["code"] == "conflict"
    connect_key(api, KEY2)
    assert api.ok("GET", "/api/airaapi/overview")["key"]["source"] == "app"
    api.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "from app"}}
    )
    assert env.stub.calls[-1].key_digest == digest_of(KEY2)
    after = api.ok("DELETE", "/api/airaapi/key")
    assert after["source"] == "env" and after["last4"] == KEY[-4:]
    status, body, _ = api.call("DELETE", "/api/airaapi/key")
    assert (
        status == 409
        and body["code"] == "conflict"
        and "SERPAPI_API_KEY" in body["error"]
    )
    api.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "env again"}},
    )
    assert env.stub.calls[-1].key_digest == digest_of(KEY)
    assert_secret_absent(env, caplog, KEY, KEY2)


def test_other_features_use_the_connected_key(env, api, monkeypatch):
    sources = api.ok("GET", "/api/sources")["sources"]
    google = next(item for item in sources if item["source"] == "google_jobs")
    assert google["connected"] is False and "API" in google["connection_hint"]
    connect_key(api)
    sources = api.ok("GET", "/api/sources")["sources"]
    google = next(item for item in sources if item["source"] == "google_jobs")
    assert google["connected"] is True
    assert api.ok("GET", "/api/status")["google_jobs"] is True


# -- searching and history -----------------------------------------------------------


def test_search_cache_history_and_filters(env, connected, caplog):
    first = connected.ok(
        "POST",
        "/api/airaapi/search",
        {
            "engine": "google_jobs",
            "params": {"q": "barista", "location": "New York"},
            "label": "NYC baristas",
        },
    )
    assert (
        first["status"] == "ok"
        and first["credits"] == 1
        and first["cache_hit"] is False
    )
    assert first["result_count"] == 4 and first["main_result_key"] == "jobs_results"
    assert first["result"]["jobs_results"][0]["company_name"] == "Blue Bottle Coffee"
    assert first["result"]["search_metadata"]["fixture"] is True
    assert (
        first["params"] == {"q": "barista", "location": "New York"}
        and first["label"] == "NYC baristas"
    )
    assert env.stub.search_calls == 1
    again = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google_jobs", "params": {"location": "New York", "q": " barista "}},
    )
    assert (
        again["cache_hit"] is True
        and again["credits"] == 0
        and again["id"] != first["id"]
    )
    assert again["serpapi_id"] == first["serpapi_id"] and env.stub.search_calls == 1
    fresh = connected.ok(
        "POST",
        "/api/airaapi/search",
        {
            "engine": "google_jobs",
            "params": {"q": "barista", "location": "New York"},
            "options": {"no_cache": True},
        },
    )
    assert fresh["cache_hit"] is False and env.stub.search_calls == 2
    assert env.stub.calls[-1].params["no_cache"] == "true"
    connected.ok(
        "POST", "/api/airaapi/search", {"engine": "bing", "params": {"q": "tea"}}
    )
    history = connected.ok("GET", "/api/airaapi/searches")
    assert history["total"] == 4 and history["limit"] == 25 and history["offset"] == 0
    assert [item["engine"] for item in history["items"]] == [
        "bing",
        "google_jobs",
        "google_jobs",
        "google_jobs",
    ]
    assert all("result" not in item for item in history["items"])
    assert history["items"][0]["engine_name"]
    assert connected.ok("GET", "/api/airaapi/searches?engine=google_jobs")["total"] == 3
    assert connected.ok("GET", "/api/airaapi/searches?cache=1")["total"] == 1
    assert connected.ok("GET", "/api/airaapi/searches?cache=0&q=barista")["total"] == 2
    assert connected.ok("GET", "/api/airaapi/searches?status=error")["total"] == 0
    assert (
        connected.ok("GET", "/api/airaapi/searches?source=playground&limit=2&offset=3")[
            "items"
        ][0]["engine"]
        == "google_jobs"
    )
    assert (
        connected.ok("GET", "/api/airaapi/searches?from=2020-01-01&to=2099-12-31")[
            "total"
        ]
        == 4
    )
    assert connected.ok("GET", "/api/airaapi/searches?to=2020-01-01")["total"] == 0
    for query, message in (
        ("from=yesterday", "Dates must"),
        ("limit=0", "between 1 and 200"),
        ("limit=abc", "whole number"),
        ("pinned=maybe", "true or false"),
    ):
        status, body, _ = connected.get("/api/airaapi/searches?" + query)
        assert status == 400 and message in body["error"], query
    detail = connected.ok("GET", f"/api/airaapi/searches/{first['id']}")
    assert detail["result"] == first["result"] and detail["id"] == first["id"]
    updated = connected.ok(
        "PATCH",
        f"/api/airaapi/searches/{first['id']}",
        {"pinned": True, "label": "pinned run"},
    )
    assert updated["pinned"] is True and updated["label"] == "pinned run"
    assert connected.ok("GET", "/api/airaapi/searches?pinned=1")["total"] == 1
    status, body, _ = connected.call(
        "PATCH", f"/api/airaapi/searches/{first['id']}", {}
    )
    assert status == 400
    overview = connected.ok("GET", "/api/airaapi/overview")
    assert (
        overview["usage"]["today"] == 4 and overview["usage"]["credits_used_30d"] == 3
    )
    assert overview["usage"]["cache_hit_rate"] == pytest.approx(0.25)
    assert overview["usage"]["by_engine"][0]["engine"] == "google_jobs"
    assert len(overview["recent"]) == 4
    assert_secret_absent(env, caplog, KEY)


def test_search_validation_errors_are_plain_and_cost_nothing(env, connected):
    for body, message in (
        ({"engine": "google", "params": {}}, "'q' is required"),
        (
            {"engine": "google", "params": {"q": "x", "nonsense": 1}},
            "Unknown parameter 'nonsense'",
        ),
        (
            {"engine": "google", "params": {"q": "x", "api_key": "leak"}},
            "not an engine parameter",
        ),
        (
            {"engine": "google", "params": {"q": "x", "start": "ten"}},
            "must be a whole number",
        ),
        (
            {"engine": "google", "params": {"q": "x"}, "options": {"bogus": 1}},
            "Unknown option",
        ),
        (
            {
                "engine": "google",
                "params": {"q": "x"},
                "options": {"async": True, "no_cache": True},
            },
            "async together",
        ),
        ({"engine": "google", "params": "q=x"}, "params must be an object"),
        ({"params": {"q": "x"}}, "Choose an engine"),
        ({"engine": "google_flights", "params": {}}, "Provide at least one of"),
    ):
        status, payload, _ = connected.post("/api/airaapi/search", body)
        assert status == 400 and message in payload["error"], (body, payload)
        assert "leak" not in payload["error"]
    status, payload, _ = connected.post(
        "/api/airaapi/search", {"engine": "nope", "params": {"q": "x"}}
    )
    assert status == 404
    assert env.stub.search_calls == 0
    assert connected.ok("GET", "/api/airaapi/searches")["total"] == 0


def test_upstream_errors_are_recorded_and_never_end_the_session(env, connected):
    expectations = {
        "__invalid_key__": (422, "invalid_api_key"),
        "__out_of_searches__": (402, "out_of_searches"),
        "__rate_limit__": (429, "rate_limited"),
        "__server_error__": (502, "upstream_error"),
        "__search_failed__": (502, "search_failed"),
    }
    record_ids = []
    for marker, (status_code, code) in expectations.items():
        status, body, _ = connected.post(
            "/api/airaapi/search", {"engine": "google", "params": {"q": marker}}
        )
        assert status == status_code and body["code"] == code, (marker, status, body)
        assert body["record_id"]
        record_ids.append(body["record_id"])
        assert connected.ok("GET", "/api/airaapi/overview")[
            "configured"
        ]  # session still valid
    history = connected.ok("GET", "/api/airaapi/searches?status=error")
    assert history["total"] == 5
    assert {item["id"] for item in history["items"]} == set(record_ids)
    assert all(item["credits"] == 0 and item["error"] for item in history["items"])
    usage = connected.ok("GET", "/api/airaapi/overview")["usage"]
    assert usage["error_rate"] == 1 and usage["credits_used_30d"] == 0
    # An error is never served as a cached answer.
    status, _, _ = connected.post(
        "/api/airaapi/search", {"engine": "google", "params": {"q": "__rate_limit__"}}
    )
    assert status == 429 and env.stub.count("/search.json", "google") >= 7


def test_empty_results_are_a_normal_billed_record(env, connected):
    record = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "__empty__"}},
    )
    assert (
        record["status"] == "ok"
        and record["result_count"] == 0
        and record["credits"] == 1
    )
    assert record["note"].startswith("Google hasn't returned")
    assert record["result"]["error"].startswith("Google hasn't")


def test_html_output_async_and_archive_html(env, connected):
    page = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "page"}, "options": {"output": "html"}},
    )
    assert (
        page["output"] == "html"
        and page["result"] is None
        and "Fixture page" in page["html"]
    )
    detail = connected.ok("GET", f"/api/airaapi/searches/{page['id']}?html=1")
    assert "Fixture page" in detail["html"]
    status, raw, headers = connected.get(
        f"/api/airaapi/searches/{page['id']}/export?format=html", raw=True
    )
    assert status == 200 and b"Fixture page" in raw
    assert headers["Content-Type"].startswith("text/html")
    assert 'filename="google-' in headers["Content-Disposition"]
    live = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "archive me"}},
    )
    assert "html" not in live
    lazy = connected.ok("GET", f"/api/airaapi/searches/{live['id']}?html=1")
    assert "Fixture page" in lazy["html"]  # fetched once from the free Search Archive
    assert env.stub.count("/searches") == 1
    connected.ok("GET", f"/api/airaapi/searches/{live['id']}?html=1")
    assert env.stub.count("/searches") == 1  # now stored locally
    queued = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "later"}, "options": {"async": True}},
    )
    assert queued["status"] == "running" and queued["credits"] == 0
    assert connected.ok("GET", "/api/airaapi/status")["queue"]["async_pending"] == 1
    still = connected.ok("GET", f"/api/airaapi/searches/{queued['id']}")
    assert still["status"] == "running"
    done = connected.ok("GET", f"/api/airaapi/searches/{queued['id']}")
    assert done["status"] == "ok" and done["credits"] == 1 and done["result_count"] == 5
    assert connected.ok("GET", "/api/airaapi/status")["queue"]["async_pending"] == 0


def test_exports_rerun_diff_and_delete(env, connected):
    first = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google_news", "params": {"q": "coffee"}},
    )
    env.stub.variant = 2
    second = connected.ok(
        "POST",
        "/api/airaapi/search",
        {
            "engine": "google_news",
            "params": {"q": "coffee"},
            "options": {"no_cache": True},
        },
    )
    assert second["result_count"] == 7
    status, body, headers = connected.get(
        f"/api/airaapi/searches/{first['id']}/export?format=csv", raw=True
    )
    assert status == 200 and headers["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    assert len(rows) == 5 and rows[0]["source.name"] == "Reuters"
    status, body, headers = connected.get(
        f"/api/airaapi/searches/{first['id']}/export?format=json", raw=True
    )
    document = json.loads(body)
    assert (
        document["search"]["engine"] == "google_news"
        and document["result"]["news_results"]
    )
    assert headers["Content-Disposition"].endswith('.json"')
    status, body, _ = connected.get(
        f"/api/airaapi/searches/{first['id']}/export?format=curl", raw=True
    )
    text = body.decode()
    assert "api_key=YOUR_SERPAPI_KEY" in text and KEY not in text
    status, payload, _ = connected.get(
        f"/api/airaapi/searches/{first['id']}/export?format=html"
    )
    assert status == 404 and "No raw page" in payload["error"]
    status, payload, _ = connected.get(
        f"/api/airaapi/searches/{first['id']}/export?format=pdf"
    )
    assert status == 400
    diff = connected.ok("GET", f"/api/airaapi/diff?a={first['id']}&b={second['id']}")
    assert (
        len(diff["added"]) == 2
        and diff["removed"] == []
        and "2 added" in diff["summary"]
    )
    assert diff["a"]["id"] == first["id"] and diff["list_key"] == "news_results"
    status, payload, _ = connected.get("/api/airaapi/diff?a=" + first["id"])
    assert status == 400
    status, payload, _ = connected.get(
        f"/api/airaapi/diff?a={first['id']}&b={'0' * 32}"
    )
    assert status == 404
    env.stub.variant = 0
    rerun = connected.ok("POST", f"/api/airaapi/searches/{first['id']}/rerun", {})
    assert (
        rerun["source"] == "rerun"
        and rerun["cache_hit"] is False
        and rerun["result_count"] == 5
    )
    cached = connected.ok(
        "POST",
        f"/api/airaapi/searches/{first['id']}/rerun",
        {"options": {"use_cache": True}},
    )
    assert cached["cache_hit"] is True
    connected.ok("DELETE", f"/api/airaapi/searches/{first['id']}")
    status, _, _ = connected.get(f"/api/airaapi/searches/{first['id']}")
    assert status == 404
    status, _, _ = connected.call("DELETE", f"/api/airaapi/searches/{first['id']}")
    assert status == 404
    status, body, _ = connected.call("DELETE", "/api/airaapi/searches")
    assert status == 400 and "never accidental" in body["error"]
    connected.ok("PATCH", f"/api/airaapi/searches/{second['id']}", {"pinned": True})
    outcome = connected.ok("DELETE", "/api/airaapi/searches?all=1")
    assert outcome["kept_pinned"] == 1 and outcome["deleted"] >= 2
    assert connected.ok("GET", "/api/airaapi/searches")["total"] == 1
    assert (
        connected.ok(
            "DELETE", "/api/airaapi/searches?engine=google_news&include_pinned=1"
        )["deleted"]
        == 1
    )


# -- settings ------------------------------------------------------------------------------


def test_settings_control_cache_window_and_local_limits(env, connected):
    settings = connected.ok("GET", "/api/settings")["settings"]
    assert (
        settings["airaapi_cache_ttl_minutes"] == "60"
        and settings["airaapi_hourly_limit"] == "0"
    )
    assert (
        settings["airaapi_retention_days"] == "30"
        and settings["airaapi_max_records"] == "2000"
    )
    for payload in (
        {"airaapi_cache_ttl_minutes": "soon"},
        {"airaapi_cache_ttl_minutes": -5},
        {"airaapi_hourly_limit": 10**9},
        {"airaapi_retention_days": 0},
        {"airaapi_max_records": 3},
        {"airaapi_result_cap_mb": 0},
    ):
        status, body, _ = connected.post("/api/settings", payload)
        assert status == 400, payload
    connected.ok("POST", "/api/settings", {"airaapi_cache_ttl_minutes": 0})
    for _ in range(2):
        connected.ok(
            "POST",
            "/api/airaapi/search",
            {"engine": "google", "params": {"q": "no cache window"}},
        )
    assert env.stub.search_calls == 2  # a zero window disables the cache
    connected.ok(
        "POST",
        "/api/settings",
        {"airaapi_cache_ttl_minutes": 60, "airaapi_hourly_limit": 3},
    )
    connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "a"}}
    )
    connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "a"}}
    )  # cached, free
    status, body, _ = connected.post(
        "/api/airaapi/search", {"engine": "google", "params": {"q": "b"}}
    )
    assert status == 429 and body["code"] == "local_rate_limit"
    assert connected.ok("GET", "/api/airaapi/status")["throttle"] == {
        "hourly_limit": 3,
        "used_this_hour": 3,
    }


def test_status_reports_reachability_and_the_local_server(env, connected):
    status = connected.ok("GET", "/api/airaapi/status")
    assert status["local"]["db_ok"] is True and status["local"]["version"]
    assert status["serpapi"]["reachable"] is True and status["serpapi"]["error"] is None
    assert status["throttle"] == {"hourly_limit": 0, "used_this_hour": 0}
    assert status["queue"] == {"running": 0, "async_pending": 0}
    calls = env.stub.count("/locations.json")
    connected.ok("GET", "/api/airaapi/status")
    assert env.stub.count("/locations.json") == calls  # probe is cached for 30 seconds
    env.stub.stop()
    down = connected.ok("GET", "/api/airaapi/status?refresh=1")
    assert down["serpapi"]["reachable"] is False and down["serpapi"]["error"]
    overview = connected.ok("GET", "/api/airaapi/overview")
    assert overview["configured"] is True and overview["account_error"]


# -- saved searches, monitors and the scheduler ---------------------------------------


def test_saved_searches_and_monitor_endpoints(env, connected):
    status, body, _ = connected.post(
        "/api/airaapi/saved", {"name": "bad", "engine": "google", "params": {}}
    )
    assert status == 400
    saved = connected.ok(
        "POST",
        "/api/airaapi/saved",
        {
            "name": "Barista jobs",
            "engine": "google_jobs",
            "params": {"q": "barista"},
            "tags": ["jobs"],
        },
        expect=201,
    )
    assert saved["tags"] == ["jobs"] and saved["engine_name"] == "Google Jobs"
    assert connected.ok("GET", "/api/airaapi/saved")["saved"][0]["id"] == saved["id"]
    record = connected.ok("POST", f"/api/airaapi/saved/{saved['id']}/run", {})
    assert record["label"] == "Barista jobs" and record["result_count"] == 4
    listed = connected.ok("GET", "/api/airaapi/saved")["saved"][0]
    assert listed["last_search_id"] == record["id"] and listed["last_run_at"]
    renamed = connected.ok(
        "PATCH",
        f"/api/airaapi/saved/{saved['id']}",
        {"name": "Renamed", "pinned": True},
    )
    assert renamed["name"] == "Renamed" and renamed["pinned"]
    status, body, _ = connected.post(
        "/api/airaapi/monitors", {"saved_id": saved["id"], "interval_minutes": 5}
    )
    assert status == 400 and "every 15 minutes" in body["error"]
    monitor = connected.ok(
        "POST",
        "/api/airaapi/monitors",
        {"saved_id": saved["id"], "interval_minutes": 15, "notify_on_change": True},
        expect=201,
    )
    assert monitor["estimated_monthly_searches"] == 2880 and monitor["enabled"] is True
    status, body, _ = connected.post(
        "/api/airaapi/monitors", {"saved_id": saved["id"], "interval_minutes": 30}
    )
    assert status == 400 and "already has a monitor" in body["error"]
    listing = connected.ok("GET", "/api/airaapi/monitors")
    assert listing["monitors"][0]["saved_name"] == "Renamed"
    assert listing["limits"] == {"min_interval_minutes": 15, "max_monitors": 25}
    paused = connected.ok(
        "PATCH", f"/api/airaapi/monitors/{monitor['id']}", {"enabled": False}
    )
    assert paused["enabled"] is False
    assert connected.ok("GET", "/api/airaapi/overview")["monitors"]["active"] == 0
    connected.ok(
        "PATCH",
        f"/api/airaapi/monitors/{monitor['id']}",
        {"enabled": True, "interval_minutes": 30},
    )
    first = connected.ok("POST", f"/api/airaapi/monitors/{monitor['id']}/run", {})
    assert first["diff"] is None and first["summary"].startswith(
        "Baseline captured (4 results)"
    )
    assert first["record"]["source"] == "monitor"
    assert env.stub.calls[-1].params["no_cache"] == "true"
    env.stub.variant = 1
    second = connected.ok("POST", f"/api/airaapi/monitors/{monitor['id']}/run", {})
    assert second["diff"]["counts"]["added"] == 1 and second["summary"].startswith(
        "1 added"
    )
    notifications = connected.ok("GET", "/api/notifications")["notifications"]
    assert [item["title"] for item in notifications] == ["Monitor changed: Renamed"]
    assert notifications[0]["link"].startswith("#/airaapi?tab=history&search=")
    connected.ok("DELETE", f"/api/airaapi/monitors/{monitor['id']}")
    connected.ok("DELETE", f"/api/airaapi/saved/{saved['id']}")
    status, _, _ = connected.call("DELETE", f"/api/airaapi/saved/{saved['id']}")
    assert status == 404
    assert connected.ok("GET", "/api/airaapi/monitors")["monitors"] == []


def _make_monitor(connected, interval=15):
    saved = connected.ok(
        "POST",
        "/api/airaapi/saved",
        {"name": "Watch", "engine": "google", "params": {"q": "coffee"}},
        expect=201,
    )
    return connected.ok(
        "POST",
        "/api/airaapi/monitors",
        {"saved_id": saved["id"], "interval_minutes": interval},
        expect=201,
    )


def _make_due(env, monitor_id):
    with env.user_db() as connection:
        connection.execute(
            "UPDATE airaapi_monitors SET next_run_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (monitor_id,),
        )


def _user_row(env):
    with env.accounts_db() as connection:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute("SELECT * FROM users").fetchone())


def test_scheduler_runs_due_monitors_and_notifies(env, connected):
    monitor = _make_monitor(connected)
    user = _user_row(env)
    app._run_airaapi_monitors_for(user)  # nothing is due yet
    assert env.stub.search_calls == 0
    _make_due(env, monitor["id"])
    app._run_airaapi_monitors_for(user)
    assert env.stub.search_calls == 1
    listed = connected.ok("GET", "/api/airaapi/monitors")["monitors"][0]
    assert (
        listed["last_change_summary"].startswith("Baseline captured")
        and listed["last_run_at"]
    )
    assert listed["next_run_at"] > listed["last_run_at"]
    env.stub.variant = -1
    _make_due(env, monitor["id"])
    app._run_airaapi_monitors_for(user)
    assert connected.ok("GET", "/api/airaapi/monitors")["monitors"][0][
        "last_change_summary"
    ].startswith("1 removed")
    assert [
        item["title"]
        for item in connected.ok("GET", "/api/notifications")["notifications"]
    ] == ["Monitor changed: Watch"]
    history = connected.ok("GET", "/api/airaapi/searches?source=monitor")
    assert history["total"] == 2


def test_scheduler_tick_spawns_one_worker_per_account(env, connected):
    monitor = _make_monitor(connected)
    _make_due(env, monitor["id"])
    app._run_due_airaapi_monitors()
    deadline = time.time() + 20
    while time.time() < deadline and env.stub.search_calls < 1:
        time.sleep(0.05)
    assert env.stub.search_calls == 1
    deadline = time.time() + 5
    while (
        time.time() < deadline
        and f"airaapi:{_user_row(env)['user_id']}" in app._automation_running
    ):
        time.sleep(0.05)
    assert not any(name.startswith("airaapi:") for name in app._automation_running)


def test_scheduler_skips_accounts_without_a_key(env, api):
    saved = api.ok(
        "POST",
        "/api/airaapi/saved",
        {"name": "Watch", "engine": "google", "params": {"q": "x"}},
        expect=201,
    )
    monitor = api.ok(
        "POST",
        "/api/airaapi/monitors",
        {"saved_id": saved["id"], "interval_minutes": 15},
        expect=201,
    )
    _make_due(env, monitor["id"])
    app._run_airaapi_monitors_for(_user_row(env))
    assert env.stub.search_calls == 0


def test_scheduler_records_failures_without_raising(env, connected):
    monitor = _make_monitor(connected)
    saved_id = connected.ok("GET", "/api/airaapi/saved")["saved"][0]["id"]
    with env.user_db() as connection:
        connection.execute(
            "UPDATE airaapi_saved SET params_json = ? WHERE id = ?",
            (json.dumps({"q": "__rate_limit__"}), saved_id),
        )
    _make_due(env, monitor["id"])
    app._run_airaapi_monitors_for(_user_row(env))
    listed = connected.ok("GET", "/api/airaapi/monitors")["monitors"][0]
    assert listed["last_change_summary"].startswith("Run failed:")
    assert listed["next_run_at"] > "2001-01-01"


# -- API keys and /v1 ------------------------------------------------------------


def create_key(api: Api, name="script", hourly_limit=None) -> dict:
    body = {"name": name}
    if hourly_limit is not None:
        body["hourly_limit"] = hourly_limit
    return api.ok("POST", "/api/airaapi/keys", body, expect=201)


def test_aira_keys_show_the_secret_once_and_store_only_a_hash(env, api, caplog):
    created = create_key(api, "ci job", 100)
    secret = created["secret"]
    assert KEY_PATTERN.match(secret)
    key = created["key"]
    assert (
        key["name"] == "ci job"
        and key["prefix"] == secret[:9]
        and key["hourly_limit"] == 100
    )
    assert (
        key["used_this_hour"] == 0
        and key["revoked_at"] is None
        and key["last_used_at"] is None
    )
    assert set(key) == {
        "id",
        "name",
        "prefix",
        "created_at",
        "last_used_at",
        "revoked_at",
        "hourly_limit",
        "used_this_hour",
        # client terms (reselling): none of them is secret
        "client_name",
        "price_per_1000",
        "monthly_quota",
        "allowed_engines",
        "used_this_month",
        "quota_left",
    }
    listing = api.ok("GET", "/api/airaapi/keys")["keys"]
    assert [item["id"] for item in listing] == [key["id"]]
    assert "secret" not in listing[0] and "key_hash" not in listing[0]
    with env.user_db() as connection:
        stored = connection.execute("SELECT key_hash FROM airaapi_api_keys").fetchone()[
            0
        ]
    assert stored == hashlib.sha256(secret.encode()).hexdigest()
    with env.accounts_db() as connection:
        assert (
            connection.execute("SELECT key_hash FROM airaapi_key_index").fetchone()[0]
            == stored
        )
    assert secret.encode() not in env.database_bytes()
    assert (
        sum(secret.encode() in body for body in env.transcript) == 1
    )  # only the create response
    for payload, message in (
        ({"name": ""}, "Give the key a name"),
        ({"name": "x" * 81}, "up to 80"),
        ({"name": "x", "hourly_limit": -1}, "between 0"),
        ({"name": "x", "hourly_limit": "lots"}, "whole number"),
    ):
        status, body, _ = api.post("/api/airaapi/keys", payload)
        assert status == 400 and message in body["error"], payload
    assert_secret_absent(env, caplog, secret, responses_allowed=1)


def test_v1_search_with_query_key_and_bearer_token(env, connected, caplog):
    secret = create_key(connected)["secret"]
    status, body, headers = env.v1(
        "/v1/search.json?engine=google_jobs&q=barista&location=New+York", key=secret
    )
    assert status == 200 and body["jobs_results"][0]["title"] == "Barista"
    assert (
        body["search_metadata"]["aira"]["cache_hit"] is False
        and body["search_metadata"]["aira"]["id"]
    )
    assert body["search_metadata"]["fixture"] is True
    assert "Access-Control-Allow-Origin" not in headers  # loopback only, no CORS
    assert env.stub.search_calls == 1 and env.stub.calls[-1].key_digest == digest_of(
        KEY
    )
    status, body, _ = env.v1(
        "/v1/search?engine=google_jobs&q=barista&location=New+York", bearer=secret
    )
    assert status == 200 and body["search_metadata"]["aira"]["cache_hit"] is True
    assert env.stub.search_calls == 1  # served from Aira's cache, SerpApi untouched
    status, body, _ = env.v1(
        "/v1/search.json?q=coffee", key=secret
    )  # engine defaults to google
    assert status == 200 and body["organic_results"] and env.stub.search_calls == 2
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=x&no_cache=true&json_restrictor=organic_results",
        key=secret,
    )
    assert status == 200 and set(body) == {
        "search_metadata",
        "search_parameters",
        "organic_results",
    }
    assert env.stub.calls[-1].params["no_cache"] == "true"
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=hello&output=html", key=secret
    )
    assert status == 200 and "Fixture page" in body
    for path, message in (
        ("/v1/search.json?engine=google", "'q' is required"),
        (
            "/v1/search.json?engine=google&q=x&bogus=1&allow_unknown=false",
            "Unknown parameter 'bogus'",
        ),
        ("/v1/search.json?engine=google&q=x&start=abc", "whole number"),
    ):
        status, body, _ = env.v1(path, key=secret)
        assert status == 400 and message in body["error"], path
    # Like SerpApi, parameters the catalog does not know pass through (its SDKs
    # add some of their own, for example source=python).
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=x&bogus=1&source=python", key=secret
    )
    assert status == 200 and env.stub.calls[-1].params["bogus"] == "1"
    assert env.stub.calls[-1].params["source"] == "python"
    status, body, _ = env.v1("/v1/search.json?engine=nope&q=x", key=secret)
    assert status == 404
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=__out_of_searches__", key=secret
    )
    assert status == 402 and body["code"] == "out_of_searches"
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=__invalid_key__", key=secret
    )
    assert (
        status == 502 and body["code"] == "invalid_api_key"
    )  # the upstream key problem is not the caller's 401
    history = connected.ok("GET", "/api/airaapi/searches?source=api")
    assert history["total"] >= 6 and all(
        item["label"] == "API key: script" for item in history["items"]
    )
    assert_secret_absent(env, caplog, KEY)
    assert_secret_absent(
        env, caplog, secret, responses_allowed=1
    )  # shown once, at creation
    # The 'api' calls also show up in key usage and the overview source split.
    keys = connected.ok("GET", "/api/airaapi/keys")["keys"]
    assert keys[0]["used_this_hour"] >= 5 and keys[0]["last_used_at"]
    sources = {
        item["source"]: item["searches"]
        for item in connected.ok("GET", "/api/airaapi/overview")["usage"]["by_source"]
    }
    assert sources["api"] >= 6


def test_v1_rejects_missing_wrong_malformed_and_revoked_keys(env, connected):
    created = create_key(connected)
    secret = created["secret"]
    for presented in (
        None,
        "aira_" + "x" * 40,
        "aira_short",
        "not-aira-key",
        KEY,
        secret[:-1] + ("a" if secret[-1] != "a" else "b"),
    ):
        status, body, _ = env.v1("/v1/search.json?engine=google&q=x", key=presented)
        assert status == 401 and body["error"].startswith("Invalid API key."), presented
    assert env.stub.search_calls == 0
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=x", key="ignored", bearer=secret
    )
    assert status == 200  # a Bearer token wins over the query parameter
    revoked = connected.ok("DELETE", f"/api/airaapi/keys/{created['key']['id']}")
    assert revoked["ok"] and revoked["key"]["revoked_at"]
    status, body, _ = env.v1("/v1/search.json?engine=google&q=x", key=secret)
    assert status == 401
    with env.accounts_db() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM airaapi_key_index").fetchone()[0]
            == 0
        )
    status, _, _ = connected.call("DELETE", f"/api/airaapi/keys/{'0' * 32}")
    assert status == 404
    listed = connected.ok("GET", "/api/airaapi/keys")["keys"]
    assert listed[0]["revoked_at"]


def test_v1_enforces_each_keys_hourly_limit(env, connected):
    limited = create_key(connected, "limited", 2)["secret"]
    free = create_key(connected, "free")["secret"]
    for query in ("a", "b"):
        status, _, _ = env.v1(f"/v1/search.json?engine=google&q={query}", key=limited)
        assert status == 200
    status, body, _ = env.v1("/v1/search.json?engine=google&q=c", key=limited)
    assert (
        status == 429
        and "2 searches for the hour" in body["error"]
        and body["code"] == "rate_limited"
    )
    status, _, _ = env.v1("/v1/search.json?engine=google&q=c", key=free)
    assert status == 200
    assert env.stub.search_calls == 3
    keys = {
        item["name"]: item for item in connected.ok("GET", "/api/airaapi/keys")["keys"]
    }
    assert (
        keys["limited"]["used_this_hour"] == 2 and keys["free"]["used_this_hour"] == 1
    )
    status, _, _ = env.v1("/v1/account.json", key=limited)
    assert status == 200  # non-search calls are not metered


def test_failed_key_attempts_are_rate_limited(env, api):
    for _ in range(8):
        status, _, _ = env.v1(
            "/v1/search.json?engine=google&q=x", key="aira_" + "z" * 40
        )
        assert status == 401
    status, body, _ = env.v1(
        "/v1/search.json?engine=google&q=x", key="aira_" + "z" * 40
    )
    assert status == 429 and "Too many rejected keys" in body["error"]


def test_v1_account_locations_searches_and_engines(env, connected):
    secret = create_key(connected)["secret"]
    status, account, _ = env.v1("/v1/account.json", key=secret)
    assert status == 200 and account["plan_name"] == "Developer Plan"
    assert "api_key" not in account and KEY not in json.dumps(account)
    assert account["account_rate_limit_per_hour"] == 1000
    status, locations, _ = env.v1("/v1/locations.json?q=austin&limit=2", key=secret)
    assert (
        status == 200
        and isinstance(locations, list)
        and locations[0]["name"] == "Austin, TX"
    )
    status, body, _ = env.v1("/v1/locations.json?q=a", key=secret)
    assert status == 400
    status, first, _ = env.v1("/v1/search.json?engine=bing&q=tea", key=secret)
    assert status == 200
    aira_id = first["search_metadata"]["aira"]["id"]
    status, listing, _ = env.v1("/v1/searches.json?engine=bing", key=secret)
    assert (
        status == 200 and listing["total"] == 1 and listing["items"][0]["id"] == aira_id
    )
    assert "result" not in listing["items"][0]
    status, stored, _ = env.v1(f"/v1/searches/{aira_id}.json", key=secret)
    assert status == 200 and stored["organic_results"] == first["organic_results"]
    serpapi_id = first["search_metadata"]["id"]
    status, by_serpapi_id, _ = env.v1(f"/v1/searches/{serpapi_id}", key=secret)
    assert (
        status == 200 and by_serpapi_id["organic_results"] == first["organic_results"]
    )
    status, page, headers = env.v1(f"/v1/searches/{aira_id}.html", key=secret)
    assert (
        status == 200
        and "Fixture page" in page
        and headers["Content-Type"].startswith("text/html")
    )
    status, body, _ = env.v1("/v1/searches/does-not-exist.json", key=secret)
    assert status == 404
    status, engines, _ = env.v1("/v1/engines.json", key=secret)
    assert (
        status == 200
        and len(engines["engines"]) == 137
        and "params" not in engines["engines"][0]
    )
    status, full, _ = env.v1("/v1/engines.json?full=1", key=secret)
    assert full["engines"][0]["params"]
    request = urllib.request.Request(
        env.base + "/v1/search.json?engine=google&q=x&api_key=" + secret,
        method="POST",
        data=b"{}",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=30)
    assert caught.value.code in {403, 404}


def test_v1_without_a_serpapi_key_serves_cache_and_explains_the_rest(env, api):
    secret = create_key(api)["secret"]
    status, body, _ = env.v1("/v1/search.json?engine=google&q=x", key=secret)
    assert status == 409 and body["code"] == "not_configured"
    status, body, _ = env.v1("/v1/account.json", key=secret)
    assert status == 409
    assert env.stub.search_calls == 0


def test_accounts_are_isolated_and_deleting_one_removes_its_keys(env, connected):
    secret = create_key(connected)["secret"]
    other = env.client()
    register(other, "other@example.com")
    connect_key(other, KEY2)
    connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "mine"}}
    )
    assert other.ok("GET", "/api/airaapi/searches")["total"] == 0
    assert other.ok("GET", "/api/airaapi/keys")["keys"] == []
    mine = connected.ok("GET", "/api/airaapi/searches")["items"][0]["id"]
    status, _, _ = other.get(f"/api/airaapi/searches/{mine}")
    assert status == 404
    other.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "theirs"}}
    )
    assert env.stub.calls[-1].key_digest == digest_of(KEY2)
    status, listing, _ = env.v1("/v1/searches.json", key=secret)
    assert listing["total"] == 1 and listing["items"][0]["params"] == {"q": "mine"}
    status, body, _ = env.v1("/v1/search.json?engine=google&q=viaA", key=secret)
    assert status == 200 and env.stub.calls[-1].key_digest == digest_of(KEY)
    status, payload, _ = connected.call(
        "POST", "/api/account/delete", {"password": PASSWORD}
    )
    assert status == 200
    status, body, _ = env.v1("/v1/search.json?engine=google&q=x", key=secret)
    assert status == 401
    with env.accounts_db() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM airaapi_key_index").fetchone()[0]
            == 0
        )


def test_account_export_contains_saved_work_but_no_secrets(env, connected, caplog):
    create_key(connected)
    connected.ok(
        "POST",
        "/api/airaapi/saved",
        {"name": "Keep", "engine": "google", "params": {"q": "x"}},
        expect=201,
    )
    status, raw, _ = connected.get("/api/account/export", raw=True)
    exported = json.loads(raw)
    assert [row["name"] for row in exported["tables"]["airaapi_saved"]] == ["Keep"]
    assert (
        "airaapi_secrets" not in exported["tables"]
        and "airaapi_api_keys" not in exported["tables"]
    )
    assert KEY not in raw.decode()
    assert_secret_absent(env, caplog, KEY)


def test_request_ids_and_security_headers_on_new_routes(env, connected):
    status, _, headers = connected.get(
        "/api/airaapi/overview", headers={"X-Request-ID": "trace-1234"}
    )
    assert headers["X-Request-ID"] == "trace-1234"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert (
        headers["X-Content-Type-Options"] == "nosniff"
        and headers["Cache-Control"] == "no-store"
    )
    metrics = connected.ok("GET", "/api/metrics")
    assert any(name.startswith("airaapi__routes__") for name in metrics["routes"])


def test_v1_works_at_serpapi_root_paths_and_rewrites_links(env, connected):
    """An SDK only needs its base URL pointed at Aira: same paths as serpapi.com."""
    secret = create_key(connected)["secret"]
    status, body, _ = env.v1("/search.json?engine=google&q=coffee", key=secret)
    assert status == 200 and body["organic_results"]
    metadata = body["search_metadata"]
    assert metadata["json_endpoint"] == f"{env.base}/searches/{metadata['id']}.json"
    assert body["serpapi_pagination"]["next"].startswith(f"{env.base}/search.json?")
    assert env.base not in json.dumps(body["search_metadata"]["google_url"])
    # The rewritten archive link resolves by SerpApi's own search id.
    status, again, _ = env.v1(f"/searches/{metadata['id']}.json", key=secret)
    assert status == 200 and again["search_metadata"]["id"] == metadata["id"]
    status, page, _ = env.v1("/search?engine=google&q=coffee", key=secret)
    assert status == 200 and page["search_metadata"]["aira"]["cache_hit"] is True
    status, html, headers = env.v1("/search.html?engine=google&q=coffee", key=secret)
    assert status == 200 and "Fixture page" in html
    assert headers["Content-Type"].startswith("text/html")
    status, account, _ = env.v1("/account.json", key=secret)
    assert status == 200 and "plan_name" in account and "api_key" not in account
    status, _, _ = env.v1("/search.json?engine=google&q=coffee")
    assert status == 401


def test_locations_need_no_key_like_serpapi_but_a_bad_key_is_still_refused(env, api):
    status, found, _ = env.v1("/locations.json?q=Austin&limit=2")
    assert status == 200 and isinstance(found, list) and found
    status, found, _ = env.v1("/v1/locations.json?q=Austin&limit=2")
    assert status == 200 and found
    status, body, _ = env.v1("/locations.json?q=Austin", key="aira_" + "x" * 40)
    assert status == 401 and body["code"] == "invalid_aira_key"
    status, body, _ = env.v1("/locations.json?q=A")
    assert status == 400


# -- security regressions (adversarial review) ------------------------------------


def create_aira_key(api: Api, name="regression", hourly_limit=0) -> dict:
    return api.ok(
        "POST", "/api/airaapi/keys", {"name": name, "hourly_limit": hourly_limit}, 201
    )


def test_debug_log_hides_aira_keys_however_the_query_spells_them(
    env, connected, caplog
):
    secret = create_aira_key(connected)["secret"]
    spellings = (
        f"/v1/search.json?engine=google&q=log1&api_key={secret}",
        f"/v1/search.json?engine=google&q=log2&api%5Fkey={secret}",
        f"/search.json?engine=google&q=log3&%61pi_key={secret}",
        f"/search.json?engine=google&q=log4&api_key={secret.replace('_', '%5F')}",
    )
    for path in spellings:
        status, _, _ = env.v1(path)
        assert status == 200, path
    status, _, _ = env.v1("/search.json?engine=google&q=log5", bearer=secret)
    assert status == 200
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "GET /v1/search.json" in logs  # the lines are logged, just redacted
    assert secret not in logs and secret.replace("_", "%5F") not in logs
    assert secret[5:25] not in logs


def test_a_valid_key_is_never_locked_out_by_rejected_ones(env, connected):
    """Every local process and web page shares one client address; junk must not lock out the owner."""
    secret = create_aira_key(connected)["secret"]
    junk = "aira_" + "z" * 40
    for _ in range(8):
        assert env.v1("/v1/account.json", key=junk)[0] == 401
    status, body, _ = env.v1("/v1/account.json", key=junk)
    assert status == 429 and "Too many rejected keys" in body["error"]
    status, body, _ = env.v1("/v1/account.json", key=secret)
    assert status == 200, body
    status, _, _ = env.v1("/v1/account.json", bearer=secret)
    assert status == 200
    assert env.v1("/v1/account.json", key=junk)[0] == 429  # junk stays throttled


def test_pages_saved_from_a_search_engine_are_served_sandboxed(env, connected):
    secret = create_aira_key(connected)["secret"]
    status, page, headers = env.v1(
        "/v1/search.json?engine=google&q=pages&output=html", key=secret
    )
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    policies = headers.get_all("Content-Security-Policy")
    assert any(
        item.startswith("sandbox") and "form-action 'none'" in item for item in policies
    )
    assert any(
        item.startswith("default-src 'self'") for item in policies
    )  # the app policy stays
    record = connected.ok("GET", "/api/airaapi/searches?q=pages")["items"][0]
    status, _, headers = env.v1(f"/searches/{record['id']}.html", key=secret)
    assert status == 200
    assert any(
        item.startswith("sandbox")
        for item in headers.get_all("Content-Security-Policy")
    )
    # JSON answers keep only the app policy
    status, _, headers = env.v1("/v1/search.json?engine=google&q=pages2", key=secret)
    assert not any(
        item.startswith("sandbox")
        for item in headers.get_all("Content-Security-Policy")
    )


def test_keyless_locations_are_cached_and_rate_limited(env, api, monkeypatch):
    from aira.airaapi import routes

    monkeypatch.setattr(routes, "KEYLESS_LOCATIONS_PER_MINUTE", 3)
    for _ in range(5):
        assert env.v1("/locations.json?q=austin")[0] == 200
    assert env.stub.count("/locations.json") == 1  # repeats come from memory
    assert env.v1("/locations.json?q=boston")[0] == 200
    assert env.v1("/locations.json?q=chicago")[0] == 200
    status, body, _ = env.v1("/locations.json?q=denver")
    assert status == 429 and body["code"] == "rate_limited"
    assert env.stub.count("/locations.json") == 3
    assert env.v1("/locations.json?q=austin")[0] == 200  # cached answers are still free


def test_history_cap_holds_when_every_request_is_a_cache_hit(env, connected):
    connected.ok("POST", "/api/settings", {"airaapi_max_records": 50})
    secret = create_aira_key(connected)["secret"]
    for _ in range(90):
        assert env.v1("/v1/search.json?engine=google&q=hammer", key=secret)[0] == 200
    assert env.stub.count("/search.json") == 1
    total = connected.ok("GET", "/api/airaapi/searches?limit=1")["total"]
    assert total <= 60, total


def test_per_key_hourly_limit_holds_under_concurrent_requests(env, connected):
    secret = create_aira_key(connected, hourly_limit=2)["secret"]
    codes: list[int] = []

    def go(index: int) -> None:
        codes.append(
            env.v1(f"/v1/search.json?engine=google&q=race{index}", key=secret)[0]
        )

    threads = [threading.Thread(target=go, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert codes.count(200) == 2 and codes.count(429) == 8


def test_a_decompression_bomb_in_a_stored_result_is_refused_not_inflated(
    env, connected
):
    import zlib

    record = connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "bomb"}}
    )
    packer = zlib.compressobj(6)
    chunk = b"\0" * (1 << 20)
    bomb = b"".join([packer.compress(chunk) for _ in range(130)] + [packer.flush()])
    db = env.user_db()
    db.execute(
        "UPDATE airaapi_searches SET result_blob = ? WHERE id = ?", (bomb, record["id"])
    )
    db.commit()
    db.close()
    started = time.monotonic()
    status, body, _ = connected.get(f"/api/airaapi/searches/{record['id']}")
    assert status == 400 and "larger than Aira stores" in body["error"]
    assert time.monotonic() - started < 10
    assert connected.get("/api/airaapi/overview")[0] == 200  # the server is fine
    status, _, _ = connected.get(
        f"/api/airaapi/searches/{record['id']}/export?format=csv"
    )
    assert status == 400


def test_dns_rebinding_cannot_reach_the_aira_api_routes(env, connected):
    """A page re-pointed at 127.0.0.1 is same-origin with itself; the Host header gives it away."""
    import http.client

    secret = create_aira_key(connected)["secret"]
    port = int(env.base.rsplit(":", 1)[1])

    def ask(path, host, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.putrequest("GET", path, skip_host=True)
        connection.putheader("Host", host)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    jar = next(
        item.cookiejar
        for item in connected.opener.handlers
        if hasattr(item, "cookiejar")
    )
    cookie = {"Cookie": "; ".join(f"{c.name}={c.value}" for c in jar)}
    assert cookie["Cookie"]  # a real signed-in session, replayed under a foreign Host
    for host in ("evil.example:8765", "evil.example", "127.0.0.1.evil.example"):
        for path in (
            f"/v1/account.json?api_key={secret}",
            "/locations.json?q=austin",
            "/v1/engines.json",
            "/api/airaapi/overview",
            "/api/careers/overview",
        ):
            status, body = ask(path, host, cookie)
            assert status == 403 and b"loopback" in body, (host, path, status)
    assert ask(f"/v1/account.json?api_key={secret}", f"127.0.0.1:{port}")[0] == 200
    assert ask(f"/v1/account.json?api_key={secret}", f"localhost:{port}")[0] == 200


def test_archive_answers_410_for_a_deleted_or_pruned_search(env, connected):
    """SerpApi answers 410 for an expired archive id; Aira keeps a tombstone to do the same."""
    secret = create_key(connected)["secret"]
    status, body, _ = env.v1("/search.json?engine=google&q=gone", key=secret)
    assert status == 200
    aira_id = body["search_metadata"]["aira"]["id"]
    serpapi_id = body["search_metadata"]["id"]
    connected.ok("DELETE", f"/api/airaapi/searches/{aira_id}")
    for search_id in (aira_id, serpapi_id):
        status, body, _ = env.v1(f"/searches/{search_id}.json", key=secret)
        assert status == 410 and body["code"] == "gone", search_id
    status, body, _ = env.v1(
        "/searches/0123456789abcdef0123456789abcdef.json", key=secret
    )
    assert status == 404  # never existed
    # Bulk delete and retention pruning leave tombstones too.
    status, body, _ = env.v1("/search.json?engine=google&q=pruned", key=secret)
    pruned_id = body["search_metadata"]["aira"]["id"]
    connected.ok("DELETE", "/api/airaapi/searches?before=2999-01-01&engine=google")
    status, body, _ = env.v1(f"/searches/{pruned_id}.json", key=secret)
    assert status == 410


def test_every_route_refuses_a_non_loopback_host(env, api):
    """DNS rebinding sends a foreign Host to 127.0.0.1; every route must refuse it."""
    for path in ("/api/health", "/api/dashboard", "/v1/engines.json", "/"):
        status, body, _ = env.v1(path, headers={"Host": "evil.example.com"})
        assert status == 403 and "loopback" in body["error"], path
    status, _, _ = env.v1("/api/health", headers={"Host": "localhost"})
    assert status == 200
    status, _, _ = env.v1("/api/health", headers={"Host": "127.0.0.1:8765"})
    assert status == 200


def test_a_deeply_nested_request_body_is_a_400_not_a_crash(env):
    request = urllib.request.Request(
        env.base + "/api/auth/login",
        data=b"[" * 20000 + b"]" * 20000,
        method="POST",
        headers={"Content-Type": "application/json", "X-Requested-With": "aira"},
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=30)
    assert caught.value.code == 400
    assert "nested too deeply" in json.loads(caught.value.read())["error"]
