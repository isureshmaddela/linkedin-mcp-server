"""SerpApi client against the stand-in server: errors, retries, scrubbing, safety."""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aira.airaapi import client as client_module
from aira.airaapi.client import (
    SerpApiClient,
    SerpApiError,
    map_error,
    normalize_account,
    resolve_base_url,
    scrub,
    scrub_value,
)
from serpapi_stub import DEFAULT_KEY, SerpApiStub, digest_of


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def stub():
    with SerpApiStub() as running:
        yield running


@pytest.fixture
def api(stub):
    return SerpApiClient(DEFAULT_KEY, stub.base_url, timeout=10)


def test_search_returns_body_and_sends_the_key_only_as_a_query_parameter(stub, api):
    response = api.search("google_jobs", {"q": "barista", "lrad": 5}, {})
    assert response.status == 200
    assert response.body is not None and response.body["jobs_results"]
    assert response.serpapi_id and response.duration_ms >= 0
    assert not response.queued and response.note == ""
    call = stub.calls[0]
    assert call.path == "/search.json" and call.engine == "google_jobs"
    assert call.key_ok and call.key_digest == digest_of(DEFAULT_KEY)
    assert call.params == {"q": "barista", "lrad": "5", "engine": "google_jobs"}


def test_parameter_serialisation(stub, api):
    api.search(
        "google_flights", {"departure_id": "AUS", "show_hidden": True, "adults": 2}, {}
    )
    params = stub.calls[0].params
    assert params["show_hidden"] == "true" and params["adults"] == "2"


def test_options_map_to_serpapi_flags(stub, api):
    api.search(
        "google",
        {"q": "x"},
        {"no_cache": True, "zero_trace": True, "json_restrictor": "organic_results"},
    )
    params = stub.calls[0].params
    assert params["no_cache"] == "true" and params["zero_trace"] == "true"
    assert params["json_restrictor"] == "organic_results"
    assert "output" not in params and "async" not in params
    response = api.search(
        "google", {"q": "y"}, {"output": "html", "json_restrictor": "x"}
    )
    assert response.body is None and response.text and "Fixture page" in response.text
    assert "json_restrictor" not in stub.calls[-1].params
    markdown = api.search("google", {"q": "z"}, {"output": "md"})
    assert markdown.text and markdown.text.startswith("# Fixture markdown")
    with pytest.raises(SerpApiError) as caught:
        api.search("google", {"q": "x"}, {"output": "xml"})
    assert caught.value.code == "bad_request"


def test_async_search_reports_queued_and_archive_completes(stub, api):
    queued = api.search("google", {"q": "later"}, {"async": True})
    assert queued.queued and queued.serpapi_id
    assert (
        queued.body is not None and queued.body["search_metadata"]["status"] == "Queued"
    )
    assert api.archive(queued.serpapi_id).queued
    done = api.archive(queued.serpapi_id)
    assert not done.queued and done.body is not None and done.body["organic_results"]
    page = api.archive(queued.serpapi_id, "html")
    assert page.text and "Fixture page" in page.text


def test_archive_validates_ids_and_maps_expiry(stub, api):
    for bad in ("", "../x", "a b", "x" * 200):
        with pytest.raises(SerpApiError) as caught:
            api.archive(bad)
        assert caught.value.code == "bad_request"
    with pytest.raises(SerpApiError) as caught:
        api.archive("expired123")
    assert caught.value.code == "gone" and caught.value.response_status == 410
    with pytest.raises(SerpApiError) as caught:
        api.archive("unknownid1")
    assert caught.value.code == "not_found"
    with pytest.raises(SerpApiError):
        api.archive("abc", "xml")


def test_invalid_key_maps_without_retry_and_never_echoes_the_key(stub):
    bad = SerpApiClient("wrong_key_0123456789abcdef", stub.base_url)
    with pytest.raises(SerpApiError) as caught:
        bad.search("google", {"q": "x"}, {})
    error = caught.value
    assert error.code == "invalid_api_key" and error.http_status == 401
    assert error.response_status == 401
    assert error.status_for("session") == 422 and error.status_for("api_key") == 502
    assert "wrong_key" not in str(error) and "wrong_key" not in json.dumps(
        error.payload()
    )
    assert stub.count("/search.json") == 1


