"""The SerpApi-compatible ``/v1`` API and generated documentation and code samples.

``/v1/search.json`` accepts SerpApi's query parameters, authenticates with an
API key (``api_key`` query parameter or ``Authorization: Bearer``), runs
the search through :func:`aira.airaapi.service.run_search` with the owner's
SerpApi key and answers with the SerpApi JSON body plus
``search_metadata.aira = {id, cache_hit}``. Aira keys are random 45-character
secrets; only their SHA-256 hash is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from aira.airaapi import catalog, service, store
from aira.airaapi.client import SerpApiError
from aira.airaapi.http import Headers, Raw

INVALID_KEY_MESSAGE = (
    "Invalid API key. Pass an API key as the api_key parameter "
    "(create one in API > Keys)."
)
KEY_PREFIX = "aira_"
KEY_BODY_LENGTH = 40
KEY_PATTERN = re.compile(r"^aira_[A-Za-z0-9_-]{40}\Z")
MAX_KEYS = 25
OPTION_PARAMS = ("output", "async", "no_cache", "zero_trace", "json_restrictor")
ENVELOPE_PARAMS = frozenset({"api_key", "engine", "allow_unknown", *OPTION_PARAMS})
AIRA_KEY_PLACEHOLDER = "YOUR_AIRA_API_KEY"
SERPAPI_KEY_PLACEHOLDER = "YOUR_SERPAPI_KEY"


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Return (plaintext, display prefix, sha256 hash). Show the plaintext once."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(30)[:KEY_BODY_LENGTH]
    return plaintext, plaintext[: len(KEY_PREFIX) + 4], hash_key(plaintext)


def extract_key(headers: Headers, query: Mapping[str, list[str]]) -> str | None:
    """The presented key: a Bearer token wins over the ``api_key`` query parameter."""
    header = str(headers.get("Authorization", "") or "").strip()
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return token
    values = query.get("api_key")
    return values[0].strip() if values and values[0].strip() else None


def key_view(
    row: sqlite3.Row | Mapping[str, Any], used_this_hour: int = 0
) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "name": data["name"],
        "prefix": data["prefix"],
        "created_at": data["created_at"],
        "last_used_at": data.get("last_used_at"),
        "revoked_at": data.get("revoked_at"),
        "hourly_limit": data.get("hourly_limit") or 0,
        "used_this_hour": used_this_hour,
    }


def create_key(
    connection: sqlite3.Connection,
    key_index: Any,
    name: str,
    hourly_limit: Any,
    now: datetime,
) -> dict[str, Any]:
    clean = str(name or "").strip()
    if not clean or len(clean) > 80:
        raise ValueError("Give the key a name of up to 80 characters.")
    try:
        limit = int(str(hourly_limit if hourly_limit not in (None, "") else 0).strip())
    except ValueError:
        raise ValueError(
            "The hourly limit must be a whole number (0 means unlimited)."
        ) from None
    if not 0 <= limit <= 1_000_000:
        raise ValueError("The hourly limit must be between 0 and 1,000,000.")
    active = [row for row in store.list_api_keys(connection) if not row["revoked_at"]]
    if len(active) >= MAX_KEYS:
        raise ValueError(
            f"You can have up to {MAX_KEYS} active keys; revoke one first."
        )
    plaintext, prefix, digest = generate_key()
    key_id = store.new_id()
    store.insert_api_key(
        connection,
        {
            "id": key_id,
            "name": clean,
            "prefix": prefix,
            "key_hash": digest,
            "created_at": store.to_iso(now),
            "hourly_limit": limit,
        },
    )
    connection.commit()
    key_index.add(digest)
    row = store.get_api_key(connection, key_id)
    assert row is not None
    return {"key": key_view(row), "secret": plaintext}


def revoke_key(
    connection: sqlite3.Connection, key_index: Any, key_id: str, now: datetime
) -> dict[str, Any]:
    row = store.get_api_key(connection, key_id)
    if row is None:
        raise LookupError("Key not found.")
    store.revoke_api_key(connection, key_id, store.to_iso(now))
    connection.commit()
    key_index.remove(row["key_hash"])
    updated = store.get_api_key(connection, key_id)
    assert updated is not None
    return key_view(updated)


def list_keys(connection: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    since = store.to_iso(now - timedelta(hours=1))
    return [
        key_view(row, store.count_live_since(connection, since, api_key_id=row["id"]))
        for row in store.list_api_keys(connection)
    ]


def authenticate_key(
    connection: sqlite3.Connection, plaintext: str | None, now: datetime
) -> sqlite3.Row:
    """Resolve a presented Aira key in the user's database or raise a 401 error."""
    if not plaintext or not KEY_PATTERN.match(plaintext):
        raise SerpApiError("invalid_aira_key", INVALID_KEY_MESSAGE, 401)
    digest = hash_key(plaintext)
    row = store.get_api_key_by_hash(connection, digest)
    if row is None or not hmac.compare_digest(str(row["key_hash"]), digest):
        raise SerpApiError("invalid_aira_key", INVALID_KEY_MESSAGE, 401)
    if row["revoked_at"]:
        raise SerpApiError("invalid_aira_key", "This API key has been revoked.", 401)
    store.touch_api_key(connection, row["id"], store.to_iso(now))
    connection.commit()
    return row


