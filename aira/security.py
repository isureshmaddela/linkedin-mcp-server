"""Security helpers: login rate limiting, security headers, CSRF checks."""

from __future__ import annotations

import threading
import time
from collections import deque

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)


class RateLimiter:
    """Sliding-window limiter keyed by an identifier (IP or account)."""

    def __init__(
        self, *, limit: int, window_seconds: int, lockout_seconds: int = 0
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._events: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(
        self, key: str, *, now: float | None = None, limit: int | None = None
    ) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        ``limit`` overrides the limiter's own for this call only: callers shared between threads pass the
        limit that applies to *their* key instead of changing the limiter under everyone else.
        """
        current = now if now is not None else time.monotonic()
        cap = self.limit if limit is None else limit
        with self._lock:
            locked = self._locked_until.get(key, 0)
            if locked > current:
                return False, int(locked - current) + 1
            events = self._events.setdefault(key, deque())
            while events and events[0] <= current - self.window:
                events.popleft()
            if len(events) >= cap:
                if self.lockout:
                    self._locked_until[key] = current + self.lockout
                    return False, self.lockout
                return False, int(self.window - (current - events[0])) + 1
            return True, 0

    def hit(self, key: str, *, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        with self._lock:
            self._events.setdefault(key, deque()).append(current)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._locked_until.pop(key, None)


def same_origin(origin: str | None, host: str) -> bool:
    """Reject cross-site state changes: Origin, when present, must match Host."""
    if not origin:
        return True
    from urllib.parse import urlsplit

    return urlsplit(origin).netloc.lower() == host.lower()


def requires_custom_header(headers: dict[str, str]) -> bool:
    """Browsers cannot send this header cross-site without a CORS preflight."""
    return headers.get("X-Requested-With", "").lower() == "aira"