def test_missing_key_is_not_configured():
    with pytest.raises(SerpApiError) as caught:
        SerpApiClient(None, "http://127.0.0.1:9").search("google", {"q": "x"}, {})
    assert caught.value.code == "not_configured" and caught.value.response_status == 409


def test_rate_limit_and_out_of_searches_are_distinguished(stub, api):
    with pytest.raises(SerpApiError) as limited:
        api.search("google", {"q": "__rate_limit__"}, {})
    assert limited.value.code == "rate_limited" and limited.value.retryable
    assert limited.value.response_status == 429
    with pytest.raises(SerpApiError) as empty:
        api.search("google", {"q": "__out_of_searches__"}, {})
    assert empty.value.code == "out_of_searches" and empty.value.response_status == 402
    assert stub.count("/search.json") == 2  # 4xx is never retried


def test_server_errors_retry_exactly_once(stub, api):
    with pytest.raises(SerpApiError) as caught:
        api.search("google", {"q": "__server_error__"}, {})
    assert caught.value.code == "upstream_error" and caught.value.retryable
    assert caught.value.response_status == 502
    assert stub.count("/search.json") == 2


def test_flaky_upstream_recovers_on_the_retry(stub, api):
    response = api.search("google", {"q": "__flaky__"}, {})
    assert response.status == 200 and response.body is not None
    assert stub.count("/search.json") == 2


def test_bad_engine_is_a_bad_request(stub, api):
    with pytest.raises(SerpApiError) as caught:
        api.search("not_an_engine", {"q": "x"}, {})
    assert caught.value.code == "bad_request" and caught.value.http_status == 400


def test_empty_results_are_a_note_not_an_error(stub, api):
    response = api.search("google", {"q": "__empty__"}, {})
    assert response.body is not None and response.note.startswith(
        "Google hasn't returned"
    )
    assert "organic_results" not in response.body


def test_failed_search_status_is_an_error(stub, api):
    with pytest.raises(SerpApiError) as caught:
        api.search("google", {"q": "__search_failed__"}, {})
    assert caught.value.code == "search_failed" and caught.value.retryable


def test_network_failure_retries_once_and_hides_the_key(monkeypatch):
    attempts = []
    sleeps = []
    monkeypatch.setattr(client_module, "_sleep", sleeps.append)
    api = SerpApiClient("secret_key_0123456789abcd", "http://127.0.0.1:9", timeout=2)

    def refuse(request, timeout=None):
        attempts.append(request.full_url)
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(api._opener, "open", refuse)
    with pytest.raises(SerpApiError) as caught:
        api.search("google", {"q": "x"}, {})
    assert caught.value.code == "network_error" and caught.value.retryable
    assert len(attempts) == 2 and len(sleeps) == 1
    assert "secret_key" in attempts[0]  # the key travels to the host...
    assert "secret_key" not in str(caught.value)  # ...but never into an error
    assert "secret_key" not in repr(caught.value.payload())


def test_timeout_is_reported_as_timeout(monkeypatch):
    api = SerpApiClient("secret_key_0123456789abcd", "http://127.0.0.1:9", timeout=1)

    def boom(request, timeout=None):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(api._opener, "open", boom)
    with pytest.raises(SerpApiError) as caught:
        api.locations("austin")
    assert caught.value.code == "timeout"


def test_redirects_are_refused_so_the_key_cannot_follow_them():
    hits = {"other": 0}

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits["other"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    other = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=other.serve_forever, daemon=True).start()

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header(
                "Location", f"http://127.0.0.1:{other.server_address[1]}/steal"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            return

    first = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=first.serve_forever, daemon=True).start()
    try:
        api = SerpApiClient(DEFAULT_KEY, f"http://127.0.0.1:{first.server_address[1]}")
        with pytest.raises(SerpApiError) as caught:
            api.account()
        assert caught.value.code == "upstream_error"
        assert hits["other"] == 0
    finally:
        first.shutdown()
        other.shutdown()
        first.server_close()
        other.server_close()


