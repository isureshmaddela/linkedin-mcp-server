"""HTTP client for the documented SerpApi endpoints (urllib only).

Responsibilities and guarantees:

* ``search`` / ``account`` / ``locations`` / ``archive`` / ``status`` map to
  ``/search.json``, ``/account.json``, ``/locations.json``,
  ``/searches/{id}.json|.html`` and a free reachability probe.
* One retry on network errors and 5xx responses, never on 4xx.
* Redirects are refused: the API key travels in the query string (the only
  place SerpApi accepts it), so it must never follow a redirect to another host.
* Every error that leaves this module is a :class:`SerpApiError` whose message
  has the key removed, and the key is also scrubbed from response bodies
  before they are handed back.
* Plain ``http`` is accepted only for loopback hosts (the stand-in server used
  by tests and demos, selected with ``AIRA_SERPAPI_BASE_URL``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

logger = logging.getLogger("aira.airaapi.client")

DEFAULT_BASE_URL = "https://serpapi.com"
BASE_URL_ENV = "AIRA_SERPAPI_BASE_URL"
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
RETRY_DELAY_SECONDS = 0.25
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_SEARCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_KEY_IN_URL_RE = re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE)
OUTPUTS = ("json", "html", "md")
ACCOUNT_FIELDS = (
    "account_id",
    "account_email",
    "account_status",
    "plan_id",
    "plan_name",
    "plan_monthly_price",
    "plan_renewal_date",
    "searches_per_month",
    "plan_searches_left",
    "extra_credits",
    "total_searches_left",
    "this_month_usage",
    "this_hour_searches",
    "last_hour_searches",
)
# Response status Aira answers with, by error code.
STATUS_FOR_CODE = {
    "bad_request": 400,
    "invalid_api_key": 401,
    "invalid_aira_key": 401,
    "out_of_searches": 402,
    "forbidden": 403,
    "not_found": 404,
    "not_configured": 409,
    "conflict": 409,
    "gone": 410,
    "rate_limited": 429,
    "local_rate_limit": 429,
    "busy": 429,
}
_sleep = time.sleep


class SerpApiError(RuntimeError):
    """A failed SerpApi call, mapped to a stable code and an Aira response status."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.record_id: str | None = None

    @property
    def response_status(self) -> int:
        return STATUS_FOR_CODE.get(self.code, 502)

    def status_for(self, auth: str) -> int:
        """Status to answer with, given how the caller authenticated.

        A rejected SerpApi key must never surface as 401 on a signed-in
        console route (the browser app treats 401 as "session ended") nor on
        the compatible ``/v1`` API (where 401 means the *Aira* key is bad).
        """
        status = self.response_status
        if self.code == "invalid_api_key":
            return 422 if auth == "session" else 502
        return status

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.record_id:
            body["record_id"] = self.record_id
        return body


@dataclass(slots=True)
class RawResponse:
    """What the API returned for one search, before Aira stores it."""

    status: int
    body: dict[str, Any] | None
    text: str | None
    serpapi_id: str
    duration_ms: int
    note: str = ""
    queued: bool = False


def scrub(text: str, api_key: str | None) -> str:
    """Remove the API key (raw and URL-encoded) and ``api_key=`` query values."""
    if api_key:
        text = text.replace(api_key, "[redacted]").replace(
            quote(api_key, safe=""), "[redacted]"
        )
    return _KEY_IN_URL_RE.sub(r"\1[redacted]", text)


def scrub_value(value: Any, api_key: str | None) -> Any:
    """Return ``value`` with the key removed from every string inside it."""
    if isinstance(value, str):
        return scrub(value, api_key) if ("api_key" in value or api_key) else value
    if isinstance(value, list):
        return [scrub_value(item, api_key) for item in value]
    if isinstance(value, dict):
        return {key: scrub_value(item, api_key) for key, item in value.items()}
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def map_error(status: int, message: str) -> SerpApiError:
    """Translate an HTTP status and message into a :class:`SerpApiError`."""
    low = message.lower()
    if status == 401 or "invalid api key" in low:
        return SerpApiError("invalid_api_key", message, status)
    if status == 402 or "run out of searches" in low or "out of searches" in low:
        return SerpApiError("out_of_searches", message, status)
    if status == 429:
        return SerpApiError("rate_limited", message, status, retryable=True)
    if status == 403:
        return SerpApiError("forbidden", message, status)
    if status == 404:
        return SerpApiError("not_found", message, status)
    if status == 410:
        return SerpApiError("gone", message, status)
    if status in (400, 422):
        return SerpApiError("bad_request", message, status)
    return SerpApiError("upstream_error", message, status, retryable=status >= 500)