def enforce_hourly_limit(
    connection: sqlite3.Connection, key: Mapping[str, Any], now: datetime
) -> None:
    limit = int(key["hourly_limit"] or 0)
    if not limit:
        return
    used = store.count_live_since(
        connection, store.to_iso(now - timedelta(hours=1)), api_key_id=str(key["id"])
    )
    if used >= limit:
        raise SerpApiError(
            "rate_limited",
            f"This API key has used its {limit} searches for the hour. Try again later.",
            429,
        )


_key_inflight: dict[str, int] = {}
_key_inflight_lock = threading.Lock()


@contextmanager
def hourly_slot(
    connection: sqlite3.Connection, key: Mapping[str, Any], now: datetime
) -> Iterator[None]:
    """Reserve one call against the key's hourly limit for the duration of the request.

    ``enforce_hourly_limit`` alone counts finished calls, so N simultaneous
    requests can all see "under the limit" and run. Counting the calls still in
    flight closes that gap.
    """
    limit = int(key["hourly_limit"] or 0)
    if not limit:
        yield
        return
    key_id = str(key["id"])
    with _key_inflight_lock:
        used = store.count_live_since(
            connection, store.to_iso(now - timedelta(hours=1)), api_key_id=key_id
        )
        if used + _key_inflight.get(key_id, 0) >= limit:
            raise SerpApiError(
                "rate_limited",
                f"This API key has used its {limit} searches for the hour. Try again later.",
                429,
            )
        _key_inflight[key_id] = _key_inflight.get(key_id, 0) + 1
    try:
        yield
    finally:
        with _key_inflight_lock:
            remaining = _key_inflight.get(key_id, 1) - 1
            if remaining > 0:
                _key_inflight[key_id] = remaining
            else:
                _key_inflight.pop(key_id, None)


# ---------------------------------------------------------------------------
# /v1 handlers (logic only; routes.py binds them)
# ---------------------------------------------------------------------------


def split_query(
    query: Mapping[str, list[str]],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Split a SerpApi query string into (engine, engine params, run options)."""
    engine = (query.get("engine") or ["google"])[0].strip() or "google"
    options: dict[str, Any] = {}
    for name in OPTION_PARAMS:
        if name in query:
            options[name] = query[name][0].strip()
    if "allow_unknown" in query:
        options["allow_unknown"] = query["allow_unknown"][0].strip()
    params = {
        name: values[0]
        for name, values in query.items()
        if name not in ENVELOPE_PARAMS and values
    }
    return engine, params, options


def _rebase(url: str, base_url: str) -> str:
    """Point a SerpApi search link at this server, keeping its path and query."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not re.match(
        r"/search(\.json|\.html)?$", parts.path
    ):
        return url
    path = "/search.json" if parts.path == "/search" else parts.path
    return f"{base_url}{path}" + (f"?{parts.query}" if parts.query else "")


