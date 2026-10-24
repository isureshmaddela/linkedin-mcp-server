"""Route handlers for the Aira API console (``/api/airaapi/*``) and ``/v1/*``.

Handlers are thin: they open the user's database, resolve the SerpApi key and
hand everything else to :mod:`aira.airaapi.service` and
:mod:`aira.airaapi.compat`. The SerpApi key never appears in a response, log
line or stored record; responses only say whether it is configured, where it
came from and its last four characters.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import OrderedDict, deque
from datetime import date, datetime, timezone
from typing import Any

from aira import __version__ as AIRA_VERSION
from aira.airaapi import catalog, compat, service, store
from aira.airaapi.client import SerpApiClient, SerpApiError
from aira.airaapi.http import Raw, Request, aira_route
from aira.audit import notify
from aira.observability import metrics

NOT_CONFIGURED = service.NOT_CONFIGURED_MESSAGE


def make_client(api_key: str | None) -> SerpApiClient:
    """Build the SerpApi client. Tests replace this to inject a fake."""
    return SerpApiClient(api_key)


def _now() -> datetime:
    return store.now_utc()


def _limits(request: Request) -> service.Limits:
    return service.load_limits(request.setting)


def _resolved(request: Request, connection: sqlite3.Connection) -> service.ResolvedKey:
    return service.resolve_key(connection, request.secret_store)


def _client(
    request: Request, connection: sqlite3.Connection, *, required: bool = True
) -> SerpApiClient | None:
    resolved = _resolved(request, connection)
    if not resolved.configured:
        if required:
            raise SerpApiError("not_configured", resolved.error or NOT_CONFIGURED)
        return None
    return make_client(resolved.key)


def _bound(text: str, *, end: bool) -> str:
    """Turn a date or ISO timestamp from a query string into a stored-format bound."""
    if not text:
        return ""
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            moment = datetime(
                day.year,
                day.month,
                day.day,
                23 if end else 0,
                59 if end else 0,
                59 if end else 0,
                tzinfo=timezone.utc,
            )
        else:
            moment = store.from_iso(text)
    except ValueError:
        raise ValueError(
            "Dates must look like 2026-10-22 or 2026-10-22T14:30:00Z."
        ) from None
    return store.to_iso(moment)


def _body(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return value


# ---------------------------------------------------------------------------
# overview, account, key, status
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/airaapi/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    now = _now()
    with request.connect() as connection:
        resolved = _resolved(request, connection)
        data = service.overview(connection, now)
        account = None
        account_error = None
        if resolved.configured:
            try:
                account = service.cached_account(
                    connection, make_client(resolved.key), resolved.fingerprint(), now
                )
            except SerpApiError as failure:
                account_error = failure.message
    return 200, {
        "configured": resolved.configured,
        "key": resolved.public(),
        "key_error": resolved.error,
        "account": account,
        "account_error": account_error,
        **data,
    }


@aira_route("GET", r"/api/airaapi/account")
def account(request: Request) -> tuple[int, dict[str, Any]]:
    now = _now()
    with request.connect() as connection:
        resolved = _resolved(request, connection)
        if not resolved.configured:
            raise SerpApiError("not_configured", resolved.error or NOT_CONFIGURED)
        return 200, service.cached_account(
            connection,
            make_client(resolved.key),
            resolved.fingerprint(),
            now,
            force=request.q("refresh") == "1",
        )


@aira_route("POST", r"/api/airaapi/key")
def set_key(request: Request) -> tuple[int, dict[str, Any]]:
    candidate = request.payload.get("api_key")
    if not isinstance(candidate, str):
        raise ValueError("Send the SerpApi key as api_key.")
    now = _now()
    with request.connect() as connection:
        account_info = service.set_app_key(
            connection, request.secret_store, make_client, candidate, now
        )
        resolved = _resolved(request, connection)
    request.audit(
        "airaapi.key.set", "airaapi_key", "serpapi", {"last4": resolved.last4}
    )
    return 200, {
        "configured": True,
        "source": resolved.source,
        "last4": resolved.last4,
        "account": account_info,
    }


@aira_route("DELETE", r"/api/airaapi/key")
def remove_key(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        removed = service.remove_app_key(connection, request.secret_store)
        resolved = _resolved(request, connection)
    if not removed:
        if resolved.source == "env":
            raise SerpApiError(
                "conflict",
                "The key comes from SERPAPI_API_KEY in the server environment. "
                "Remove it from .env and restart Aira.",
                http_status=409,
            )
        raise LookupError("There is no stored SerpApi key to remove.")
    request.audit("airaapi.key.remove", "airaapi_key", "serpapi")
    return 200, {
        "configured": resolved.configured,
        "source": resolved.source,
        "last4": resolved.last4,
    }


@aira_route("GET", r"/api/airaapi/status")
def status(request: Request) -> tuple[int, dict[str, Any]]:
    now = _now()
    limits = _limits(request)
    with request.connect() as connection:
        db_ok = True
        try:
            connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            db_ok = False
        cached = (
            None
            if request.q("refresh") == "1"
            else store.cache_get(connection, "status:serpapi", now)
        )
        if isinstance(cached, dict):
            reachability = cached
        else:
            reachability = make_client(None).status()
            store.cache_set(
                connection,
                "status:serpapi",
                reachability,
                service.STATUS_CACHE_SECONDS,
                now,
            )
            connection.commit()
        throttle = service.throttle_state(connection, limits, now)
        queue = service.queue_state(connection)
    return 200, {
        "local": {
            "version": AIRA_VERSION,
            "uptime_s": int(now.timestamp() - metrics.started_at),
            "db_ok": db_ok,
        },
        "serpapi": reachability,
        "throttle": throttle,
        "queue": queue,
    }


# ---------------------------------------------------------------------------
# engines
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/airaapi/engines")
def engines(request: Request) -> tuple[int, dict[str, Any]]:
    return 200, service.engines_listing(
        category=request.q("category"), family=request.q("family"), text=request.q("q")
    )


@aira_route("GET", r"/api/airaapi/engines/([a-z][a-z0-9_]{0,63})")
def engine_detail(request: Request) -> tuple[int, dict[str, Any]]:
    found = catalog.engine(request.match.group(1))
    return 200, {
        **found.to_dict(),
        "common_parameters": catalog.common_parameters(),
        "examples": compat.engine_examples(found.id, request.base_url()),
    }


# ---------------------------------------------------------------------------
# search and history
# ---------------------------------------------------------------------------


@aira_route("POST", r"/api/airaapi/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    engine_id = str(payload.get("engine", "")).strip()
    if not engine_id:
        raise ValueError("Choose an engine.")
    params = _body(payload, "params")
    options = _body(payload, "options")
    save_as = str(payload.get("save_as") or "").strip()
    now = _now()
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        record = service.run_search(
            connection,
            client,
            engine_id,
            params,
            options=options,
            source="playground",
            label=str(payload.get("label") or ""),
            now=now,
            limits=_limits(request),
        )
        if save_as:
            try:
                record["saved"] = service.create_saved(
                    connection,
                    {"name": save_as, "engine": engine_id, "params": record["params"]},
                    now=now,
                )
            except ValueError as failure:
                record["save_error"] = str(failure)
    return 200, record


@aira_route("GET", r"/api/airaapi/searches")
def searches(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, service.list_searches(
            connection,
            engine=request.q("engine"),
            status=request.q("status"),
            source=request.q("source"),
            text=request.q("q"),
            pinned=request.qbool("pinned"),
            cache_hit=request.qbool("cache"),
            in_results=request.qbool("in_results") is True,
            date_from=_bound(request.q("from"), end=False),
            date_to=_bound(request.q("to"), end=True),
            limit=request.qint("limit", 25, 1, 200),
            offset=request.qint("offset", 0, 0, 1_000_000),
        )


@aira_route("DELETE", r"/api/airaapi/searches")
def delete_searches(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        outcome = service.bulk_delete(
            connection,
            before=request.q("before"),
            engine=request.q("engine"),
            everything=request.qbool("all") is True,
            include_pinned=request.qbool("include_pinned") is True,
        )
    request.audit("airaapi.searches.delete", "airaapi_searches", "", outcome)
    return 200, outcome


@aira_route("GET", r"/api/airaapi/searches/([0-9a-f]{32})")
def search_detail(request: Request) -> tuple[int, dict[str, Any]]:
    search_id = request.match.group(1)
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        return 200, service.get_search(
            connection,
            search_id,
            include_html=request.qbool("html") is True,
            client=client,
            limits=_limits(request),
        )


@aira_route("PATCH", r"/api/airaapi/searches/([0-9a-f]{32})")
def search_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, service.update_search_meta(
            connection, request.match.group(1), request.payload
        )


@aira_route("DELETE", r"/api/airaapi/searches/([0-9a-f]{32})")
def search_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        service.delete_search(connection, request.match.group(1))
    return 200, {"ok": True}


@aira_route("POST", r"/api/airaapi/searches/([0-9a-f]{32})/rerun")
def search_rerun(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        return 200, service.rerun(
            connection,
            client,
            request.match.group(1),
            _body(request.payload, "options"),
            limits=_limits(request),
        )


@aira_route("GET", r"/api/airaapi/searches/([0-9a-f]{32})/export")
def search_export(request: Request) -> Raw:
    with request.connect() as connection:
        body, content_type, filename = service.export_search(
            connection, request.match.group(1), request.q("format", "json")
        )
    return Raw(200, body, content_type, filename)


@aira_route("GET", r"/api/airaapi/searches/([0-9a-f]{32})/xray")
def search_xray(request: Request) -> tuple[int, dict[str, Any]]:
    """Pixel positions of an archived Google search, mapped to JSON paths (free archive call)."""
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        return 200, service.xray(
            connection,
            client,
            request.match.group(1),
            refresh=request.qbool("refresh") is True,
            limits=_limits(request),
        )


@aira_route("GET", r"/api/airaapi/diff")
def diff(request: Request) -> tuple[int, dict[str, Any]]:
    first, second = request.q("a"), request.q("b")
    if not first or not second:
        raise ValueError("Pass the two searches to compare as a and b.")
    with request.connect() as connection:
        return 200, service.diff_searches(connection, first, second)


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/airaapi/locations")
def locations(request: Request) -> tuple[int, dict[str, Any]]:
    now = _now()
    limit = request.qint("limit", 10, 1, 10)
    with request.connect() as connection:
        resolved = _resolved(request, connection)
        found = service.cached_locations(
            connection, make_client(resolved.key), request.q("q"), limit, now
        )
    return 200, {"locations": found}


# ---------------------------------------------------------------------------
# saved searches and monitors
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/airaapi/saved")
def saved_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, {"saved": service.list_saved(connection)}


@aira_route("POST", r"/api/airaapi/saved")
def saved_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 201, service.create_saved(connection, request.payload)


@aira_route("PATCH", r"/api/airaapi/saved/([0-9a-f]{32})")
def saved_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, service.update_saved(
            connection, request.match.group(1), request.payload
        )


@aira_route("DELETE", r"/api/airaapi/saved/([0-9a-f]{32})")
def saved_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        service.delete_saved(connection, request.match.group(1))
    return 200, {"ok": True}


@aira_route("POST", r"/api/airaapi/saved/([0-9a-f]{32})/run")
def saved_run(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        return 200, service.run_saved(
            connection,
            client,
            request.match.group(1),
            options=_body(request.payload, "options"),
            limits=_limits(request),
        )


@aira_route("GET", r"/api/airaapi/monitors")
def monitors_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        items = service.list_monitors(connection)
    return 200, {
        "monitors": items,
        "limits": {
            "min_interval_minutes": service.MIN_MONITOR_INTERVAL,
            "max_monitors": service.MAX_MONITORS,
        },
    }


@aira_route("POST", r"/api/airaapi/monitors")
def monitors_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 201, service.create_monitor(connection, request.payload)


@aira_route("PATCH", r"/api/airaapi/monitors/([0-9a-f]{32})")
def monitors_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, service.update_monitor(
            connection, request.match.group(1), request.payload
        )


@aira_route("DELETE", r"/api/airaapi/monitors/([0-9a-f]{32})")
def monitors_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        service.delete_monitor(connection, request.match.group(1))
    return 200, {"ok": True}


@aira_route("POST", r"/api/airaapi/monitors/([0-9a-f]{32})/run")
def monitors_run(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        outcome = service.run_monitor(
            connection,
            client,
            request.match.group(1),
            notify=compat.make_notifier(request.connect, notify),
            limits=_limits(request),
        )
    return 200, outcome


# ---------------------------------------------------------------------------
# Aira API keys and docs
# ---------------------------------------------------------------------------


@aira_route("GET", r"/api/airaapi/keys")
def keys_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, {"keys": compat.list_keys(connection, _now())}


@aira_route("POST", r"/api/airaapi/keys")
def keys_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        created = compat.create_key(
            connection,
            request.key_index,
            str(request.payload.get("name", "")),
            request.payload.get("hourly_limit"),
            _now(),
        )
    request.audit(
        "airaapi.apikey.create",
        "airaapi_api_key",
        created["key"]["id"],
        {"name": created["key"]["name"]},
    )
    return 201, created


@aira_route("DELETE", r"/api/airaapi/keys/([0-9a-f]{32})")
def keys_revoke(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        revoked = compat.revoke_key(
            connection, request.key_index, request.match.group(1), _now()
        )
    request.audit("airaapi.apikey.revoke", "airaapi_api_key", revoked["id"])
    return 200, {"ok": True, "key": revoked}


@aira_route("GET", r"/api/airaapi/docs")
def docs(request: Request) -> tuple[int, dict[str, Any]]:
    return 200, compat.docs_payload(request.base_url())


PLATFORM_SECTIONS = (
    "dashboard_pages",
    "plans",
    "billing_rules",
    "sdks",
    "metadata_fields",
    "account_api",
    "locations_api",
    "search_archive_api",
    "status_api",
    "async_and_webhooks",
    "ui_ideas",
)


@aira_route("GET", r"/api/airaapi/platform")
def platform_reference(request: Request) -> tuple[int, dict[str, Any]]:
    """The platform reference the Docs and Account pages show: plans, billing rules, SDKs, metadata fields."""
    data = catalog.platform()
    return 200, {name: data[name] for name in PLATFORM_SECTIONS if name in data}


# ---------------------------------------------------------------------------
# SerpApi-compatible /v1 (authenticated by an Aira API key)
# ---------------------------------------------------------------------------


def _api_key(request: Request) -> dict[str, Any]:
    if request.api_key is None:
        raise SerpApiError("invalid_aira_key", compat.INVALID_KEY_MESSAGE, 401)
    return dict(request.api_key)


# SerpApi's own paths (/search.json, /account.json, ...) work at the root as well
# as under /v1, so an SDK or script only needs its base URL pointed at Aira.
@aira_route("GET", r"(?:/v1)?/search(?:\.(json|html))?", auth="api_key")
def v1_search(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    key = _api_key(request)
    query = request.query
    if request.match.group(1) == "html":
        query = {**query, "output": ["html"]}
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        return compat.v1_search(
            connection,
            client,
            query,
            key,
            limits=_limits(request),
            now=_now(),
            base_url=request.base_url(),
        )


@aira_route("GET", r"(?:/v1)?/account(?:\.json)?", auth="api_key")
def v1_account(request: Request) -> tuple[int, dict[str, Any]]:
    _api_key(request)
    now = _now()
    with request.connect() as connection:
        resolved = _resolved(request, connection)
        if not resolved.configured:
            raise SerpApiError("not_configured", resolved.error or NOT_CONFIGURED)
        info = service.cached_account(
            connection, make_client(resolved.key), resolved.fingerprint(), now
        )
    return 200, compat.v1_account(info)


# The keyless Locations endpoint forwards to SerpApi's free Locations API from
# this machine's address. Anything on the machine, or any web page that can fire
# a GET at 127.0.0.1, could use it as a relay, so it is cached and rate limited.
KEYLESS_LOCATIONS_TTL = 3600
KEYLESS_LOCATIONS_CACHE_MAX = 256
KEYLESS_LOCATIONS_PER_MINUTE = 30
_keyless_cache: OrderedDict[tuple[str, int], tuple[float, list[Any]]] = OrderedDict()
_keyless_hits: dict[str, deque[float]] = {}
_keyless_lock = threading.Lock()


def _keyless_locations(client_id: str, text: str, limit: int) -> list[Any]:
    now = time.monotonic()
    token = (text.lower(), limit)
    with _keyless_lock:
        found = _keyless_cache.get(token)
        if found is not None and found[0] > now:
            _keyless_cache.move_to_end(token)
            return list(found[1])
        hits = _keyless_hits.setdefault(client_id, deque())
        while hits and hits[0] <= now - 60:
            hits.popleft()
        if len(hits) >= KEYLESS_LOCATIONS_PER_MINUTE:
            raise SerpApiError(
                "rate_limited",
                "Too many location lookups without an Aira API key. Wait a minute or send one.",
                429,
            )
        hits.append(now)
        if len(_keyless_hits) > 1024:
            for stale in [name for name, seen in _keyless_hits.items() if not seen]:
                del _keyless_hits[stale]
    result = list(make_client(None).locations(text, limit))
    with _keyless_lock:
        _keyless_cache[token] = (now + KEYLESS_LOCATIONS_TTL, result)
        _keyless_cache.move_to_end(token)
        while len(_keyless_cache) > KEYLESS_LOCATIONS_CACHE_MAX:
            _keyless_cache.popitem(last=False)
    return list(result)


@aira_route("GET", r"(?:/v1)?/locations(?:\.json)?", auth="api_key_optional")
def v1_locations(request: Request) -> tuple[int, list[Any]]:
    """Like SerpApi's Locations API, no key is needed; a presented key is still checked."""
    limit = request.qint("limit", 10, 1, 10)
    if request.api_key is None:
        text = request.q("q").strip()
        if len(text) < 2:
            raise ValueError("Type at least 2 characters to search locations.")
        return 200, _keyless_locations(request.client, text[:120], limit)
    with request.connect() as connection:
        resolved = _resolved(request, connection)
        found = service.cached_locations(
            connection,
            make_client(resolved.key),
            request.q("q"),
            limit,
            _now(),
        )
    return 200, found