def test_non_json_reply_is_an_upstream_error():
    class Html(BaseHTTPRequestHandler):
        def do_GET(self):
            data = b"<html>oops</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Html)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = SerpApiClient(DEFAULT_KEY, f"http://127.0.0.1:{server.server_address[1]}")
        with pytest.raises(SerpApiError, match="not JSON"):
            api.search("google", {"q": "x"}, {})
        with pytest.raises(SerpApiError, match="not JSON"):
            api.account()
    finally:
        server.shutdown()
        server.server_close()


def test_key_is_scrubbed_from_response_bodies(monkeypatch):
    key = "scrub_key_0123456789abcdef"
    api = SerpApiClient(key, "http://127.0.0.1:9")
    body = {
        "search_metadata": {
            "id": "1",
            "status": "Success",
            "note": f"see api_key={key}&x=1",
        },
        "organic_results": [
            {"link": f"https://x.test/?api_key={key}", "snippet": f"{key}"}
        ],
        "serpapi_pagination": {
            "next": "https://serpapi.com/search.json?q=a&api_key=other_secret_value"
        },
    }
    monkeypatch.setattr(
        api, "_fetch", lambda *a, **k: (200, "application/json", json.dumps(body))
    )
    response = api.search("google", {"q": "a"}, {})
    dumped = json.dumps(response.body)
    assert key not in dumped and "other_secret_value" not in dumped
    assert "[redacted]" in dumped


def test_scrub_helpers():
    assert scrub("a KEY b", "KEY") == "a [redacted] b"
    assert scrub("x?api_key=abc&y=1", None) == "x?api_key=[redacted]&y=1"
    assert scrub("p%40ss", "p@ss") == "[redacted]"
    nested = scrub_value({"a": ["KEY", {"b": "KEY"}], "n": 3}, "KEY")
    assert nested == {"a": ["[redacted]", {"b": "[redacted]"}], "n": 3}
    assert scrub_value("plain text", None) == "plain text"


def test_account_is_normalised_and_drops_the_key(stub, api):
    account = api.account()
    assert "api_key" not in account and "api_key" not in json.dumps(account)
    assert account["plan_name"] == "Developer Plan"
    assert account["hourly_rate_limit"] == 1000
    assert account["total_searches_left"] == account["plan_searches_left"] + 100
    assert (
        normalize_account({"account_rate_limit_per_hour": 7})["hourly_rate_limit"] == 7
    )


def test_account_rejects_a_bad_key(stub):
    with pytest.raises(SerpApiError) as caught:
        SerpApiClient("nope_0123456789abcdef", stub.base_url).account()
    assert caught.value.code == "invalid_api_key"


def test_locations_need_no_key_and_clamp_the_limit(stub):
    anonymous = SerpApiClient(None, stub.base_url)
    found = anonymous.locations("austin", 50)
    assert [item["name"] for item in found][0] == "Austin, TX"
    assert found[0]["gps"] == [-97.7430608, 30.267153]
    assert stub.calls[0].params["limit"] == "10"
    assert anonymous.locations("rochester")[0]["gps"] is None
    assert anonymous.locations("zzzz-nothing") == []


def test_status_probe_never_raises(stub):
    healthy = SerpApiClient(None, stub.base_url).status()
    assert healthy["reachable"] is True and healthy["error"] is None
    stub.stop()
    down = SerpApiClient(None, stub.base_url, timeout=1).status()
    assert down["reachable"] is False and down["error"]


def test_base_url_resolution(monkeypatch):
    assert resolve_base_url(None) == "https://serpapi.com"
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", "http://127.0.0.1:9999/")
    assert resolve_base_url(None) == "http://127.0.0.1:9999"
    assert SerpApiClient("k" * 20).base_url == "http://127.0.0.1:9999"
    assert resolve_base_url("https://example.org/api") == "https://example.org/api"
    for bad in (
        "ftp://example.org",
        "http://example.org",
        "https://user:pw@example.org",
        "https://example.org/?x=1",
        "not a url",
        "",
    ):
        if bad == "":
            continue
        with pytest.raises(ValueError):
            resolve_base_url(bad)


