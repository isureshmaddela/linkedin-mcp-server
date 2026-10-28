"""A tiny, framework-neutral request layer for the API routes.

Route handlers are plain functions ``handler(request) -> (status, body)`` or
``-> Raw`` (a download). They are registered with :func:`aira_route` and the
HTTP server module installs every entry of :data:`AIRA_ROUTES` into its own
route table, building a :class:`Request` for each call. Keeping handlers free
of the server module makes them testable and keeps ``auto_apply_assistant.py``
small. Any module may register routes; the server calls
:func:`load_route_modules`, which imports every module in :data:`ROUTE_MODULES`.

Error contract (the server turns these into its standard ``{"error": ...}``
envelope): ``ValueError`` is 400, ``LookupError`` 404, ``PermissionError`` 403,
``RuntimeError`` 502 and :class:`aira.airaapi.client.SerpApiError` carries its
own status and a machine-readable ``code``.
"""

from __future__ import annotations

import importlib
import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import unquote_plus

from aira.airaapi.store import SecretStore
from aira.observability import redact

# "public_link": no sign-in; the first group of the route pattern is a public link token, which names the
# account whose database the handler opens (see aira.suite.public).
AuthMode = Literal["session", "api_key", "api_key_optional", "none", "public_link"]


class Headers(Protocol):
    """Anything with a case-insensitive ``get`` (``http.client.HTTPMessage``, a dict)."""

    def get(self, name: str, default: Any = None, /) -> Any: ...


class KeyIndex(Protocol):
    """Maps API key hashes to the owning account (kept in the accounts database)."""

    def add(self, key_hash: str) -> None: ...

    def remove(self, key_hash: str) -> None: ...


def _no_audit(
    action: str, target_type: str = "", target_id: str = "", detail: Any = None
) -> None:
    """Default audit hook: do nothing (tests and the monitor scheduler)."""


@dataclass(slots=True)
class Request:
    match: re.Match[str]
    payload: dict[str, Any]
    query: dict[str, list[str]]
    user: dict[str, Any] | None
    headers: Headers
    client: str
    connect: Callable[[], sqlite3.Connection]
    setting: Callable[[str], str]
    secret_store: SecretStore
    key_index: KeyIndex
    api_key: Mapping[str, Any] | None = None
    audit: Callable[..., None] = _no_audit

    def q(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return str(values[0]).strip() if values else default

    def qint(self, name: str, default: int, low: int, high: int) -> int:
        text = self.q(name, "")
        if text == "":
            return default
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{name} must be a whole number.") from None
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}.")
        return value

    def qbool(self, name: str) -> bool | None:
        text = self.q(name, "").lower()
        if text == "":
            return None
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{name} must be true or false.")

    def header(self, name: str, default: str = "") -> str:
        return str(self.headers.get(name, default) or default)

    def base_url(self) -> str:
        """Origin the caller used, for generated code samples (loopback by default)."""
        host = self.header("Host")
        if host and re.fullmatch(
            r"[A-Za-z0-9.\-]+(:\d{1,5})?|\[[0-9A-Fa-f:]+\](:\d{1,5})?", host
        ):
            return f"http://{host}"
        return "http://127.0.0.1:8765"


@dataclass(slots=True)
class Raw:
    """A non-JSON response, typically a file download."""

    status: int
    body: bytes
    content_type: str
    filename: str | None = None
    headers: tuple[tuple[str, str], ...] = ()


# Pages SerpApi saved from a search engine are untrusted HTML. Whatever opens
# one directly (a browser tab pointed at /searches/{id}.html) must treat it as
# an inert document: no scripts, no forms, no access to Aira's origin.
UNTRUSTED_PAGE_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", "sandbox; form-action 'none'; base-uri 'none'"),
)