@aira_route("GET", r"/v1/searches\.json", auth="api_key")
def v1_searches(request: Request) -> tuple[int, dict[str, Any]]:
    _api_key(request)
    with request.connect() as connection:
        return 200, service.list_searches(
            connection,
            engine=request.q("engine"),
            status=request.q("status"),
            source=request.q("source"),
            limit=request.qint("limit", 25, 1, 200),
            offset=request.qint("offset", 0, 0, 1_000_000),
        )


@aira_route(
    "GET",
    r"(?:/v1)?/searches/([A-Za-z0-9_-]{1,80})(?:\.(json|html))?",
    auth="api_key",
)
def v1_search_detail(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    _api_key(request)
    fmt = request.match.group(2) or (
        "html" if request.q("output") == "html" else "json"
    )
    with request.connect() as connection:
        client = _client(request, connection, required=False)
        return compat.v1_search_by_id(
            connection,
            client,
            request.match.group(1),
            fmt,
            _now(),
            base_url=request.base_url(),
        )


@aira_route("GET", r"/v1/engines(?:\.json)?", auth="api_key")
def v1_engines(request: Request) -> tuple[int, dict[str, Any]]:
    _api_key(request)
    if request.qbool("full"):
        return 200, {"engines": [item.to_dict() for item in catalog.engines().values()]}
    return 200, {"engines": [item.summary() for item in catalog.engines().values()]}