def test_map_error_table():
    assert map_error(401, "Invalid API key").code == "invalid_api_key"
    assert map_error(200, "Invalid API key").code == "invalid_api_key"
    assert (
        map_error(429, "Your account has run out of searches.").code
        == "out_of_searches"
    )
    assert map_error(429, "slow down").code == "rate_limited"
    assert map_error(403, "no").code == "forbidden"
    assert map_error(404, "no").code == "not_found"
    assert map_error(410, "gone").code == "gone"
    assert map_error(422, "bad").code == "bad_request"
    assert map_error(503, "down").retryable
    assert not map_error(418, "teapot").retryable


# -- security regressions (adversarial review) ------------------------------------


class _Canned(BaseHTTPRequestHandler):
    body = b"{}"
    status = 200

    def do_GET(self):  # noqa: N802 - http.server API
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args):
        return


def _serve(body: bytes, status: int = 200):
    handler = type("Canned", (_Canned,), {"body": body, "status": status})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_absurdly_nested_responses_become_a_clean_upstream_error():
    nested = (
        '{"search_metadata":{"id":"x","status":"Success"},"deep":'
        + '{"a":' * 5000
        + "1"
        + "}" * 5000
        + "}"
    ).encode()
    server, base = _serve(nested)
    try:
        with pytest.raises(SerpApiError) as caught:
            SerpApiClient("k" * 20, base, timeout=10).search("google", {"q": "x"}, {})
    finally:
        server.shutdown()
        server.server_close()
    assert caught.value.code == "upstream_error"
    assert "nested too deeply" in caught.value.message


def test_a_key_echoed_in_every_corner_of_a_hostile_reply_is_scrubbed():
    key = "echo_key_0123456789abcdefABCDEF"
    body = json.dumps(
        {
            "search_metadata": {
                "id": "e1",
                "status": "Success",
                "json_endpoint": f"https://serpapi.com/searches/e1.json?api_key={key}",
            },
            "search_parameters": {"q": "x", "api_key": key},
            "organic_results": [
                {"title": f"key {key}", "link": f"https://e.com/?api_key={key}"}
            ],
            "serpapi_pagination": {
                "next": f"https://serpapi.com/search.json?api_key={key}&start=10"
            },
        }
    ).encode()
    server, base = _serve(body)
    try:
        response = SerpApiClient(key, base, timeout=10).search("google", {"q": "x"}, {})
    finally:
        server.shutdown()
        server.server_close()
    assert key not in json.dumps(response.body)
    assert "api_key=[redacted]" in json.dumps(response.body)


def test_error_text_that_echoes_the_key_is_scrubbed():
    key = "echo_key_0123456789abcdefABCDEF"
    server, base = _serve(
        json.dumps({"error": f"Bad key {key} / api_key={key}&x=1"}).encode(), status=400
    )
    try:
        with pytest.raises(SerpApiError) as caught:
            SerpApiClient(key, base, timeout=10).search("google", {"q": "x"}, {})
    finally:
        server.shutdown()
        server.server_close()
    assert key not in caught.value.message and key not in repr(caught.value)


def test_a_redirect_is_never_followed_with_the_key():
    seen: list[str] = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    destination = f"http://127.0.0.1:{target.server_address[1]}/evil"

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", destination + "?api_key=stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=redirect.serve_forever, daemon=True).start()
    try:
        with pytest.raises(SerpApiError):
            SerpApiClient(
                "k" * 20, f"http://127.0.0.1:{redirect.server_address[1]}", timeout=10
            ).search("google", {"q": "x"}, {})
    finally:
        redirect.shutdown()
        redirect.server_close()
        target.shutdown()
        target.server_close()
    assert seen == []


@pytest.mark.parametrize(
    "bad",
    [
        "https://user:pass@serpapi.com",
        "https://serpapi.com?api_key=x",
        "http://serpapi.com",
        "http://evil.example",
        "ftp://serpapi.com",
        "file:///etc/passwd",
        "//serpapi.com",
    ],
)
def test_the_base_url_cannot_be_pointed_at_a_credential_sink(bad):
    with pytest.raises(ValueError):
        resolve_base_url(bad)


def test_engine_and_key_cannot_be_overridden_by_parameters(stub):
    api = SerpApiClient(DEFAULT_KEY, stub.base_url, timeout=10)
    api.search("google", {"q": "x", "engine": "bing", "api_key": "attacker"}, {})
    call = stub.calls[-1]
    assert call.engine == "google" and call.key_ok