def extra_headers(outcome: Raw) -> tuple[tuple[str, str], ...]:
    """Headers to add to a :class:`Raw` response (the route's own plus page safety)."""
    kind = outcome.content_type.split(";", 1)[0].strip().lower()
    if kind in {"text/html", "application/xhtml+xml", "image/svg+xml"}:
        return (*UNTRUSTED_PAGE_HEADERS, *outcome.headers)
    return outcome.headers


ALLOWED_HOSTS_ENV = "AIRA_ALLOWED_HOSTS"
_HOST_RE = re.compile(r"^(?:\[([0-9A-Fa-f:.]+)\]|([A-Za-z0-9.-]+))(?::\d{1,5})?$")


def host_is_local(host: str, extra: str = "") -> bool:
    """Whether a ``Host`` header names this machine's loopback interface.

    Aira binds 127.0.0.1, so a request whose Host is some other name reached it
    through DNS rebinding (a web page whose domain was re-pointed at 127.0.0.1)
    or an unsupported proxy. Same-origin checks cannot catch rebinding because
    the page really is "same-origin" with itself. ``extra`` (or the
    ``AIRA_ALLOWED_HOSTS`` environment variable) is a comma-separated list of
    further host names to accept.
    """
    if not host:
        return True  # HTTP/1.0 clients; browsers always send Host
    found = _HOST_RE.match(host.strip())
    if not found:
        return False
    name = (found.group(1) or found.group(2) or "").lower().rstrip(".")
    if name in {"localhost", "::1"} or name.endswith(".localhost"):
        return True
    parts = name.split(".")
    if len(parts) == 4 and all(part.isdigit() and int(part) < 256 for part in parts):
        return parts[0] == "127"
    allowed = {
        item.strip().lower()
        for item in (extra or os.environ.get(ALLOWED_HOSTS_ENV, "")).split(",")
        if item.strip()
    }
    return name in allowed


_ENCODED_KEY_RE = re.compile(r"aira_[A-Za-z0-9_-]{12,}")


def scrub_request_line(text: str) -> str:
    """Redact credentials in an access-log line however the query string spells them.

    ``api_key`` can arrive percent-encoded (``api%5Fkey=``, ``%61pi_key=``) and
    the generic redactor only sees the raw spelling, so decode first, then mask
    both ``api_key=`` style assignments and anything shaped like an Aira key.
    """
    decoded = unquote_plus(text)
    return redact(_ENCODED_KEY_RE.sub("aira_[redacted]", decoded))


Handler = Callable[[Request], "tuple[int, dict[str, Any] | list[Any]] | Raw"]


@dataclass(frozen=True, slots=True)
class AiraRoute:
    method: str
    pattern: re.Pattern[str]
    handler: Handler
    auth: AuthMode
    attr: str


AIRA_ROUTES: list[AiraRoute] = []


def aira_route(
    method: str, pattern: str, auth: AuthMode = "session"
) -> Callable[[Handler], Handler]:
    """Register ``handler`` for ``method`` and a full-match regex ``pattern``."""

    def wrap(handler: Handler) -> Handler:
        name = str(getattr(handler, "__name__", "handler"))
        attr = f"airaapi__{handler.__module__.rsplit('.', 1)[-1]}__{name}"
        if any(item.attr == attr for item in AIRA_ROUTES):
            raise RuntimeError(f"Duplicate Monosage route handler {attr}.")
        AIRA_ROUTES.append(
            AiraRoute(method.upper(), re.compile(pattern), handler, auth, attr)
        )
        return handler

    return wrap


# Modules whose import registers routes. The first is required; later ones are
# optional feature modules (for example the Careers API) that may not exist yet.
ROUTE_MODULES = (
    "aira.airaapi.routes",
    "aira.airaapi.careers_routes",
    "aira.airaapi.leads_routes",
)


def load_route_modules() -> None:
    """Import every route module so its ``@aira_route`` handlers are registered."""
    for index, name in enumerate(ROUTE_MODULES):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as error:
            if index == 0 or error.name != name:
                raise
    from aira import suite

    suite.load_apps()  # the industry apps and the Business hub
