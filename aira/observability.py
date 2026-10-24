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


def redact(text: str) -> str:
    """Mask credential-looking values and email addresses in log text."""
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
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
                payload[key] = value
        if record.exc_info and record.exc_info[1] is not None:
            payload["error"] = redact(
                f"{type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            )
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact(str(record.msg))
        base = super().format(record)
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