def _rebase_links(value: Any, base_url: str) -> Any:
    if isinstance(value, str):
        return _rebase(value, base_url)
    if isinstance(value, list):
        return [_rebase_links(item, base_url) for item in value]
    if isinstance(value, dict):
        return {key: _rebase_links(item, base_url) for key, item in value.items()}
    return value


def with_aira_metadata(record: Mapping[str, Any], base_url: str = "") -> dict[str, Any]:
    """The SerpApi body of ``record`` with ``search_metadata.aira`` added (stored copy untouched).

    With ``base_url`` the archive and pagination links are rewritten to this
    server so SDKs that follow ``json_endpoint`` or ``serpapi_pagination.next``
    keep talking to Aira (and are served from its archive and cache).
    """
    body = dict(record.get("result") or {})
    found = body.get("search_metadata")
    metadata = dict(found) if isinstance(found, Mapping) else {}
    metadata["aira"] = {"id": record["id"], "cache_hit": bool(record["cache_hit"])}
    if base_url:
        search_id = str(metadata.get("id") or record["id"])
        if "json_endpoint" in metadata:
            metadata["json_endpoint"] = f"{base_url}/searches/{search_id}.json"
        for name in ("raw_html_file", "prettify_html_file"):
            if name in metadata:
                metadata[name] = f"{base_url}/searches/{search_id}.html"
        if "serpapi_pagination" in body:
            body["serpapi_pagination"] = _rebase_links(
                body["serpapi_pagination"], base_url
            )
    body["search_metadata"] = metadata
    return body


def v1_search(
    connection: sqlite3.Connection,
    client: Any,
    query: Mapping[str, list[str]],
    key: Mapping[str, Any],
    *,
    limits: service.Limits,
    now: datetime,
    base_url: str = "",
) -> tuple[int, dict[str, Any]] | Raw:
    engine, params, options = split_query(query)
    # SerpApi ignores parameters it does not know, and its SDKs add some of
    # their own (source=python, ...). Stay as lenient unless the caller opts out.
    options.setdefault("allow_unknown", "true")
    with hourly_slot(connection, key, now):
        record = service.run_search(
            connection,
            client,
            engine,
            params,
            options=options,
            source="api",
            label=f"API key: {key['name']}",
            now=now,
            limits=limits,
            api_key_id=str(key["id"]),
        )
    if record["output"] != "json":
        kind = (
            "text/markdown; charset=utf-8"
            if record["output"] == "md"
            else "text/html; charset=utf-8"
        )
        return Raw(200, str(record.get("html") or "").encode("utf-8"), kind)
    if record.get("result") is None:
        raise RuntimeError(
            "The result exceeded the stored size limit and cannot be returned."
        )
    return 200, with_aira_metadata(record, base_url)


def v1_search_by_id(
    connection: sqlite3.Connection,
    client: Any,
    search_id: str,
    fmt: str,
    now: datetime,
    base_url: str = "",
) -> tuple[int, dict[str, Any]] | Raw:
    row = store.get_search_row(
        connection, search_id
    ) or store.get_search_row_by_serpapi(connection, search_id)
    if row is None:
        if store.is_gone(connection, search_id):
            raise SerpApiError("gone", "This search has expired from the archive.", 410)
        raise LookupError("Search not found.")
    record = service.get_search(
        connection, row["id"], include_html=fmt == "html", client=client, now=now
    )
    if fmt == "html":
        if not record.get("html"):
            raise LookupError("No HTML page is stored for this search.")
        return Raw(200, str(record["html"]).encode("utf-8"), "text/html; charset=utf-8")
    if record.get("result") is None:
        raise LookupError("This search has no stored JSON result.")
    return 200, with_aira_metadata(record, base_url)


