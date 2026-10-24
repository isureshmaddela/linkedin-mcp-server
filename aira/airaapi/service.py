"""Business logic for the Aira API console.

Pure functions over a user's SQLite connection plus a ``client`` argument that
looks like :class:`aira.airaapi.client.SerpApiClient`, so tests inject a fake or
the stand-in SerpApi server. Service functions commit their own writes: a
search can run for tens of seconds and must never keep a write transaction
open while the network call is in flight.

Search semantics (see docs/airaapi-spec.md section 3.3):

1. validate parameters against the catalog;
2. serve a fresh identical run from the local cache (0 credits, no SerpApi call);
3. enforce the local hourly limit and a per-process concurrency cap;
4. call SerpApi, store the record (errors included) and update usage rollups.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import shlex
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aira.airaapi import catalog, store, xlsx
from aira.airaapi.client import OUTPUTS, RawResponse, SerpApiError

logger = logging.getLogger("aira.airaapi.service")

SOURCES = ("playground", "careers", "api", "monitor", "intel", "rerun", "leads")
APP_KEY_SECRET = "serpapi_api_key"
ENV_KEY_NAME = "SERPAPI_API_KEY"
NOT_CONFIGURED_MESSAGE = (
    "No SerpApi key is configured. Connect one in Aira API > Account, "
    "or set SERPAPI_API_KEY in the server environment."
)
MAX_CONCURRENT_SEARCHES = 4
SLOT_WAIT_SECONDS = 30
MIN_MONITOR_INTERVAL = 15
MAX_MONITORS = 25
MAX_SAVED = 300
DIFF_CAP = 300
CSV_COLUMN_CAP = 60
ACCOUNT_CACHE_SECONDS = 60
LOCATION_CACHE_SECONDS = 24 * 3600
LOCATION_CACHE_MAX_ENTRIES = 2000
STATUS_CACHE_SECONDS = 30
PRUNE_INTERVAL_SECONDS = 600
OPTION_KEYS = frozenset(
    {
        "no_cache",
        "output",
        "async",
        "zero_trace",
        "json_restrictor",
        "allow_unknown",
        "use_cache",
    }
)
SETTING_BOUNDS: dict[str, tuple[int, int, int]] = {
    # name: (default, minimum, maximum)
    "airaapi_cache_ttl_minutes": (60, 0, 10_080),
    "airaapi_hourly_limit": (0, 0, 100_000),
    "airaapi_retention_days": (30, 1, 3650),
    "airaapi_max_records": (2000, 50, 100_000),
    "airaapi_result_cap_mb": (5, 1, 50),
}
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SECONDARY_KEYS = frozenset(
    {
        "related_searches",
        "related_questions",
        "refine_this_search",
        "refine_search_filters",
        "filters",
        "serpapi_pagination",
        "pagination",
        "search_information",
        "things_to_know",
        "perspectives",
        "airports",
        "price_insights",
        "menu_items",
    }
)
_PRIMARY_KEYS = (
    "organic_results",
    "jobs_results",
    "news_results",
    "shopping_results",
    "images_results",
    "video_results",
    "videos",
    "reviews",
    "local_results",
    "places",
    "properties",
    "best_flights",
    "other_flights",
    "articles",
    "apps",
    "results",
)
_VOLATILE_KEYS = frozenset(
    {"search_metadata", "search_parameters", "serpapi_pagination", "pagination"}
)
_IDENTITY_FIELDS = (
    "link",
    "url",
    "product_link",
    "product_id",
    "place_id",
    "data_id",
    "job_id",
    "video_id",
    "asin",
    "id",
    "title",
    "name",
    "query",
    "position",
)

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_SEARCHES)
_inflight = 0
_inflight_lock = threading.Lock()
FLIGHT_WAIT_SECONDS = 90


class _Flight:
    """One in-progress search: its lock and how many requests are waiting on it."""

    __slots__ = ("lock", "waiters")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiters = 0


_flights: dict[tuple[str, str], _Flight] = {}
_flights_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class Limits:
    """Per-user tuning read from settings (see ``SETTING_BOUNDS``)."""

    cache_ttl_minutes: int = 60
    hourly_limit: int = 0
    retention_days: int = 30
    max_records: int = 2000
    result_cap_mb: int = 5

    @property
    def result_cap_bytes(self) -> int:
        return self.result_cap_mb * 1024 * 1024


def parse_setting(name: str, value: Any) -> int:
    """Validate one ``airaapi_*`` setting; raises ``ValueError`` with a plain message."""
    default, low, high = SETTING_BOUNDS[name]
    label = name.replace("airaapi_", "").replace("_", " ")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"The {label} must be a whole number.") from None
    if not low <= number <= high:
        raise ValueError(f"The {label} must be between {low:,} and {high:,}.")
    return number


def load_limits(setting: Callable[[str], str]) -> Limits:
    """Build :class:`Limits` from a settings getter, falling back to defaults."""
    values: dict[str, int] = {}
    for name, (default, low, high) in SETTING_BOUNDS.items():
        try:
            number = int(str(setting(name)).strip())
        except (TypeError, ValueError):
            number = default
        values[name] = number if low <= number <= high else default
    return Limits(
        cache_ttl_minutes=values["airaapi_cache_ttl_minutes"],
        hourly_limit=values["airaapi_hourly_limit"],
        retention_days=values["airaapi_retention_days"],
        max_records=values["airaapi_max_records"],
        result_cap_mb=values["airaapi_result_cap_mb"],
    )


def inflight_searches() -> int:
    return _inflight


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    """The SerpApi key in effect. ``key`` is a secret; never put it in a response."""

    key: str | None = field(repr=False)  # a secret: never in repr(), logs or tracebacks
    source: str | None
    last4: str | None
    error: str | None = None

    @property
    def configured(self) -> bool:
        return self.key is not None

    def public(self) -> dict[str, Any]:
        return {"source": self.source, "last4": self.last4}

    def fingerprint(self) -> str:
        return hashlib.sha256((self.key or "").encode("utf-8")).hexdigest()[:16]


def validate_key_format(api_key: str) -> str:
    key = str(api_key or "").strip()
    if not _KEY_RE.match(key):
        raise ValueError(
            "That does not look like a SerpApi key: use 16 to 128 letters, digits, "
            "dashes or underscores, with no spaces."
        )
    return key


def resolve_key(
    connection: sqlite3.Connection,
    secret_store: store.SecretStore,
    environ: Mapping[str, str] | None = None,
) -> ResolvedKey:
    """The key to use: the account's own key wins over ``SERPAPI_API_KEY``."""
    from aira.hosting import HOOKS

    if HOOKS.serpapi_key is not None:
        # A hosted deployment resolves the organization's key (or the platform's pooled key).
        hosted_key, hosted_source = HOOKS.serpapi_key()
        if not hosted_key:
            return ResolvedKey(None, None, None)
        return ResolvedKey(
            hosted_key,
            hosted_source,
            hosted_key[-4:] if hosted_source == "app" else None,
        )
    error = None
    try:
        stored = secret_store.get(connection, APP_KEY_SECRET)
    except store.SecretError as failure:
        stored = None
        error = str(failure)
    if stored:
        return ResolvedKey(stored, "app", stored[-4:])
    env_value = str(
        (environ if environ is not None else os.environ).get(ENV_KEY_NAME, "")
    ).strip()
    if env_value:
        return ResolvedKey(env_value, "env", env_value[-4:], error)
    return ResolvedKey(None, None, None, error)


def set_app_key(
    connection: sqlite3.Connection,
    secret_store: store.SecretStore,
    client_factory: Callable[[str], Any],
    api_key: str,
    now: datetime,
) -> dict[str, Any]:
    """Validate ``api_key`` against account.json, then store it encrypted.

    The old key is untouched when validation fails.
    """
    key = validate_key_format(api_key)
    account = client_factory(key).account()
    secret_store.put(connection, APP_KEY_SECRET, key, now)
    store.cache_delete(connection, "account:")
    connection.commit()
    return account


def remove_app_key(
    connection: sqlite3.Connection, secret_store: store.SecretStore
) -> bool:
    removed = secret_store.delete(connection, APP_KEY_SECRET)
    store.cache_delete(connection, "account:")
    connection.commit()
    return removed