def normalize_account(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the documented account fields. The raw reply echoes the API key; drop it."""
    account = {name: raw.get(name) for name in ACCOUNT_FIELDS}
    account["hourly_rate_limit"] = raw.get(
        "account_rate_limit_per_hour", raw.get("hourly_rate_limit")
    )
    return account


def normalize_location(raw: Mapping[str, Any]) -> dict[str, Any]:
    gps = raw.get("gps")
    return {
        "id": str(raw.get("id", "")),
        "google_id": raw.get("google_id"),
        "google_parent_id": raw.get("google_parent_id"),
        "name": str(raw.get("name", "")),
        "canonical_name": str(raw.get("canonical_name", "")),
        "country_code": str(raw.get("country_code", "")),
        "target_type": str(raw.get("target_type", "")),
        "reach": raw.get("reach"),
        "gps": list(gps) if isinstance(gps, (list, tuple)) and len(gps) == 2 else None,
    }


def resolve_base_url(base_url: str | None = None) -> str:
    value = (base_url or os.getenv(BASE_URL_ENV, "") or DEFAULT_BASE_URL).strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("The SerpApi base URL must be an http(s) URL.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The SerpApi base URL must not carry credentials or a query.")
    if parts.scheme == "http" and parts.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("Plain http is only allowed for loopback SerpApi stand-ins.")
    return value.rstrip("/")


class SerpApiClient:
    """Talk to SerpApi (or the stand-in named by ``AIRA_SERPAPI_BASE_URL``)."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None = None,
        timeout: float = 60,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self.base_url = resolve_base_url(base_url)
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    # -- transport ------------------------------------------------------------

    def _clean(self, text: str) -> str:
        return scrub(text, self._api_key)

    def _require_key(self) -> str:
        if not self._api_key:
            raise SerpApiError(
                "not_configured", "No SerpApi key is configured.", retryable=False
            )
        return self._api_key

    def _fetch(
        self, path: str, query: Mapping[str, str], *, with_key: bool
    ) -> tuple[int, str, str]:
        """GET ``path``; return (status, content type, body text) or raise."""
        parameters = dict(query)
        if with_key:
            parameters["api_key"] = self._require_key()
        url = f"{self.base_url}{path}?{urlencode(parameters)}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Aira/1.0", "Accept": "application/json, text/*"},
        )
        last: SerpApiError | None = None
        for attempt in (1, 2):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise SerpApiError(
                            "upstream_error",
                            "The SerpApi response is larger than 25 MB.",
                            response.status,
                        )
                    kind = response.headers.get("Content-Type", "")
                    return response.status, kind, raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                text = error.read(65536).decode("utf-8", errors="replace")
                failure = map_error(
                    error.code, self._clean(self._error_message(text, error.code))
                )
                if (
                    failure.retryable
                    and failure.http_status
                    and failure.http_status >= 500
                    and attempt == 1
                ):
                    last = failure
                    _sleep(RETRY_DELAY_SECONDS)
                    continue
                raise failure from None
            except OSError as error:
                reason = getattr(error, "reason", error)
                timed_out = isinstance(reason, TimeoutError)
                last = SerpApiError(
                    "timeout" if timed_out else "network_error",
                    "SerpApi did not answer in time."
                    if timed_out
                    else f"Could not reach SerpApi ({type(reason).__name__}).",
                    retryable=True,
                )
                logger.warning("SerpApi request failed: %s", last.message)
                if attempt == 1:
                    _sleep(RETRY_DELAY_SECONDS)
                    continue
                raise last from None
        raise last or SerpApiError("upstream_error", "SerpApi request failed.")

    @staticmethod
    def _error_message(text: str, status: int) -> str:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])[:500]
        return text.strip()[:200] or f"SerpApi answered HTTP {status}."

    def _json(self, text: str, status: int) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise SerpApiError(
                "upstream_error",
                "SerpApi returned a response that is not JSON.",
                status,
            ) from None
        except RecursionError:
            raise SerpApiError(
                "upstream_error",
                "SerpApi returned a response nested too deeply to read.",
                status,
            ) from None

    def _scrubbed(self, data: Any, status: int) -> Any:
        """``data`` with the key removed; absurdly nested bodies are refused, not crashed on."""
        try:
            return scrub_value(data, self._api_key)
        except RecursionError:
            raise SerpApiError(
                "upstream_error",
                "SerpApi returned a response nested too deeply to read.",
                status,
            ) from None

    # -- endpoints --------------------------------------------------------------

    def search(
        self,
        engine: str,
        params: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> RawResponse:
        """Run one search. ``options``: output, async, no_cache, zero_trace, json_restrictor."""
        opts = dict(options or {})
        output = str(opts.get("output") or "json")
        if output not in OUTPUTS:
            raise SerpApiError("bad_request", f"Unsupported output {output!r}.")
        query: dict[str, str] = {}
        for name, value in params.items():
            query[str(name)] = _wire(value)
        query["engine"] = engine
        if output != "json":
            query["output"] = output
        for flag in ("async", "no_cache", "zero_trace"):
            if opts.get(flag):
                query[flag] = "true"
        restrictor = str(opts.get("json_restrictor") or "").strip()
        if restrictor and output == "json":
            query["json_restrictor"] = restrictor
        started = time.monotonic()
        status, _kind, text = self._fetch("/search.json", query, with_key=True)
        duration = int((time.monotonic() - started) * 1000)
        text = self._clean(text)
        if output != "json":
            return RawResponse(status, None, text, "", duration)
        data = self._json(text, status)
        if not isinstance(data, dict):
            raise SerpApiError(
                "upstream_error", "SerpApi returned an unexpected response.", status
            )
        metadata = data.get("search_metadata")
        meta = metadata if isinstance(metadata, dict) else {}
        state = str(meta.get("status", "")).lower()
        message = str(data.get("error", ""))
        if state == "error":
            raise SerpApiError(
                "search_failed",
                self._clean(message) or "SerpApi could not complete this search.",
                status,
                retryable=True,
            )
        if message and not meta:
            raise map_error(status, self._clean(message))
        return RawResponse(
            status,
            self._scrubbed(data, status),
            None,
            str(meta.get("id", "")),
            duration,
            note=self._clean(message),
            queued=state in {"queued", "processing"},
        )

    def archive(self, search_id: str, fmt: str = "json") -> RawResponse:
        """Fetch a finished (or in-flight) search from the Search Archive API. Free."""
        if not _SEARCH_ID_RE.match(search_id or ""):
            raise SerpApiError("bad_request", "That is not a valid SerpApi search id.")
        if fmt not in {"json", "html", "json_with_pixel_position"}:
            raise SerpApiError(
                "bad_request",
                "Archive format must be json, html or json_with_pixel_position.",
            )
        started = time.monotonic()
        status, _kind, text = self._fetch(
            f"/searches/{search_id}.{fmt}", {}, with_key=True
        )
        duration = int((time.monotonic() - started) * 1000)
        text = self._clean(text)
        if fmt == "html":
            return RawResponse(status, None, text, search_id, duration)
        data = self._json(text, status)
        if not isinstance(data, dict):
            raise SerpApiError(
                "upstream_error", "SerpApi returned an unexpected response.", status
            )
        metadata = data.get("search_metadata")
        meta = metadata if isinstance(metadata, dict) else {}
        state = str(meta.get("status", "")).lower()
        return RawResponse(
            status,
            self._scrubbed(data, status),
            None,
            str(meta.get("id", search_id)),
            duration,
            note=self._clean(str(data.get("error", ""))),
            queued=state in {"queued", "processing"},
        )

    def account(self) -> dict[str, Any]:
        """Plan, usage and rate limit from ``account.json`` (free, never includes the key)."""
        status, _kind, text = self._fetch("/account.json", {}, with_key=True)
        data = self._json(self._clean(text), status)
        if not isinstance(data, dict):
            raise SerpApiError(
                "upstream_error", "SerpApi returned an unexpected response.", status
            )
        if (
            data.get("error")
            and "account_email" not in data
            and "plan_name" not in data
        ):
            raise map_error(status, self._clean(str(data["error"])))
        return normalize_account(data)

    def locations(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search Google locations. Free and needs no key."""
        query = {"q": q, "limit": str(max(1, min(int(limit), 10)))}
        status, _kind, text = self._fetch("/locations.json", query, with_key=False)
        data = self._json(text, status)
        if isinstance(data, dict) and data.get("error"):
            raise map_error(status, str(data["error"]))
        if not isinstance(data, list):
            raise SerpApiError(
                "upstream_error",
                "SerpApi returned an unexpected locations reply.",
                status,
            )
        return [normalize_location(item) for item in data if isinstance(item, dict)]

    def status(self) -> dict[str, Any]:
        """Probe reachability with the free locations endpoint. Never raises."""
        started = time.monotonic()
        try:
            self._fetch("/locations.json", {"q": "a", "limit": "1"}, with_key=False)
            return {
                "reachable": True,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "checked_at": _utc_now(),
                "error": None,
            }
        except SerpApiError as error:
            return {
                "reachable": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "checked_at": _utc_now(),
                "error": error.message,
            }


def _wire(value: Any) -> str:
    """Serialise one parameter value the way SerpApi expects it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(_wire(item) for item in value)
    return str(value)