def v1_account(account: Mapping[str, Any]) -> dict[str, Any]:
    """The owner's SerpApi account in the shape SerpApi's SDKs expect (no key)."""
    body = {name: value for name, value in account.items() if name != "fetched_at"}
    body["account_rate_limit_per_hour"] = body.get("hourly_rate_limit")
    return body


# ---------------------------------------------------------------------------
# Code samples
# ---------------------------------------------------------------------------


_SAMPLE_TEXT = {
    "q": "coffee",
    "query": "coffee",
    "search_query": "coffee",
    "text": "coffee",
    "p": "coffee",
    "k": "coffee",
    "term": "coffee",
    "_nkw": "laptop",
    "location": "Austin, Texas, United States",
    "url": "https://example.com",
    "gl": "us",
    "hl": "en",
    "place_id": "ChIJ_example_place_id",
    "product_id": "123456789",
    "asin": "B0EXAMPLE00",
    "v": "dQw4w9WgXcQ",
    "channel_id": "UC_example_channel_id",
    "departure_id": "AUS",
    "arrival_id": "JFK",
    "data_cid": "1234567890123456789",
    "profile_id": "example",
}
_HELP_EXAMPLE_RE = re.compile(r"e\.g\.?,?\s*[`'\"]?([^`'\",;()]{1,60})", re.IGNORECASE)


def _sample_value(spec: catalog.Param, today: date) -> str:
    if spec.name in _SAMPLE_TEXT:
        return _SAMPLE_TEXT[spec.name]
    if spec.default not in (None, ""):
        return (
            str(spec.default).lower()
            if isinstance(spec.default, bool)
            else str(spec.default)
        )
    if spec.type == "enum" and spec.options:
        return spec.options[0].value
    if spec.type == "integer":
        return "1"
    if spec.type == "number":
        return "1"
    if spec.type == "boolean":
        return "true"
    if spec.type == "date":
        offset = 45 if "out" in spec.name or "return" in spec.name else 30
        return (today + timedelta(days=offset)).isoformat()
    if spec.type == "csv" and spec.options:
        return spec.options[0].value
    found = _HELP_EXAMPLE_RE.search(spec.help)
    return found.group(1).strip() if found else "example"


def sample_params(engine_id: str, today: date | None = None) -> dict[str, str]:
    """Placeholder values for the parameters a first call to ``engine_id`` needs."""
    found = catalog.engine(engine_id)
    day = today or date.today()
    chosen: dict[str, str] = {}
    grouped = {name for group in found.requires_any_of for name in group}
    for spec in found.params:
        if spec.required and spec.name not in grouped:
            chosen[spec.name] = _sample_value(spec, day)
    for group in found.requires_any_of:
        if not any(name in chosen for name in group):
            spec = found.param(group[0])
            if spec is not None:
                chosen[spec.name] = _sample_value(spec, day)
    for spec in found.params:
        if spec.required and spec.name not in chosen:
            chosen[spec.name] = _sample_value(spec, day)
    if not chosen and found.params:
        # Nothing is mandatory (for example google_sports): show the first parameter.
        chosen[found.params[0].name] = _sample_value(found.params[0], day)
    return chosen