def cached_account(
    connection: sqlite3.Connection,
    client: Any,
    fingerprint: str,
    now: datetime,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Account details, refreshed at most once a minute. Never contains the key."""
    cache_key = f"account:{fingerprint}"
    if not force:
        cached = store.cache_get(connection, cache_key, now)
        if isinstance(cached, dict):
            return cached
    account = dict(client.account())
    account["fetched_at"] = store.to_iso(now)
    store.cache_set(connection, cache_key, account, ACCOUNT_CACHE_SECONDS, now)
    connection.commit()
    return account


def cached_locations(
    connection: sqlite3.Connection,
    client: Any,
    query: str,
    limit: int,
    now: datetime,
) -> list[dict[str, Any]]:
    text = query.strip()
    if len(text) < 2:
        raise ValueError("Type at least 2 characters to search locations.")
    if len(text) > 120:
        raise ValueError("Location searches are limited to 120 characters.")
    limit = max(1, min(int(limit), 10))
    cache_key = f"locations:{limit}:{text.lower()}"
    cached = store.cache_get(connection, cache_key, now)
    if isinstance(cached, list):
        return cached
    found = list(client.locations(text, limit))
    store.cache_set(connection, cache_key, found, LOCATION_CACHE_SECONDS, now)
    store.cache_expire(connection, now)
    store.cache_trim(connection, "locations:", LOCATION_CACHE_MAX_ENTRIES)
    connection.commit()
    return found


# ---------------------------------------------------------------------------
# options, hashing and record shaping
# ---------------------------------------------------------------------------


def _flag(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError(f"Option {name} must be true or false.")


def normalize_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate run options and return them with every key present."""
    raw = dict(options or {})
    unknown = sorted(set(raw) - OPTION_KEYS)
    if unknown:
        raise ValueError(
            f"Unknown option {unknown[0]!r}. Options: {', '.join(sorted(OPTION_KEYS))}."
        )
    output = str(raw.get("output") or "json").strip().lower()
    if output not in OUTPUTS:
        raise ValueError(f"Option output must be one of: {', '.join(OUTPUTS)}.")
    restrictor = str(raw.get("json_restrictor") or "").strip()
    if len(restrictor) > 500:
        raise ValueError("Option json_restrictor is limited to 500 characters.")
    if restrictor and output != "json":
        raise ValueError("Option json_restrictor only applies to JSON output.")
    clean = {
        "no_cache": _flag(raw.get("no_cache", False), "no_cache"),
        "output": output,
        "async": _flag(raw.get("async", False), "async"),
        "zero_trace": _flag(raw.get("zero_trace", False), "zero_trace"),
        "json_restrictor": restrictor,
        "allow_unknown": _flag(raw.get("allow_unknown", False), "allow_unknown"),
        "use_cache": _flag(raw.get("use_cache", False), "use_cache"),
    }
    if clean["async"] and clean["no_cache"]:
        raise ValueError("SerpApi does not allow async together with no_cache.")
    if clean["async"] and output != "json":
        raise ValueError("Async searches are collected as JSON; leave output as json.")
    return clean


def params_hash(
    engine_id: str, params: Mapping[str, Any], options: Mapping[str, Any]
) -> str:
    """Cache key: engine, sorted parameters and the options that change the body."""
    material = {
        "engine": engine_id,
        "params": {key: params[key] for key in sorted(params)},
        "output": options.get("output", "json"),
        "json_restrictor": options.get("json_restrictor", ""),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _engine_name(engine_id: str) -> str:
    try:
        return catalog.engine(engine_id).name
    except LookupError:
        return engine_id


def main_result_key(engine_id: str, result: Any) -> str | None:
    """The key of the list that represents the result set (what CSV and counts use)."""
    if not isinstance(result, dict):
        return None
    candidates = [
        key
        for key, value in result.items()
        if isinstance(value, list) and value and key not in _VOLATILE_KEYS
    ]
    if not candidates:
        return None
    for key in _PRIMARY_KEYS:
        if key in candidates:
            return key
    try:
        declared = catalog.engine(engine_id).result_keys
    except LookupError:
        declared = ()
    for key in declared:
        if key in candidates and key not in _SECONDARY_KEYS:
            return key
    pool = [key for key in candidates if key not in _SECONDARY_KEYS] or candidates
    return max(pool, key=lambda key: len(result[key]))


def count_results(engine_id: str, result: Any) -> int:
    key = main_result_key(engine_id, result)
    if key:
        return len(result[key])
    if isinstance(result, dict):
        meaningful = [
            name
            for name, value in result.items()
            if name not in _VOLATILE_KEYS and name != "search_information" and value
        ]
        return 1 if meaningful else 0
    return 0


def record_from_row(
    row: sqlite3.Row | Mapping[str, Any],
    *,
    result: Any = None,
    html: str | None = None,
    include_result: bool = False,
) -> dict[str, Any]:
    """Shape a stored row as the API record described in the spec (section 4.3)."""
    data = dict(row)
    try:
        params = json.loads(data.get("params_json") or "{}")
    except json.JSONDecodeError:
        params = {}
    try:
        options = json.loads(data.get("options_json") or "{}")
    except json.JSONDecodeError:
        options = {}
    html_available = bool(data.get("has_html")) or bool(
        data.get("serpapi_id")
        and data.get("status") == "ok"
        and data.get("output") == "json"
    )
    record: dict[str, Any] = {
        "id": data["id"],
        "serpapi_id": data.get("serpapi_id") or "",
        "engine": data["engine"],
        "engine_name": _engine_name(data["engine"]),
        "params": params,
        "options": options,
        "status": data["status"],
        "source": data["source"],
        "label": data.get("label") or "",
        "created_at": data["created_at"],
        "duration_ms": data.get("duration_ms") or 0,
        "http_status": data.get("http_status"),
        "error": data.get("error") or "",
        "note": data.get("note") or "",
        "result_count": data.get("result_count") or 0,
        "result_bytes": data.get("result_bytes") or 0,
        "cache_hit": bool(data.get("cache_hit")),
        "credits": data.get("credits") or 0,
        "pinned": bool(data.get("pinned")),
        "output": data.get("output") or "json",
        "html_available": html_available,
        "result_truncated": bool(data.get("result_truncated")),
        "main_result_key": None,
    }
    if include_result:
        record["result"] = result
        record["main_result_key"] = main_result_key(data["engine"], result)
        if html is not None:
            record["html"] = html
    return record


def get_search(
    connection: sqlite3.Connection,
    search_id: str,
    *,
    include_html: bool = False,
    client: Any = None,
    now: datetime | None = None,
    limits: Limits | None = None,
) -> dict[str, Any]:
    """One record with its result. Refreshes a running async search when possible."""
    row = store.get_search_row(connection, search_id)
    if row is None:
        raise LookupError("Search not found.")
    if row["status"] == "running" and client is not None:
        refresh_running(connection, client, search_id, now=now, limits=limits)
        row = store.get_search_row(connection, search_id)
        if row is None:
            raise LookupError("Search not found.")
    result_blob, html_blob = store.get_blobs(connection, search_id)
    html: str | None = None
    if include_html:
        html = store.unpack_text(html_blob)
        if html is None and client is not None and row["serpapi_id"]:
            html = _fetch_archive_html(connection, client, row, search_id)
    result = store.unpack(result_blob) if row["output"] == "json" else None
    record = record_from_row(row, result=result, html=html, include_result=True)
    if include_html:
        record["html"] = html
    return record


def _fetch_archive_html(
    connection: sqlite3.Connection, client: Any, row: sqlite3.Row, search_id: str
) -> str | None:
    try:
        raw = client.archive(row["serpapi_id"], "html")
    except SerpApiError:
        return None
    if raw.text is None:
        return None
    store.set_blobs(connection, search_id, html_blob=store.pack_text(raw.text))
    connection.commit()
    return raw.text


def list_searches(
    connection: sqlite3.Connection,
    *,
    engine: str = "",
    status: str = "",
    source: str = "",
    text: str = "",
    pinned: bool | None = None,
    date_from: str = "",
    date_to: str = "",
    cache_hit: bool | None = None,
    in_results: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """A page of history. With ``in_results``, ``text`` also matches inside stored
    results and each such record carries ``match_excerpt`` (matched words wrapped in
    ``store.SNIPPET_OPEN``/``store.SNIPPET_CLOSE``)."""
    rows, total = store.list_searches(
        connection,
        engine=engine,
        status=status,
        source=source,
        text=text,
        pinned=pinned,
        date_from=date_from,
        date_to=date_to,
        cache_hit=cache_hit,
        in_results=in_results,
        limit=limit,
        offset=offset,
    )
    items = [record_from_row(row) for row in rows]
    if in_results and text.strip():
        excerpts = store.text_snippets(connection, [item["id"] for item in items], text)
        for item in items:
            if item["id"] in excerpts:
                item["match_excerpt"] = excerpts[item["id"]]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "in_results": in_results,
    }


def update_search_meta(
    connection: sqlite3.Connection, search_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "pinned" in payload:
        fields["pinned"] = 1 if _flag(payload["pinned"], "pinned") else 0
    if "label" in payload:
        fields["label"] = str(payload["label"] or "").strip()[:200]
    if not fields:
        raise ValueError("Send pinned or label to update.")
    if not store.update_search(connection, search_id, fields):
        raise LookupError("Search not found.")
    connection.commit()
    row = store.get_search_row(connection, search_id)
    assert row is not None
    return record_from_row(row)


def delete_search(connection: sqlite3.Connection, search_id: str) -> None:
    if not store.delete_search(connection, search_id):
        raise LookupError("Search not found.")
    connection.commit()


def bulk_delete(
    connection: sqlite3.Connection,
    *,
    before: str = "",
    engine: str = "",
    everything: bool = False,
    include_pinned: bool = False,
) -> dict[str, int]:
    if not (before or engine or everything):
        raise ValueError(
            "Pass before, engine or all=1 so a bulk delete is never accidental."
        )
    if before:
        try:
            store.from_iso(before)
        except ValueError:
            raise ValueError("before must be an ISO-8601 date or time.") from None
        before = store.to_iso(store.from_iso(before))
    deleted, kept = store.delete_where(
        connection, before=before, engine=engine, include_pinned=include_pinned
    )
    connection.commit()
    return {"deleted": deleted, "kept_pinned": kept}


# ---------------------------------------------------------------------------
# running searches
# ---------------------------------------------------------------------------


class _Slot:
    """Context manager for the per-process concurrency cap."""

    def __enter__(self) -> None:
        global _inflight
        if not _slots.acquire(timeout=SLOT_WAIT_SECONDS):
            raise SerpApiError(
                "busy", "Too many searches are running at once. Try again in a moment."
            )
        with _inflight_lock:
            _inflight += 1

    def __exit__(self, *_exc: object) -> None:
        global _inflight
        with _inflight_lock:
            _inflight -= 1
        _slots.release()


def _day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def _database_id(connection: sqlite3.Connection) -> str:
    """Which database file this connection talks to (cache entries are per database)."""
    try:
        row = connection.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row else ""
    except sqlite3.Error:
        return ""


@contextmanager
def _single_flight(connection: sqlite3.Connection, digest: str) -> Iterator[None]:
    """Let identical cacheable searches run one at a time.

    Without this, N requests that arrive together for a search nobody has run
    yet all miss the cache and each spends a SerpApi search. The first one
    runs; the others wait, then find its record in the cache. Waiting is
    bounded, so a stuck upstream call can only delay, never deadlock.
    """
    token = (_database_id(connection), digest)
    with _flights_lock:
        entry = _flights.get(token)
        if entry is None:
            entry = _flights[token] = _Flight()
        entry.waiters += 1
    held = entry.lock.acquire(timeout=FLIGHT_WAIT_SECONDS)
    try:
        yield
    finally:
        if held:
            entry.lock.release()
        with _flights_lock:
            entry.waiters -= 1
            if entry.waiters <= 0 and _flights.get(token) is entry:
                del _flights[token]


def run_search(
    connection: sqlite3.Connection,
    client: Any,
    engine_id: str,
    params: Mapping[str, Any] | None,
    *,
    options: Mapping[str, Any] | None = None,
    source: str = "playground",
    label: str = "",
    now: datetime | None = None,
    limits: Limits | None = None,
    api_key_id: str = "",
) -> dict[str, Any]:
    """Run (or serve from cache) one search and return its record with the result.

    Raises ``ValueError`` for invalid parameters (nothing is recorded or
    billed), ``LookupError`` for an unknown engine and :class:`SerpApiError`
    for API failures; the failed attempt is recorded and its id is available as
    ``error.record_id``. ``client`` may be ``None``: cache hits still work and
    a live call then fails with ``not_configured``.
    """
    moment = now or store.now_utc()
    limits = limits or Limits()
    if source not in SOURCES:
        raise ValueError(f"source must be one of: {', '.join(SOURCES)}.")
    found = catalog.engine(engine_id)
    opts = normalize_options(options)
    clean, problems = catalog.validate_params(
        found.id, params, allow_unknown=opts["allow_unknown"]
    )
    if problems:
        raise ValueError(" ".join(problems))
    label = str(label or "").strip()[:200]
    digest = params_hash(found.id, clean, opts)
    base_row = {
        "engine": found.id,
        "params_json": json.dumps(clean, sort_keys=True, ensure_ascii=False),
        "params_hash": digest,
        "source": source,
        "label": label,
        "output": opts["output"],
        "options_json": json.dumps(
            {k: v for k, v in opts.items() if v not in (False, "") or k == "output"},
            sort_keys=True,
        ),
        "api_key_id": api_key_id,
    }
    cacheable = (
        limits.cache_ttl_minutes > 0 and not opts["no_cache"] and not opts["async"]
    )
    if cacheable:
        with _single_flight(connection, digest):
            return _execute_search(
                connection,
                client,
                found.id,
                clean,
                opts,
                base_row,
                digest,
                moment,
                limits,
            )
    return _execute_search(
        connection, client, found.id, clean, opts, base_row, digest, moment, limits
    )


def _execute_search(
    connection: sqlite3.Connection,
    client: Any,
    engine_id: str,
    clean: Mapping[str, Any],
    opts: Mapping[str, Any],
    base_row: Mapping[str, Any],
    digest: str,
    moment: datetime,
    limits: Limits,
) -> dict[str, Any]:
    """Cache lookup, local throttle, the SerpApi call and bookkeeping for one search."""
    ttl = limits.cache_ttl_minutes
    if ttl > 0 and not opts["no_cache"] and not opts["async"]:
        cached = store.find_cached(
            connection, digest, store.to_iso(moment - timedelta(minutes=ttl))
        )
        if cached is not None:
            record = _serve_cached(connection, cached, base_row, moment)
            _housekeeping(connection, limits, moment)
            return record
    local = catalog.is_local(engine_id)
    if local:
        # Aira answers its own engines; they need no SerpApi key and cost nothing.
        from aira.airaapi.jobboards import LocalJobsClient

        client = LocalJobsClient(connection)
    elif client is None:
        raise SerpApiError("not_configured", NOT_CONFIGURED_MESSAGE)
    client_options = {
        key: opts[key]
        for key in ("output", "async", "no_cache", "zero_trace", "json_restrictor")
    }
    try:
        with _Slot():
            if not local:
                _check_local_limit(connection, limits, moment)
            raw = client.search(engine_id, clean, client_options)
    except SerpApiError as failure:
        if failure.code == "local_rate_limit":
            raise
        failure.record_id = _record_failure(connection, base_row, failure, moment)
        _bump(connection, moment, engine_id, base_row["source"], errors=1)
        connection.commit()
        _housekeeping(connection, limits, moment)
        raise
    record = _record_success(
        connection, engine_id, raw, clean, opts, base_row, moment, limits
    )
    _housekeeping(connection, limits, moment)
    return record


def _check_local_limit(
    connection: sqlite3.Connection, limits: Limits, moment: datetime
) -> None:
    """Enforce the optional account-wide hourly cap.

    Checked while holding a search slot and counting the searches still in
    flight, so a burst of simultaneous requests cannot all pass the check
    before any of them has been recorded.
    """
    if not limits.hourly_limit:
        return
    used = store.count_live_since(
        connection,
        store.to_iso(moment - timedelta(hours=1)),
        exclude_engines=catalog.local_engine_ids(),
    )
    used += max(0, inflight_searches() - 1)
    if used >= limits.hourly_limit:
        raise SerpApiError(
            "local_rate_limit",
            f"The local hourly limit of {limits.hourly_limit} live searches is reached. "
            "Raise it in Aira API settings or wait for the hour to roll over.",
        )


def _housekeeping(
    connection: sqlite3.Connection, limits: Limits, moment: datetime
) -> None:
    """Keep history inside its caps after every insert (hits and failures too).

    Cache hits copy the stored body into a new row, so a loop of identical
    requests grows the table even though it never reaches SerpApi. The timed
    sweep alone would let that run for minutes; the row cap is checked on every
    insert and a sweep runs as soon as it is exceeded by a small margin.
    """
    _maybe_prune(connection, limits, moment)
    cap = limits.max_records
    if cap > 0 and store.count_searches(connection) > cap + max(10, cap // 20):
        apply_retention(connection, limits, moment)


def _serve_cached(
    connection: sqlite3.Connection,
    original: sqlite3.Row,
    base_row: Mapping[str, Any],
    moment: datetime,
) -> dict[str, Any]:
    result_blob, html_blob = store.get_blobs(connection, original["id"])
    row = {
        **base_row,
        "id": store.new_id(),
        "serpapi_id": original["serpapi_id"],
        "status": "ok",
        "created_at": store.to_iso(moment),
        "duration_ms": 0,
        "http_status": original["http_status"],
        "note": original["note"],
        "result_count": original["result_count"],
        "result_bytes": original["result_bytes"],
        "cache_hit": 1,
        "credits": 0,
        "result_truncated": original["result_truncated"],
    }
    store.insert_search(connection, row, result_blob=result_blob, html_blob=html_blob)
    result = store.unpack(result_blob) if base_row["output"] == "json" else None
    if result is not None:
        store.index_text(connection, row["id"], result)
    _bump(connection, moment, base_row["engine"], base_row["source"], cached=1)
    connection.commit()
    new_row = store.get_search_row(connection, row["id"])
    assert new_row is not None
    html = store.unpack_text(html_blob) if base_row["output"] != "json" else None
    return record_from_row(new_row, result=result, html=html, include_result=True)


def _record_failure(
    connection: sqlite3.Connection,
    base_row: Mapping[str, Any],
    failure: SerpApiError,
    moment: datetime,
) -> str:
    return store.insert_search(
        connection,
        {
            **base_row,
            "id": store.new_id(),
            "status": "error",
            "created_at": store.to_iso(moment),
            "http_status": failure.http_status,
            "error": failure.message[:1000],
            "credits": 0,
            "result_count": 0,
        },
    )


def _bump(
    connection: sqlite3.Connection,
    moment: datetime,
    engine_id: str,
    source: str,
    *,
    cached: int = 0,
    errors: int = 0,
    credits: int = 0,
    searches: int = 1,
) -> None:
    store.bump_usage(
        connection,
        _day(moment),
        engine_id,
        source,
        searches=searches,
        cached=cached,
        errors=errors,
        credits=credits,
    )


def _record_success(
    connection: sqlite3.Connection,
    engine_id: str,
    raw: RawResponse,
    clean: Mapping[str, Any],
    opts: Mapping[str, Any],
    base_row: Mapping[str, Any],
    moment: datetime,
    limits: Limits,
) -> dict[str, Any]:
    status = "running" if raw.queued else "ok"
    credits = 0 if raw.queued else catalog.credit_cost(engine_id, opts, clean)
    result_blob: bytes | None = None
    html_blob: bytes | None = None
    truncated = 0
    size = 0
    count = 0
    if raw.body is not None:
        encoded = json.dumps(raw.body, ensure_ascii=False)
        size = len(encoded.encode("utf-8"))
        count = 0 if (raw.queued or raw.note) else count_results(engine_id, raw.body)
        if size > limits.result_cap_bytes:
            truncated = 1
        else:
            result_blob = store.pack(raw.body)
    if raw.text is not None:
        size = len(raw.text.encode("utf-8"))
        if size > limits.result_cap_bytes:
            truncated = 1
        else:
            html_blob = store.pack_text(raw.text)
    row = {
        **base_row,
        "id": store.new_id(),
        "serpapi_id": raw.serpapi_id,
        "status": status,
        "created_at": store.to_iso(moment),
        "duration_ms": raw.duration_ms,
        "http_status": raw.status,
        "note": raw.note[:500],
        "result_count": count,
        "result_bytes": size,
        "cache_hit": 0,
        "credits": credits,
        "result_truncated": truncated,
    }
    store.insert_search(connection, row, result_blob=result_blob, html_blob=html_blob)
    if result_blob is not None and not raw.queued:
        store.index_text(connection, row["id"], raw.body)
    _bump(connection, moment, engine_id, base_row["source"], credits=credits)
    connection.commit()
    stored = store.get_search_row(connection, row["id"])
    assert stored is not None
    return record_from_row(stored, result=raw.body, html=raw.text, include_result=True)


def refresh_running(
    connection: sqlite3.Connection,
    client: Any,
    search_id: str,
    *,
    now: datetime | None = None,
    limits: Limits | None = None,
) -> dict[str, Any]:
    """Poll the Search Archive for an async search and store the outcome."""
    moment = now or store.now_utc()
    row = store.get_search_row(connection, search_id)
    if row is None:
        raise LookupError("Search not found.")
    if row["status"] != "running" or not row["serpapi_id"]:
        return record_from_row(row)
    try:
        raw = client.archive(row["serpapi_id"], "json")
    except SerpApiError as failure:
        if failure.code in {"gone", "not_found", "invalid_api_key"}:
            store.update_search(
                connection,
                search_id,
                {"status": "error", "error": failure.message[:1000], "credits": 0},
            )
            _bump(
                connection, moment, row["engine"], row["source"], errors=1, searches=0
            )
            connection.commit()
        return record_from_row(store.get_search_row(connection, search_id) or row)
    if raw.queued or raw.body is None:
        return record_from_row(row)
    body = raw.body
    state = str((body.get("search_metadata") or {}).get("status", "")).lower()
    if state == "error":
        store.update_search(
            connection,
            search_id,
            {
                "status": "error",
                "error": str(
                    body.get("error") or "SerpApi could not complete this search."
                )[:1000],
                "credits": 0,
            },
        )
        _bump(connection, moment, row["engine"], row["source"], errors=1, searches=0)
        connection.commit()
        return record_from_row(store.get_search_row(connection, search_id) or row)
    cap = (limits or Limits()).result_cap_bytes
    encoded = json.dumps(body, ensure_ascii=False)
    size = len(encoded.encode("utf-8"))
    params = json.loads(row["params_json"] or "{}")
    credits = catalog.credit_cost(row["engine"], {}, params)
    if size <= cap:
        store.set_blobs(connection, search_id, result_blob=store.pack(body))
        store.index_text(connection, search_id, body)
    store.update_search(
        connection,
        search_id,
        {
            "status": "ok",
            "credits": credits,
            "result_count": count_results(row["engine"], body),
            "result_bytes": size,
            "duration_ms": raw.duration_ms,
            "note": raw.note[:500],
            "result_truncated": 1 if size > cap else 0,
        },
    )
    _bump(connection, moment, row["engine"], row["source"], credits=credits, searches=0)
    connection.commit()
    return record_from_row(store.get_search_row(connection, search_id) or row)


def rerun(
    connection: sqlite3.Connection,
    client: Any,
    search_id: str,
    options: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
    limits: Limits | None = None,
) -> dict[str, Any]:
    """Run a stored search again; fresh by default, cached only with ``use_cache``."""
    row = store.get_search_row(connection, search_id)
    if row is None:
        raise LookupError("Search not found.")
    try:
        stored_options = json.loads(row["options_json"] or "{}")
    except json.JSONDecodeError:
        stored_options = {}
    supplied = dict(options or {})
    use_cache = bool(supplied.pop("use_cache", False))
    merged = {**stored_options, **supplied}
    merged.pop("use_cache", None)
    merged["no_cache"] = not (use_cache or merged.get("async"))
    return run_search(
        connection,
        client,
        row["engine"],
        json.loads(row["params_json"] or "{}"),
        options=merged,
        source="rerun",
        label=row["label"],
        now=now,
        limits=limits,
    )


def _maybe_prune(
    connection: sqlite3.Connection, limits: Limits, moment: datetime
) -> None:
    if store.cache_get(connection, "meta:pruned", moment):
        return
    apply_retention(connection, limits, moment)
    store.cache_set(connection, "meta:pruned", True, PRUNE_INTERVAL_SECONDS, moment)
    connection.commit()


def apply_retention(
    connection: sqlite3.Connection, limits: Limits, now: datetime | None = None
) -> int:
    """Delete expired and surplus records, then forget usage older than 400 days."""
    moment = now or store.now_utc()
    deleted = store.prune(
        connection,
        retention_days=limits.retention_days,
        max_records=limits.max_records,
        now=moment,
    )
    connection.execute(
        "DELETE FROM airaapi_usage WHERE day < ?", (_day(moment - timedelta(days=400)),)
    )
    store.cache_expire(connection, moment)
    connection.commit()
    return deleted


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def _item_key(item: Any) -> str:
    if isinstance(item, dict):
        for field in _IDENTITY_FIELDS:
            value = item.get(field)
            if isinstance(value, (str, int)) and str(value).strip():
                return f"{field}={value}"
        return (
            "hash="
            + hashlib.sha1(
                json.dumps(item, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:12]
        )
    return f"value={item}"


def _keyed(items: list[Any]) -> dict[str, tuple[int, Any]]:
    keyed: dict[str, tuple[int, Any]] = {}
    seen: defaultdict[str, int] = defaultdict(int)
    for index, item in enumerate(items):
        key = _item_key(item)
        seen[key] += 1
        if seen[key] > 1:
            key = f"{key}#{seen[key]}"
        keyed[key] = (index, item)
    return keyed


DIFF_MAX_DEPTH = 40


def _deep_changes(
    path: str, before: Any, after: Any, out: list[dict[str, Any]], depth: int = 0
) -> None:
    if len(out) >= DIFF_CAP:
        return
    if depth >= DIFF_MAX_DEPTH:  # absurdly nested bodies are compared as one value
        if before != after:
            out.append({"path": path, "before": before, "after": after})
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for name in sorted(set(before) | set(after)):
            child = f"{path}.{name}" if path else name
            if name not in before:
                out.append({"path": child, "before": None, "after": after[name]})
            elif name not in after:
                out.append({"path": child, "before": before[name], "after": None})
            else:
                _deep_changes(child, before[name], after[name], out, depth + 1)
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child = f"{path}[{index}]"
            if index >= len(before):
                out.append({"path": child, "before": None, "after": after[index]})
            elif index >= len(after):
                out.append({"path": child, "before": before[index], "after": None})
            else:
                _deep_changes(child, before[index], after[index], out, depth + 1)
        return
    if before != after:
        out.append({"path": path, "before": before, "after": after})


def diff_results(engine_id: str, before: Any, after: Any) -> dict[str, Any]:
    """Compare two result bodies: items added/removed/changed in the main list plus other changes."""
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    list_key = main_result_key(engine_id, after) or main_result_key(engine_id, before)
    added: list[Any] = []
    removed: list[Any] = []
    changed: list[dict[str, Any]] = []
    if list_key:
        old_raw, new_raw = before.get(list_key), after.get(list_key)
        old_items = old_raw if isinstance(old_raw, list) else []
        new_items = new_raw if isinstance(new_raw, list) else []
        old_map, new_map = _keyed(old_items), _keyed(new_items)
        added = [item for key, (_i, item) in new_map.items() if key not in old_map]
        removed = [item for key, (_i, item) in old_map.items() if key not in new_map]
        for key, (new_index, new_item) in new_map.items():
            if key not in old_map or len(changed) >= DIFF_CAP:
                continue
            old_index, old_item = old_map[key]
            prefix = f"{list_key}[{key}]"
            if old_index != new_index:
                changed.append(
                    {
                        "path": f"{prefix}.rank",
                        "before": old_index + 1,
                        "after": new_index + 1,
                    }
                )
            _deep_changes(prefix, old_item, new_item, changed)
    for name in sorted((set(before) | set(after)) - _VOLATILE_KEYS - {list_key}):
        if len(changed) >= DIFF_CAP:
            break
        if name not in before:
            changed.append({"path": name, "before": None, "after": after[name]})
        elif name not in after:
            changed.append({"path": name, "before": before[name], "after": None})
        else:
            _deep_changes(name, before[name], after[name], changed)
    parts = []
    if added:
        parts.append(f"{len(added)} added")
    if removed:
        parts.append(f"{len(removed)} removed")
    if changed:
        parts.append(f"{len(changed)} changed")
    summary = ", ".join(parts) if parts else "No differences"
    if list_key and parts:
        summary += f" in {list_key}"
    return {
        "list_key": list_key,
        "added": added[:DIFF_CAP],
        "removed": removed[:DIFF_CAP],
        "changed": changed[:DIFF_CAP],
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "changed_any": bool(parts),
        "summary": summary,
    }


def diff_searches(
    connection: sqlite3.Connection, a_id: str, b_id: str
) -> dict[str, Any]:
    """Diff two stored searches (a is the earlier run, b the later)."""
    records = []
    for search_id in (a_id, b_id):
        row = store.get_search_row(connection, search_id)
        if row is None:
            raise LookupError("Search not found.")
        if row["output"] != "json":
            raise ValueError("Only JSON searches can be compared.")
        blob, _ = store.get_blobs(connection, search_id)
        if blob is None:
            raise ValueError(
                "A compared search has no stored result (it failed, is still running or exceeded the size cap)."
            )
        records.append((row, store.unpack(blob)))
    (row_a, body_a), (row_b, body_b) = records
    diff = diff_results(row_b["engine"], body_a, body_b)
    diff["a"] = record_from_row(row_a)
    diff["b"] = record_from_row(row_b)
    if row_a["engine"] != row_b["engine"]:
        diff["summary"] = "Different engines: " + diff["summary"]
    return diff


# ---------------------------------------------------------------------------
# X-Ray (pixel positions of an archived search)
# ---------------------------------------------------------------------------

# SerpApi's Pixel Position (beta) covers these engines only.
XRAY_ENGINES = frozenset({"google", "google_ads"})
XRAY_ELEMENT_CAP = 500
_XRAY_LABEL_FIELDS = (
    "title",
    "name",
    "question",
    "query",
    "snippet",
    "displayed_link",
    "link",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _rect(value: Any) -> dict[str, Any] | None:
    """A rectangle with finite x, y, width and height, or None."""
    if not isinstance(value, dict):
        return None
    numbers = {name: _number(value.get(name)) for name in ("x", "y", "width", "height")}
    if any(number is None for number in numbers.values()):
        return None
    rect: dict[str, Any] = dict(numbers)
    rect["hidden"] = bool(value.get("is_hidden"))
    return rect


def _xray_fields(item: Mapping[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name, value in item.items():
        if name == "rectangle" or isinstance(value, (dict, list)) or value is None:
            continue
        fields[str(name)] = str(value)[:240]
        if len(fields) >= 8:
            break
    return fields


def xray_elements(body: Any) -> tuple[list[dict[str, Any]], bool]:
    """Every part of a pixel-position body that carries a ``rectangle``, with its JSON path.

    Returns (elements sorted top to bottom, whether the list was capped).
    """
    found: list[dict[str, Any]] = []
    capped = False
    if not isinstance(body, dict):
        return found, capped
    stack: list[tuple[str, str, Any]] = [
        (str(key), str(key), value)
        for key, value in reversed(list(body.items()))
        if key not in _VOLATILE_KEYS and key != "pixel_position_information"
    ]
    while stack:
        path, section, value = stack.pop()
        if isinstance(value, dict):
            rect = _rect(value.get("rectangle"))
            if rect is not None:
                if len(found) >= XRAY_ELEMENT_CAP:
                    capped = True
                    break
                label = next(
                    (
                        str(value[name])
                        for name in _XRAY_LABEL_FIELDS
                        if isinstance(value.get(name), (str, int, float))
                        and str(value[name]).strip()
                    ),
                    path,
                )
                found.append(
                    {
                        "path": path,
                        "section": section,
                        "label": label[:160],
                        "rect": rect,
                        "fields": _xray_fields(value),
                    }
                )
            stack.extend(
                (f"{path}.{key}", section, child)
                for key, child in reversed(list(value.items()))
                if key != "rectangle" and isinstance(child, (dict, list))
            )
        elif isinstance(value, list):
            stack.extend(
                (f"{path}[{index}]", section, child)
                for index, child in reversed(list(enumerate(value)))
                if isinstance(child, (dict, list))
            )
    found.sort(key=lambda item: (item["rect"]["y"], item["rect"]["x"]))
    return found, capped


def _xray_parents(info: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = info.get("parent_rectangles")
    pairs: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, list):
                pairs.extend(
                    (f"{name}[{index}]", item) for index, item in enumerate(value)
                )
            else:
                pairs.append((str(name), value))
    parents = []
    for name, value in pairs[:200]:
        rect = _rect(value)
        if rect is not None:
            parents.append({"path": name, "rect": rect})
    return parents


def xray(
    connection: sqlite3.Connection,
    client: Any,
    search_id: str,
    *,
    refresh: bool = False,
    limits: Limits | None = None,
) -> dict[str, Any]:
    """Pixel positions for a stored Google or Google Ads search, mapped to JSON paths.

    The pixel-position copy comes from SerpApi's Search Archive (no search is
    used) the first time and is kept with the search afterwards.
    """
    row = store.get_search_row(connection, search_id)
    if row is None:
        raise LookupError("Search not found.")
    if row["engine"] not in XRAY_ENGINES:
        raise ValueError(
            "X-Ray works on Google Search and Google Ads searches: SerpApi measures pixel positions for those engines only."
        )
    if row["status"] != "ok" or not row["serpapi_id"]:
        raise ValueError(
            "X-Ray needs a successful search that SerpApi archived (one with a SerpApi id)."
        )
    blob = None if refresh else store.get_pixel_blob(connection, search_id)
    cached = blob is not None
    if blob is not None:
        body = store.unpack(blob)
    else:
        if client is None:
            raise SerpApiError(
                "not_configured",
                "Connect a SerpApi key in Account to measure this search. Fetching pixel positions from the archive does not use a search.",
            )
        raw = client.archive(row["serpapi_id"], "json_with_pixel_position")
        if raw.queued or raw.body is None:
            raise ValueError(
                "SerpApi is still measuring this search. Try X-Ray again in a few seconds."
            )
        body = raw.body
        packed = store.pack(body)
        if len(packed) <= (limits or Limits()).result_cap_bytes:
            store.set_pixel_blob(connection, search_id, packed)
            connection.commit()
    info = body.get("pixel_position_information") if isinstance(body, dict) else None
    info = info if isinstance(info, dict) else {}
    elements, capped = xray_elements(body)
    width = _number(info.get("viewport_width"))
    height = _number(info.get("viewport_height"))
    extent = max(
        [item["rect"]["y"] + item["rect"]["height"] for item in elements] or [0.0]
    )
    record = record_from_row(row)
    return {
        "search_id": row["id"],
        "serpapi_id": row["serpapi_id"],
        "engine": row["engine"],
        "viewport": {
            "width": width or 0,
            "height": height or 0,
            "page_height": max(extent, height or 0),
        },
        "time_taken": _number(info.get("time_taken")),
        "error": str(info.get("error") or "")[:500],
        "parents": _xray_parents(info),
        "elements": elements,
        "element_count": len(elements),
        "capped": capped,
        "cached": cached,
        "html_available": record["html_available"],
    }


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_safe(text: str) -> str:
    """Stop a spreadsheet from running a cell as a formula (CSV injection)."""
    return "'" + text if text[:1] in _FORMULA_LEADS else text


def _plain(text: str) -> str:
    return text


def _flatten(
    value: Any,
    prefix: str,
    out: dict[str, str],
    depth: int = 0,
    safe: Callable[[str], str] = _csv_safe,
) -> None:
    if isinstance(value, dict) and depth < 3:
        for name, child in value.items():
            _flatten(
                child, f"{prefix}.{name}" if prefix else str(name), out, depth + 1, safe
            )
        return
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            out[prefix] = safe(" | ".join(str(item) for item in value))
        else:
            out[prefix] = json.dumps(value, ensure_ascii=False)
        return
    if isinstance(value, dict):
        out[prefix] = json.dumps(value, ensure_ascii=False)
        return
    if value is None:
        out[prefix] = ""
    elif isinstance(value, bool):
        out[prefix] = "true" if value else "false"
    elif isinstance(value, str):
        out[prefix] = safe(value)
    else:
        out[prefix] = str(value)


def result_table(
    engine_id: str, result: Any, *, safe: Callable[[str], str] = _csv_safe
) -> tuple[list[str], list[list[str]]]:
    """The main result list (or the whole body) as (columns, rows) of flattened text."""
    key = main_result_key(engine_id, result)
    if key:
        items = result[key]
    elif isinstance(result, dict):
        items = [{k: v for k, v in result.items() if k not in _VOLATILE_KEYS}]
    else:
        raise ValueError("This result has nothing that can be exported as a table.")
    flats: list[dict[str, str]] = []
    columns: list[str] = []
    for item in items:
        flat: dict[str, str] = {}
        if isinstance(item, dict):
            _flatten(item, "", flat, safe=safe)
        else:
            _flatten(item, "value", flat, safe=safe)
        for name in flat:
            if name not in columns and len(columns) < CSV_COLUMN_CAP:
                columns.append(name)
        flats.append(flat)
    return columns, [[flat.get(name, "") for name in columns] for flat in flats]


def results_to_csv(engine_id: str, result: Any) -> str:
    """Flatten the main result list (or the whole body) into CSV text."""
    columns, rows = result_table(engine_id, result)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([_csv_safe(name) for name in columns])
    writer.writerows(rows)
    return buffer.getvalue()


def results_to_xlsx(engine_id: str, result: Any) -> bytes:
    """The same table as the CSV export, as a one-sheet workbook."""
    columns, rows = result_table(engine_id, result, safe=_plain)
    return xlsx.workbook(main_result_key(engine_id, result) or engine_id, columns, rows)


MARKDOWN_COLUMN_CAP = 8
MARKDOWN_CELL_CHARS = 160


def _md_cell(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) > MARKDOWN_CELL_CHARS:
        flat = flat[: MARKDOWN_CELL_CHARS - 1] + "…"
    return flat.replace("\\", "\\\\").replace("|", "\\|")


def results_to_markdown(record: Mapping[str, Any], result: Any) -> str:
    """A readable Markdown page: what was searched, then the main list as a table."""
    engine_id = str(record["engine"])
    columns, rows = result_table(engine_id, result, safe=_plain)
    # A Markdown table with dozens of columns is unreadable; keep the ones that are filled most.
    filled = sorted(
        range(len(columns)),
        key=lambda i: (-sum(1 for row in rows if row[i]), i),
    )
    keep = sorted(filled[:MARKDOWN_COLUMN_CAP])
    params = record.get("params") or {}
    lines = [
        f"# {_engine_name(engine_id)}: {_md_cell(str(params.get('q') or params.get('query') or record.get('label') or engine_id))}",
        "",
        f"- Searched: {record.get('created_at', '')}",
        f"- Parameters: {_md_cell(', '.join(f'{k}={v}' for k, v in params.items())) or 'none'}",
        f"- Results: {len(rows)}"
        + (
            f" in `{main_result_key(engine_id, result)}`"
            if main_result_key(engine_id, result)
            else ""
        ),
    ]
    if len(columns) > len(keep):
        lines.append(
            f"- Showing {len(keep)} of {len(columns)} columns; the CSV and XLSX exports have all of them."
        )
    lines.append("")
    if keep:
        lines.append("| # | " + " | ".join(_md_cell(columns[i]) for i in keep) + " |")
        lines.append("|---|" + "|".join("---" for _ in keep) + "|")
        for number, row in enumerate(rows, start=1):
            lines.append(
                f"| {number} | " + " | ".join(_md_cell(row[i]) for i in keep) + " |"
            )
    return "\n".join(lines) + "\n"


def curl_command(
    engine_id: str, params: Mapping[str, Any], options: Mapping[str, Any], base: str
) -> str:
    """A curl line for the direct SerpApi call, with a placeholder instead of the key."""
    parts = [
        "curl",
        "-G",
        shlex.quote(f"{base}/search.json"),
        "-d",
        shlex.quote(f"engine={engine_id}"),
    ]
    for name, value in params.items():
        text = "true" if value is True else "false" if value is False else str(value)
        parts += ["--data-urlencode", shlex.quote(f"{name}={text}")]
    for flag in ("no_cache", "async", "zero_trace"):
        if options.get(flag):
            parts += ["-d", f"{flag}=true"]
    if options.get("output") and options["output"] != "json":
        parts += ["-d", shlex.quote(f"output={options['output']}")]
    parts += ["-d", "api_key=YOUR_SERPAPI_KEY"]
    return " ".join(parts)


EXPORT_FORMATS = ("json", "csv", "xlsx", "md", "html", "curl")


def export_search(
    connection: sqlite3.Connection,
    search_id: str,
    fmt: str,
    *,
    serpapi_base: str = "https://serpapi.com",
) -> tuple[bytes, str, str]:
    """Return (body, content type, file name) for json, csv, xlsx, md, html or curl exports."""
    fmt = fmt.lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError("format must be json, csv, xlsx, md, html or curl.")
    row = store.get_search_row(connection, search_id)
    if row is None:
        raise LookupError("Search not found.")
    record = record_from_row(row)
    stem = f"{row['engine']}-{row['id'][:8]}"
    result_blob, html_blob = store.get_blobs(connection, search_id)
    if fmt == "curl":
        text = curl_command(
            row["engine"], record["params"], record["options"], serpapi_base
        )
        return (text + "\n").encode("utf-8"), "text/plain; charset=utf-8", f"{stem}.sh"
    if fmt == "html":
        html = store.unpack_text(html_blob)
        if html is None:
            raise LookupError(
                "No raw page was stored for this search. Re-run it with output html."
                if row["output"] == "json"
                else "The stored body is empty."
            )
        kind = (
            "text/markdown; charset=utf-8"
            if row["output"] == "md"
            else "text/html; charset=utf-8"
        )
        return (
            html.encode("utf-8"),
            kind,
            f"{stem}.{'md' if row['output'] == 'md' else 'html'}",
        )
    if row["output"] != "json" or result_blob is None:
        raise LookupError("This search has no stored JSON result to export.")
    result = store.unpack(result_blob)
    if fmt == "csv":
        return (
            results_to_csv(row["engine"], result).encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"{stem}.csv",
        )
    if fmt == "xlsx":
        return (
            results_to_xlsx(row["engine"], result),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{stem}.xlsx",
        )
    if fmt == "md":
        return (
            results_to_markdown(record, result).encode("utf-8"),
            "text/markdown; charset=utf-8",
            f"{stem}.md",
        )
    document = {"search": record, "result": result}
    return (
        json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"),
        "application/json; charset=utf-8",
        f"{stem}.json",
    )


# ---------------------------------------------------------------------------
# saved searches and monitors
# ---------------------------------------------------------------------------


def _saved_view(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    try:
        params = json.loads(data.get("params_json") or "{}")
    except json.JSONDecodeError:
        params = {}
    try:
        tags = json.loads(data.get("tags") or "[]")
    except json.JSONDecodeError:
        tags = []
    return {
        "id": data["id"],
        "name": data["name"],
        "engine": data["engine"],
        "engine_name": _engine_name(data["engine"]),
        "params": params,
        "tags": tags if isinstance(tags, list) else [],
        "created_at": data["created_at"],
        "last_run_at": data.get("last_run_at"),
        "last_search_id": data.get("last_search_id"),
        "pinned": bool(data.get("pinned")),
    }


def _clean_tags(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    items = raw.split(",") if isinstance(raw, str) else raw
    if not isinstance(items, list):
        raise ValueError("tags must be a list of short labels.")
    tags: list[str] = []
    for item in items:
        text = str(item).strip()[:40]
        if text and text.lower() not in {tag.lower() for tag in tags}:
            tags.append(text)
    if len(tags) > 10:
        raise ValueError("A saved search can have at most 10 tags.")
    return tags


def _clean_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 120:
        raise ValueError("Give the saved search a name of up to 120 characters.")
    return name


def list_saved(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_saved_view(row) for row in store.list_saved(connection)]


def create_saved(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or store.now_utc()
    name = _clean_name(payload.get("name"))
    found = catalog.engine(str(payload.get("engine", "")))
    clean, problems = catalog.validate_params(found.id, payload.get("params") or {})
    if problems:
        raise ValueError(" ".join(problems))
    if len(store.list_saved(connection)) >= MAX_SAVED:
        raise ValueError(
            f"You can keep up to {MAX_SAVED} saved searches; delete some first."
        )
    row = {
        "id": store.new_id(),
        "name": name,
        "engine": found.id,
        "params_json": json.dumps(clean, sort_keys=True, ensure_ascii=False),
        "tags": json.dumps(_clean_tags(payload.get("tags"))),
        "created_at": store.to_iso(moment),
        "pinned": 1 if payload.get("pinned") else 0,
    }
    store.insert_saved(connection, row)
    connection.commit()
    return _saved_view(row)


def update_saved(
    connection: sqlite3.Connection, saved_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    existing = store.get_saved(connection, saved_id)
    if existing is None:
        raise LookupError("Saved search not found.")
    fields: dict[str, Any] = {}
    if "name" in payload:
        fields["name"] = _clean_name(payload["name"])
    if "tags" in payload:
        fields["tags"] = json.dumps(_clean_tags(payload["tags"]))
    if "pinned" in payload:
        fields["pinned"] = 1 if _flag(payload["pinned"], "pinned") else 0
    if "params" in payload:
        clean, problems = catalog.validate_params(
            existing["engine"], payload["params"] or {}
        )
        if problems:
            raise ValueError(" ".join(problems))
        fields["params_json"] = json.dumps(clean, sort_keys=True, ensure_ascii=False)
    if not fields:
        raise ValueError("Send name, tags, pinned or params to update.")
    store.update_saved(connection, saved_id, fields)
    connection.commit()
    row = store.get_saved(connection, saved_id)
    assert row is not None
    return _saved_view(row)


def delete_saved(connection: sqlite3.Connection, saved_id: str) -> None:
    if not store.delete_saved(connection, saved_id):
        raise LookupError("Saved search not found.")
    connection.commit()


def run_saved(
    connection: sqlite3.Connection,
    client: Any,
    saved_id: str,
    *,
    options: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    limits: Limits | None = None,
    source: str = "playground",
) -> dict[str, Any]:
    saved = store.get_saved(connection, saved_id)
    if saved is None:
        raise LookupError("Saved search not found.")
    moment = now or store.now_utc()
    record = run_search(
        connection,
        client,
        saved["engine"],
        json.loads(saved["params_json"] or "{}"),
        options=options,
        source=source,
        label=saved["name"],
        now=moment,
        limits=limits,
    )
    store.update_saved(
        connection,
        saved_id,
        {"last_run_at": store.to_iso(moment), "last_search_id": record["id"]},
    )
    connection.commit()
    return record


def estimate_monthly_searches(interval_minutes: int) -> int:
    return math.ceil(30 * 24 * 60 / max(1, interval_minutes))


MAX_MONTHLY_BUDGET = 100_000


def _month_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def _next_month_start(moment: datetime) -> datetime:
    first = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def _runs_this_month(row: sqlite3.Row | Mapping[str, Any], moment: datetime) -> int:
    """Live runs counted against the budget in ``moment``'s month (the counter resets each month)."""
    data = dict(row)
    if (data.get("budget_month") or "") != _month_key(moment):
        return 0
    return int(data.get("month_runs") or 0)


def _budget(value: Any) -> int:
    try:
        budget = int(str(value).strip() or "0")
    except (TypeError, ValueError):
        raise ValueError(
            "monthly_budget must be a whole number (0 for no cap)."
        ) from None
    if budget < 0 or budget > MAX_MONTHLY_BUDGET:
        raise ValueError(
            f"monthly_budget must be between 0 (no cap) and {MAX_MONTHLY_BUDGET:,}."
        )
    return budget


def _monitor_view(
    row: sqlite3.Row | Mapping[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    data = dict(row)
    moment = now or store.now_utc()
    try:
        params = json.loads(data.get("params_json") or "{}")
    except json.JSONDecodeError:
        params = {}
    budget = int(data.get("monthly_budget") or 0)
    used = _runs_this_month(data, moment)
    estimate = estimate_monthly_searches(data["interval_minutes"])
    return {
        "id": data["id"],
        "saved_id": data["saved_id"],
        "saved_name": data.get("saved_name", ""),
        "engine": data.get("engine", ""),
        "engine_name": _engine_name(data.get("engine", "")),
        "params": params,
        "interval_minutes": data["interval_minutes"],
        "enabled": bool(data["enabled"]),
        "next_run_at": data["next_run_at"],
        "last_run_at": data.get("last_run_at"),
        "last_search_id": data.get("last_search_id"),
        "notify_on_change": bool(data["notify_on_change"]),
        "last_change_summary": data.get("last_change_summary") or "",
        "estimated_monthly_searches": min(estimate, budget) if budget else estimate,
        "unbudgeted_monthly_searches": estimate,
        "monthly_budget": budget,
        "month_runs": used,
        "budget_remaining": max(budget - used, 0) if budget else None,
        "budget_exhausted": bool(budget and used >= budget),
        "budget_resets_at": store.to_iso(_next_month_start(moment)),
        "pause_on_error": bool(data.get("pause_on_error")),
    }


def _interval(value: Any) -> int:
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("interval_minutes must be a whole number.") from None
    if minutes < MIN_MONITOR_INTERVAL:
        raise ValueError(
            f"A monitor cannot run more often than every {MIN_MONITOR_INTERVAL} minutes."
        )
    if minutes > 60 * 24 * 90:
        raise ValueError("interval_minutes cannot be longer than 90 days.")
    return minutes


def list_monitors(
    connection: sqlite3.Connection, now: datetime | None = None
) -> list[dict[str, Any]]:
    return [_monitor_view(row, now) for row in store.list_monitors(connection)]


def create_monitor(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or store.now_utc()
    saved = store.get_saved(connection, str(payload.get("saved_id", "")))
    if saved is None:
        raise LookupError("Saved search not found.")
    minutes = _interval(payload.get("interval_minutes", 60))
    if len(store.list_monitors(connection)) >= MAX_MONITORS:
        raise ValueError(
            f"You can run up to {MAX_MONITORS} monitors; delete one first."
        )
    if any(item["saved_id"] == saved["id"] for item in store.list_monitors(connection)):
        raise ValueError("That saved search already has a monitor.")
    monitor_id = store.new_id()
    store.insert_monitor(
        connection,
        {
            "id": monitor_id,
            "saved_id": saved["id"],
            "interval_minutes": minutes,
            "enabled": 0 if payload.get("enabled") is False else 1,
            "next_run_at": store.to_iso(moment + timedelta(minutes=minutes)),
            "notify_on_change": 0 if payload.get("notify_on_change") is False else 1,
            "monthly_budget": _budget(payload.get("monthly_budget", 0)),
            "pause_on_error": 1
            if _flag(payload.get("pause_on_error", False), "pause_on_error")
            else 0,
        },
    )
    connection.commit()
    row = store.get_monitor(connection, monitor_id)
    assert row is not None
    return _monitor_view(row, moment)


def update_monitor(
    connection: sqlite3.Connection,
    monitor_id: str,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or store.now_utc()
    existing = store.get_monitor(connection, monitor_id)
    if existing is None:
        raise LookupError("Monitor not found.")
    fields: dict[str, Any] = {}
    if "interval_minutes" in payload:
        minutes = _interval(payload["interval_minutes"])
        fields["interval_minutes"] = minutes
        fields["next_run_at"] = store.to_iso(moment + timedelta(minutes=minutes))
    if "enabled" in payload:
        enabled = _flag(payload["enabled"], "enabled")
        fields["enabled"] = 1 if enabled else 0
        if enabled and not existing["enabled"] and "next_run_at" not in fields:
            fields["next_run_at"] = store.to_iso(
                moment + timedelta(minutes=existing["interval_minutes"])
            )
    if "notify_on_change" in payload:
        fields["notify_on_change"] = (
            1 if _flag(payload["notify_on_change"], "notify_on_change") else 0
        )
    if "monthly_budget" in payload:
        budget = _budget(payload["monthly_budget"])
        fields["monthly_budget"] = budget
        # A raised budget lets a monitor that was waiting for next month run on schedule again.
        if (
            existing["enabled"]
            and "next_run_at" not in fields
            and (not budget or _runs_this_month(existing, moment) < budget)
            and existing["next_run_at"]
            > store.to_iso(moment + timedelta(minutes=existing["interval_minutes"]))
        ):
            fields["next_run_at"] = store.to_iso(
                moment + timedelta(minutes=existing["interval_minutes"])
            )
    if "pause_on_error" in payload:
        fields["pause_on_error"] = (
            1 if _flag(payload["pause_on_error"], "pause_on_error") else 0
        )
    if not fields:
        raise ValueError(
            "Send interval_minutes, enabled, notify_on_change, monthly_budget or pause_on_error to update."
        )
    store.update_monitor(connection, monitor_id, fields)
    connection.commit()
    row = store.get_monitor(connection, monitor_id)
    assert row is not None
    return _monitor_view(row, moment)


def delete_monitor(connection: sqlite3.Connection, monitor_id: str) -> None:
    if not store.delete_monitor(connection, monitor_id):
        raise LookupError("Monitor not found.")
    connection.commit()


Notifier = Callable[[str, str, str], None]

# A monitor hook lets a feature (the Careers job alerts) take over what a
# monitor run means. It receives the connection, the monitor row (with its
# saved search), the finished record and the notifier; it returns a one-line
# summary when it handled the run (which also suppresses the generic "monitor
# changed" notification) and ``None`` when the monitor is not its own.
MonitorHook = Callable[
    [sqlite3.Connection, sqlite3.Row, dict[str, Any], "Notifier | None"],
    "str | None",
]
MONITOR_HOOKS: list[MonitorHook] = []


def register_monitor_hook(hook: MonitorHook) -> None:
    """Add ``hook`` once; importing a feature module twice must not double-run it."""
    if hook not in MONITOR_HOOKS:
        MONITOR_HOOKS.append(hook)


def _run_monitor_hooks(
    connection: sqlite3.Connection,
    monitor: sqlite3.Row,
    record: dict[str, Any],
    notify: Notifier | None,
) -> str | None:
    for hook in list(MONITOR_HOOKS):
        try:
            summary = hook(connection, monitor, record, notify)
        except Exception:  # a broken feature must never break the monitor itself
            logger.exception("Monitor hook %s failed", getattr(hook, "__name__", hook))
            continue
        if summary is not None:
            return summary
    return None


def run_monitor(
    connection: sqlite3.Connection,
    client: Any,
    monitor_id: str,
    *,
    now: datetime | None = None,
    notify: Notifier | None = None,
    limits: Limits | None = None,
    scheduled: bool = False,
) -> dict[str, Any]:
    """Run a monitor's saved search now, diff it against the previous run and notify on change.

    A monitor with a monthly budget that is used up does not run: a scheduled run
    waits for the next month (``skipped`` in the outcome), a run asked for by hand
    is refused with ``ValueError``.
    """
    moment = now or store.now_utc()
    monitor = store.get_monitor(connection, monitor_id)
    if monitor is None:
        raise LookupError("Monitor not found.")
    interval = timedelta(minutes=monitor["interval_minutes"])
    previous_id = monitor["last_search_id"]
    budget = int(monitor["monthly_budget"] or 0)
    used = _runs_this_month(monitor, moment)
    if budget and used >= budget:
        resets = _next_month_start(moment)
        message = (
            f"Monthly budget reached: {used} of {budget} searches in {moment.strftime('%B')}. "
            f"Runs resume on {resets.strftime('%d %B').lstrip('0')}"
        )
        if not scheduled:
            raise ValueError(f"{message}, or raise the budget to run it now.")
        store.update_monitor(
            connection,
            monitor_id,
            {
                "next_run_at": store.to_iso(max(resets, moment + interval)),
                "last_change_summary": message[:300],
            },
        )
        connection.commit()
        return {"record": None, "diff": None, "summary": message, "skipped": True}
    try:
        record = run_search(
            connection,
            client,
            monitor["engine"],
            json.loads(monitor["params_json"] or "{}"),
            options={"no_cache": True},
            source="monitor",
            label=f"Monitor: {monitor['saved_name']}",
            now=moment,
            limits=limits,
        )
    except (SerpApiError, ValueError, LookupError) as failure:
        message = failure.message if isinstance(failure, SerpApiError) else str(failure)
        pause = bool(monitor["pause_on_error"])
        fields: dict[str, Any] = {
            "last_run_at": store.to_iso(moment),
            "next_run_at": store.to_iso(
                moment + max(interval, timedelta(minutes=MIN_MONITOR_INTERVAL))
            ),
            "last_change_summary": (
                f"Run failed: {message}"[:240]
                + (" Paused: pause on error is on." if pause else "")
            ),
        }
        if pause:
            fields["enabled"] = 0
        store.update_monitor(connection, monitor_id, fields)
        connection.commit()
        if pause and notify is not None:
            notify(
                f"Monitor paused: {monitor['saved_name']}",
                f"The run failed ({message[:160]}) and the monitor is set to pause on error.",
                "#/airaapi?tab=monitors",
            )
        raise
    diff: dict[str, Any] | None = None
    summary = f"Baseline captured ({record['result_count']} results)"
    if previous_id and store.get_search_row(connection, previous_id) is not None:
        try:
            diff = diff_searches(connection, previous_id, record["id"])
            summary = diff["summary"]
        except (ValueError, RecursionError):
            diff = None  # unreadable or absurdly nested: the run still counts as done
    fields = {
        "last_run_at": store.to_iso(moment),
        "next_run_at": store.to_iso(moment + interval),
        "last_search_id": record["id"],
        "last_change_summary": summary[:300],
    }
    # Only runs that reached SerpApi count against the budget (monitors never use the cache).
    if not record.get("cache_hit"):
        fields["budget_month"] = _month_key(moment)
        fields["month_runs"] = used + 1
    store.update_monitor(connection, monitor_id, fields)
    store.update_saved(
        connection,
        monitor["saved_id"],
        {"last_run_at": store.to_iso(moment), "last_search_id": record["id"]},
    )
    connection.commit()
    handled = _run_monitor_hooks(connection, monitor, record, notify)
    if handled is not None:
        summary = handled
        store.update_monitor(
            connection, monitor_id, {"last_change_summary": handled[:300]}
        )
        connection.commit()
    elif (
        diff
        and diff["changed_any"]
        and monitor["notify_on_change"]
        and notify is not None
    ):
        notify(
            f"Monitor changed: {monitor['saved_name']}",
            f"{summary}.",
            f"#/airaapi?tab=history&search={record['id']}",
        )
    return {"record": record, "diff": diff, "summary": summary}


def run_due_monitors(
    connection: sqlite3.Connection,
    client: Any,
    *,
    now: datetime | None = None,
    notify: Notifier | None = None,
    limits: Limits | None = None,
    max_runs: int = 10,
) -> list[dict[str, Any]]:
    """Run every enabled monitor whose time has come. Failures are recorded, not raised."""
    moment = now or store.now_utc()
    results: list[dict[str, Any]] = []
    for monitor in store.due_monitors(connection, store.to_iso(moment))[:max_runs]:
        try:
            outcome = run_monitor(
                connection,
                client,
                monitor["id"],
                now=moment,
                notify=notify,
                limits=limits,
                scheduled=True,
            )
            results.append(
                {
                    "monitor_id": monitor["id"],
                    "ok": True,
                    "summary": outcome["summary"],
                    "skipped": bool(outcome.get("skipped")),
                    "credits": int((outcome.get("record") or {}).get("credits") or 0),
                }
            )
        except (SerpApiError, ValueError, LookupError) as failure:
            message = (
                failure.message if isinstance(failure, SerpApiError) else str(failure)
            )
            results.append(
                {"monitor_id": monitor["id"], "ok": False, "summary": message}
            )
    return results


# ---------------------------------------------------------------------------
# overview and status
# ---------------------------------------------------------------------------


def usage_overview(
    connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    """Usage rollups for the overview page (last 30 days, UTC days)."""
    moment = now or store.now_utc()
    today = _day(moment)
    first_day = _day(moment - timedelta(days=29))
    rows = store.usage_since(connection, first_day)
    by_day: dict[str, dict[str, Any]] = {}
    for offset in range(30):
        day = _day(moment - timedelta(days=29 - offset))
        by_day[day] = {"day": day, "searches": 0, "cached": 0, "errors": 0}
    engines_total: dict[str, dict[str, int]] = defaultdict(
        lambda: {"searches": 0, "errors": 0}
    )
    sources: dict[str, int] = defaultdict(int)
    credits = cached = errors = searches = 0
    for row in rows:
        entry = by_day.get(row["day"])
        if entry is None:
            continue
        entry["searches"] += row["searches"]
        entry["cached"] += row["cached"]
        entry["errors"] += row["errors"]
        engines_total[row["engine"]]["searches"] += row["searches"]
        engines_total[row["engine"]]["errors"] += row["errors"]
        sources[row["source"]] += row["searches"]
        credits += row["credits"]
        cached += row["cached"]
        errors += row["errors"]
        searches += row["searches"]
    latency = store.latency_by_engine(
        connection, store.to_iso(moment - timedelta(days=30))
    )
    week_start = _day(moment - timedelta(days=6))
    last_30 = sum(item["searches"] for item in by_day.values())
    all_ms = [value for value in latency.values() if value]
    return {
        "today": by_day[today]["searches"],
        "last_7_days": sum(
            item["searches"] for day, item in by_day.items() if day >= week_start
        ),
        "last_30_days": last_30,
        "by_day": list(by_day.values()),
        "by_engine": [
            {
                "engine": engine_id,
                "name": _engine_name(engine_id),
                "searches": values["searches"],
                "errors": values["errors"],
                "avg_ms": round(latency.get(engine_id, 0)),
            }
            for engine_id, values in sorted(
                engines_total.items(), key=lambda pair: (-pair[1]["searches"], pair[0])
            )[:10]
        ],
        "by_source": [
            {"source": name, "searches": count}
            for name, count in sorted(
                sources.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ],
        "cache_hit_rate": round(cached / searches, 4) if searches else 0,
        "avg_ms": round(sum(all_ms) / len(all_ms)) if all_ms else 0,
        "error_rate": round(errors / searches, 4) if searches else 0,
        "credits_used_30d": credits,
    }


def overview(
    connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    """Everything on the overview page that comes from the local database."""
    moment = now or store.now_utc()
    recent_rows, _ = store.list_searches(connection, limit=10)
    monitors = store.list_monitors(connection)
    enabled = [item for item in monitors if item["enabled"]]
    return {
        "usage": usage_overview(connection, moment),
        "recent": [record_from_row(row) for row in recent_rows],
        "monitors": {
            "active": len(enabled),
            "due_next": min((item["next_run_at"] for item in enabled), default=None),
        },
        "saved": len(store.list_saved(connection)),
        "engines": len(catalog.engines()),
        "categories": catalog.categories(),
    }


def throttle_state(
    connection: sqlite3.Connection, limits: Limits, now: datetime | None = None
) -> dict[str, Any]:
    moment = now or store.now_utc()
    return {
        "hourly_limit": limits.hourly_limit,
        "used_this_hour": store.count_live_since(
            connection,
            store.to_iso(moment - timedelta(hours=1)),
            exclude_engines=catalog.local_engine_ids(),
        ),
    }


def queue_state(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "running": inflight_searches(),
        "async_pending": store.count_status(connection, "running"),
    }


def engines_listing(
    *, category: str = "", family: str = "", text: str = ""
) -> dict[str, Any]:
    found = catalog.search(category=category, family=family, text=text)
    return {
        "engines": [item.summary() for item in found],
        "categories": catalog.categories(),
        "families": catalog.families(),
        "total": len(found),
    }
