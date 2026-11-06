"""Structured logging, correlation IDs, request metrics, and redaction."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from typing import Any

_SECRET_RE = re.compile(
    r"(api[_-]?key|token|password|secret|authorization|cookie)(=|:|\s)+([^\s&;,]+)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Capability links: whoever holds the URL holds the access (an export download, an invitation, a public page, a
# password reset or a launch). The segment after one of these prefixes is masked wherever it appears in log text.
CAPABILITY_PREFIXES = (
    "/api/exports/download/",
    "/api/invites/",
    "/api/public/",
    "/p/",
    "#/invite/",
    "#/reset/",
    "#/verify/",
    "#/recovery-cancel/",
)
_CAPABILITY_RE = re.compile(
    "(" + "|".join(re.escape(prefix) for prefix in CAPABILITY_PREFIXES) + r")[^/?#\s\"'&]+"
)
# Query parameters that carry a credential or a one-time code: ``token=`` and ``api_key=`` are covered above.
_QUERY_SECRET_RE = re.compile(
    r"([?&;](?:token|api_key|code|state|sig|signature|key|access_token|refresh_token|client_secret|launch|t)=)[^&#\s\"']+",
    re.IGNORECASE,
)
# Anything shaped like a random capability token in a path (32+ URL-safe characters mixing cases or digits and
# letters, not a 32-character hexadecimal id): a route added later is masked before anyone lists it above.
_LONG_TOKEN_RE = re.compile(r"(?<=/)(?![0-9a-f]{32}(?:[/?#\s]|$))[A-Za-z0-9_-]{32,}")


def _looks_random(segment: str) -> bool:
    kinds = sum(
        (
            any(c.islower() for c in segment),
            any(c.isupper() for c in segment),
            any(c.isdigit() for c in segment),
        )
    )
    return kinds >= 2 and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", segment)


def redact_path(path: str) -> str:
    """A request path (or a URL) with capability tokens in its segments and its query masked."""
    text = _CAPABILITY_RE.sub(lambda m: f"{m.group(1)}[redacted]", path)
    text = _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}[redacted]", text)
    return _LONG_TOKEN_RE.sub(
        lambda m: "[redacted]" if _looks_random(m.group(0)) else m.group(0), text
    )


def redact(text: str) -> str:
    """Mask credential-looking values, capability links and email addresses in log text."""
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    text = redact_path(text)
    return _EMAIL_RE.sub("[email]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key in (
            "correlation_id",
            "org_id",
            "user_id",
            "method",
            "path",
            "status",
            "duration_ms",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact_path(value) if key == "path" else value
        if record.exc_info and record.exc_info[1] is not None:
            payload["error"] = redact(
                f"{type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            )
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # The arguments are part of the message (a request path is one): redact the whole formatted line, traceback
        # included, without changing the record other handlers see.
        base = redact(super().format(record))
        correlation = getattr(record, "correlation_id", None)
        return f"{base} [cid={correlation}]" if correlation else base


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper() if level else "INFO")


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


class Metrics:
    """In-process counters and latency histograms exposed at /api/metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Counter[str] = Counter()
        self._statuses: Counter[int] = Counter()
        self._latency: dict[str, list[float]] = defaultdict(list)
        self._integrations: dict[str, dict[str, Any]] = {}
        self._counters: Counter[str] = Counter()
        self.started_at = time.time()

    def record_request(self, route: str, status: int, duration_ms: float) -> None:
        with self._lock:
            self._requests[route] += 1
            self._statuses[status] += 1
            bucket = self._latency[route]
            bucket.append(duration_ms)
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def integration(self, name: str, ok: bool, message: str = "") -> None:
        with self._lock:
            self._integrations[name] = {
                "ok": ok,
                "message": message[:200],
                "checked_at": int(time.time()),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = {}
            for route, samples in self._latency.items():
                ordered = sorted(samples)
                p95 = (
                    ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
                    if ordered
                    else 0
                )
                routes[route] = {
                    "count": self._requests[route],
                    "avg_ms": round(sum(ordered) / len(ordered), 1) if ordered else 0,
                    "p95_ms": round(p95, 1),
                }
            return {
                "uptime_seconds": int(time.time() - self.started_at),
                "requests_total": sum(self._requests.values()),
                "status_codes": {
                    str(key): value for key, value in sorted(self._statuses.items())
                },
                "routes": routes,
                "counters": dict(self._counters),
                "integrations": dict(self._integrations),
            }


metrics = Metrics()