def _dq(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _sh(value: str) -> str:
    """POSIX single-quote ``value`` so the shell never expands ``$(...)``, backticks or ``!``."""
    return "'" + value.replace("'", "'\\''") + "'"


def _ruby(value: str) -> str:
    return _dq(value).replace("#", "\\#")


def _php(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def snippets(
    url: str, params: Mapping[str, str], key_placeholder: str
) -> dict[str, str]:
    """Runnable GET samples in eight languages for ``url`` with ``params`` and a key placeholder."""
    items = {**params, "api_key": key_placeholder}
    pad = " " * 12
    curl_parts = [f"curl -G {_sh(url)}"]
    for name, value in items.items():
        flag = "--data-urlencode" if name != "api_key" else "-d"
        curl_parts.append(f"  {flag} {_sh(f'{name}={value}')}")
    py_lines = "\n".join(f"        {_dq(n)}: {_dq(v)}," for n, v in items.items())
    js_lines = "\n".join(f"  {n}: {_dq(v)}," for n, v in items.items())
    rb_lines = ",\n".join(f"  {_ruby(n)} => {_ruby(v)}" for n, v in items.items())
    php_lines = "\n".join(f"    {_php(n)} => {_php(v)}," for n, v in items.items())
    go_lines = "\n".join(f"\tquery.Set({_dq(n)}, {_dq(v)})" for n, v in items.items())
    java_lines = ",\n".join(
        f"{pad}Map.entry({_dq(n)}, {_dq(v)})" for n, v in items.items()
    )
    cs_lines = "\n".join(f"    [{_dq(n)}] = {_dq(v)}," for n, v in items.items())
    return {
        "curl": " \\\n".join(curl_parts),
        "python": (
            "import requests\n\n"
            "response = requests.get(\n"
            f"    {_dq(url)},\n"
            "    params={\n"
            f"{py_lines}\n"
            "    },\n"
            "    timeout=60,\n"
            ")\n"
            "response.raise_for_status()\n"
            "print(response.json())\n"
        ),
        "javascript": (
            "const params = new URLSearchParams({\n"
            f"{js_lines}\n"
            "});\n"
            f"const response = await fetch(`{url}?${{params}}`);\n"
            "if (!response.ok) throw new Error(await response.text());\n"
            "console.log(await response.json());\n"
        ),
        "ruby": (
            'require "net/http"\nrequire "json"\n\n'
            f"uri = URI({_ruby(url)})\n"
            "uri.query = URI.encode_www_form(\n"
            f"{rb_lines}\n"
            ")\n"
            "puts JSON.parse(Net::HTTP.get(uri))\n"
        ),
        "php": (
            "<?php\n"
            "$query = http_build_query([\n"
            f"{php_lines}\n"
            "]);\n"
            f"$response = file_get_contents({_php(url)} . '?' . $query);\n"
            "print_r(json_decode($response, true));\n"
        ),
        "go": (
            "package main\n\n"
            'import (\n\t"fmt"\n\t"io"\n\t"net/http"\n\t"net/url"\n)\n\n'
            "func main() {\n"
            "\tquery := url.Values{}\n"
            f"{go_lines}\n"
            f'\tresponse, err := http.Get({_dq(url)} + "?" + query.Encode())\n'
            "\tif err != nil {\n\t\tpanic(err)\n\t}\n"
            "\tdefer response.Body.Close()\n"
            "\tbody, _ := io.ReadAll(response.Body)\n"
            "\tfmt.Println(string(body))\n"
            "}\n"
        ),
        "java": (
            "import java.net.URI;\n"
            "import java.net.URLEncoder;\n"
            "import java.net.http.HttpClient;\n"
            "import java.net.http.HttpRequest;\n"
            "import java.net.http.HttpResponse;\n"
            "import java.nio.charset.StandardCharsets;\n"
            "import java.util.Map;\n"
            "import java.util.stream.Collectors;\n\n"
            "public class Search {\n"
            "    public static void main(String[] args) throws Exception {\n"
            "        Map<String, String> params = Map.ofEntries(\n"
            f"{java_lines}\n"
            "        );\n"
            "        String query = params.entrySet().stream()\n"
            '            .map(e -> e.getKey() + "=" + URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8))\n'
            '            .collect(Collectors.joining("&"));\n'
            f'        HttpRequest request = HttpRequest.newBuilder(URI.create({_dq(url)} + "?" + query)).build();\n'
            "        HttpResponse<String> response = HttpClient.newHttpClient()\n"
            "            .send(request, HttpResponse.BodyHandlers.ofString());\n"
            "        System.out.println(response.body());\n"
            "    }\n"
            "}\n"
        ),
        "csharp": (
            "using System;\n"
            "using System.Collections.Generic;\n"
            "using System.Net.Http;\n\n"
            "var parameters = new Dictionary<string, string>\n{\n"
            f"{cs_lines}\n"
            "};\n"
            "using var client = new HttpClient();\n"
            "var query = await new FormUrlEncodedContent(parameters).ReadAsStringAsync();\n"
            f'var body = await client.GetStringAsync({_dq(url)} + "?" + query);\n'
            "Console.WriteLine(body);\n"
        ),
    }


def engine_examples(
    engine_id: str, base_url: str, today: date | None = None
) -> dict[str, Any]:
    """Code samples for one engine against Aira's ``/v1`` and directly against SerpApi."""
    params = sample_params(engine_id, today)
    query = {"engine": engine_id, **params}
    aira_url = f"{base_url}/v1/search.json"
    serpapi_url = "https://serpapi.com/search.json"
    aira_samples = snippets(aira_url, query, AIRA_KEY_PLACEHOLDER)
    return {
        **aira_samples,
        "aira_v1": f"{aira_url}?{urlencode({**query, 'api_key': AIRA_KEY_PLACEHOLDER})}",
        "serpapi": {
            **snippets(serpapi_url, query, SERPAPI_KEY_PLACEHOLDER),
            "url": f"{serpapi_url}?{urlencode({**query, 'api_key': SERPAPI_KEY_PLACEHOLDER})}",
        },
        "params": params,
    }


# ---------------------------------------------------------------------------
# Generated API reference
# ---------------------------------------------------------------------------

_V1_ENDPOINTS = (
    {
        "method": "GET",
        "path": "/search.json",
        "alias": "/search, /search.html, /v1/search.json (same on /v1)",
        "description": "Run a search on any engine. Accepts SerpApi's parameters. Identical searches inside the cache window are answered locally and cost nothing.",
        "returns": "The SerpApi JSON body with search_metadata.aira = {id, cache_hit}. output=html or output=md returns the raw page.",
    },
    {
        "method": "GET",
        "path": "/account.json",
        "alias": "/v1/account.json",
        "description": "Plan, usage and hourly rate limit of the SerpApi account behind Aira. Never includes the SerpApi key.",
        "returns": "SerpApi account fields.",
    },
    {
        "method": "GET",
        "path": "/locations.json?q=austin&limit=5",
        "alias": "/v1/locations.json",
        "description": "Search Google locations for the location parameter. Free; needs no SerpApi key.",
        "returns": "A JSON array of locations.",
    },
    {
        "method": "GET",
        "path": "/v1/searches.json?engine=&status=&limit=25&offset=0",
        "description": "List searches stored in Aira (newest first).",
        "returns": "{items, total, limit, offset} without result bodies.",
    },
    {
        "method": "GET",
        "path": "/searches/{id}.json",
        "alias": "/searches/{id}.html, /v1/searches/{id}.json",
        "description": "Fetch a stored search by Aira id or SerpApi search id, as JSON or as the raw HTML page.",
        "returns": "The stored SerpApi JSON, or the page.",
    },
    {
        "method": "GET",
        "path": "/v1/engines.json",
        "description": "The engine catalog with parameters.",
        "returns": "{engines: [...]}",
    },
)
_AIRA_ERRORS = (
    {
        "http_status": 401,
        "code": "invalid_aira_key",
        "message": "Invalid API key. Pass an API key as the api_key parameter ... / This API key has been revoked.",
        "meaning": "The key is missing, unknown or revoked.",
        "advice": "Create a key under API > Keys and send it as api_key or a Bearer token.",
    },
    {
        "http_status": 400,
        "code": "bad_request",
        "message": "'q' is required. / Unknown parameter 'x' for google.",
        "meaning": "Parameters failed validation against the engine definition. Nothing was billed.",
        "advice": "Check the engine page for its parameters. Unknown parameters are forwarded like SerpApi does; add allow_unknown=false to reject them.",
    },
    {
        "http_status": 429,
        "code": "rate_limited",
        "message": "This API key has used its N searches for the hour.",
        "meaning": "The per-key hourly limit was reached.",
        "advice": "Wait for the hour to roll over or raise the limit on the key.",
    },
    {
        "http_status": 429,
        "code": "local_rate_limit",
        "message": "The local hourly limit of N live searches is reached.",
        "meaning": "Aira's own protection against spending the SerpApi quota was hit.",
        "advice": "Raise the limit in API settings.",
    },
    {
        "http_status": 409,
        "code": "not_configured",
        "message": "No SerpApi key is configured.",
        "meaning": "The account behind this Aira key has not connected a SerpApi key.",
        "advice": "Connect one under API > Account.",
    },
    {
        "http_status": 402,
        "code": "out_of_searches",
        "message": "Your account has run out of searches.",
        "meaning": "SerpApi reported no searches left.",
        "advice": "Add credits or renew on SerpApi; cached repeats still work.",
    },
)


def docs_payload(base_url: str) -> dict[str, Any]:
    """Everything the in-app API reference shows, generated from the catalog."""
    platform = catalog.platform()
    listed = platform.get("errors")
    upstream_errors: list[Any] = listed if isinstance(listed, list) else []
    generic = engine_examples("google", base_url)
    return {
        "base_url": f"{base_url}/v1",
        "root_url": base_url,
        "auth": {
            "summary": "Send an API key as the api_key query parameter or as 'Authorization: Bearer aira_...'.",
            "key_format": "aira_ followed by 40 URL-safe characters. Only a SHA-256 hash is stored; the secret is shown once.",
            "scope": "A key reaches only the account that created it and only the /v1 endpoints. It never exposes the SerpApi key.",
        },
        "endpoints": [dict(item) for item in _V1_ENDPOINTS],
        "parameters": catalog.common_parameters(),
        "aira_parameters": [
            {
                "name": "allow_unknown",
                "type": "boolean",
                "description": "Defaults to true: parameters that are not in the engine catalog are forwarded, as SerpApi does. Send allow_unknown=false to reject them.",
            },
        ],
        "errors": [dict(item) for item in _AIRA_ERRORS],
        "upstream_errors": [
            dict(item) for item in upstream_errors if isinstance(item, dict)
        ],
        "snippets": {
            key: value
            for key, value in generic.items()
            if key
            in {"curl", "python", "javascript", "ruby", "php", "go", "java", "csharp"}
        },
        "migrate": [
            "Replace https://serpapi.com with "
            + base_url
            + " in your code. SerpApi's own paths work (/search.json, /account.json, /locations.json, /searches/{id}.json) and so do the /v1 paths.",
            "Replace your SerpApi key with an API key from API > Keys.",
            "Nothing else changes: parameters, JSON bodies, pagination links and error shapes match SerpApi, and the official SDKs work unchanged.",
            "Identical searches within the cache window now cost no SerpApi searches and appear in Aira's history.",
        ],
        "sdk": {
            "python (serpapi)": (
                "import serpapi\n"
                f'serpapi.http.HTTPClient.BASE_DOMAIN = "{base_url}"\n'
                'client = serpapi.Client(api_key="YOUR_AIRA_API_KEY")\n'
                'results = client.search(engine="google_jobs", q="python developer")\n'
                'print(results["jobs_results"])'
            ),
            "python (google-search-results)": (
                "from serpapi import GoogleSearch\n"
                f'GoogleSearch.BACKEND = "{base_url}"\n'
                'search = GoogleSearch({"engine": "google", "q": "coffee", "api_key": "YOUR_AIRA_API_KEY"})'
                + "\n"
                "print(search.get_dict())"
            ),
            "curl": f'curl "{base_url}/search.json?engine=google&q=coffee&api_key=YOUR_AIRA_API_KEY"',
        },
        "categories": catalog.categories(),
    }


def make_notifier(
    connection_factory: Callable[[], Any], notify: Callable[..., Any]
) -> service.Notifier:
    """Adapt ``aira.audit.notify`` to the ``(title, body, link)`` callback monitors expect."""

    def send(title: str, body: str, link: str) -> None:
        with connection_factory() as connection:
            notify(connection, title=title, body=body, level="info", link=link)

    return send
