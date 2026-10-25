"""A socket-free request and response model, and a shim that runs the local build's handlers against it.

The gateway is a pure function from :class:`HttpRequest` to :class:`HttpResponse`. The HTTP server
only moves bytes. That keeps one code path for the web tier, tests, and a worker that executes a
forwarded request. The shim subclasses the local ``RequestHandler`` so its 171 route methods run
unchanged; it captures what they send instead of writing to a socket.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

SESSION_COOKIE = "aira_session"
OPERATOR_COOKIE = "aira_operator"


@dataclass
class HttpRequest:
    method: str
    path: str  # path only, no query
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased names
    body: bytes = b""
    client_ip: str = "127.0.0.1"
    host: str = ""
    scheme: str = "http"

    @classmethod
    def build(
        cls,
        method: str,
        target: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | dict[str, Any] | None = None,
        client_ip: str = "127.0.0.1",
    ) -> HttpRequest:
        parts = urlsplit(target)
        lowered = {k.lower(): v for k, v in (headers or {}).items()}
        if isinstance(body, dict):
            payload = json.dumps(body).encode("utf-8")
            lowered.setdefault("content-type", "application/json")
        else:
            payload = body or b""
        return cls(
            method.upper(),
            parts.path,
            parse_qs(parts.query),
            lowered,
            payload,
            client_ip,
            lowered.get("host", ""),
        )

    def headers_object(self) -> Any:
        """The headers as an object with a case-insensitive ``get`` (what signature checks expect)."""
        outer = self.headers

        class _Headers:
            def get(self, name: str, default: Any = "") -> Any:
                return outer.get(name.lower(), default)

        return _Headers()

    def cookie(self, name: str) -> str:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("cookie", ""))
        except Exception:  # noqa: BLE001 - a malformed cookie header is just "no cookie"
            return ""
        morsel = jar.get(name)
        return morsel.value if morsel else ""

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        if (
            self.headers.get("content-type", "")
            .lower()
            .startswith("application/x-www-form-urlencoded")
        ):
            # a form post (an identity provider's SAML response): one value per name
            return {
                name: values[0]
                for name, values in parse_qs(
                    self.body.decode("utf-8", "replace"), keep_blank_values=True
                ).items()
            }
        try:
            value = json.loads(self.body)
        except RecursionError:
            raise ValueError("Request body is nested too deeply.") from None
        except ValueError:
            raise ValueError("Request body must be valid JSON.") from None
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def q(self, name: str, default: str = "") -> str:
        return str(self.query.get(name, [default])[0]).strip()

    def qint(self, name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self.query.get(name, [str(default)])[0])
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a whole number.") from None
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}.")
        return value


@dataclass
class HttpResponse:
    status: int = 200
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""

    def header(self, name: str) -> str:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return ""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


@dataclass
class Reply:
    """What a control-plane handler returns when a bare ``(status, dict)`` is not enough."""

    status: int = 200
    body: bytes | dict[str, Any] | list[Any] = b""
    content_type: str = "application/json; charset=utf-8"
    cookies: list[str] = field(default_factory=list)  # full Set-Cookie header values
    headers: list[tuple[str, str]] = field(default_factory=list)
    filename: str = ""

    def to_response(self) -> HttpResponse:
        body = (
            self.body
            if isinstance(self.body, bytes)
            else json.dumps(self.body, ensure_ascii=False, default=str).encode("utf-8")
        )
        headers = [("Content-Type", self.content_type), *self.headers]
        headers += [("Set-Cookie", cookie) for cookie in self.cookies]
        if self.filename:
            headers.append(
                ("Content-Disposition", f'attachment; filename="{self.filename}"')
            )
        return HttpResponse(self.status, headers, body)


def cookie_header(
    name: str, value: str, *, max_age: int, secure: bool, path: str = "/"
) -> str:
    parts = [
        f"{name}={value}",
        "HttpOnly",
        "SameSite=Strict",
        f"Path={path}",
        f"Max-Age={max_age}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# --------------------------------------------------------------------------- route table


@dataclass(frozen=True)
class CloudRoute:
    method: str
    pattern: re.Pattern[str]
    handler: Callable[..., Any]
    name: str
    auth: str  # public | member | operator | scim
    # states in which the route still works: a read-only organization can still export and pay
    writes: bool = False
    verified: bool = True  # False: usable before the email is verified
    mfa: bool = True  # False: usable before the second factor is given (sign-in itself, MFA setup)
    csrf: bool = True  # False for provider webhooks and token-authenticated callers


CLOUD_ROUTES: list[CloudRoute] = []


def cloud_route(
    method: str,
    pattern: str,
    *,
    auth: str = "member",
    writes: bool = False,
    verified: bool = True,
    mfa: bool = True,
    csrf: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def wrap(function: Callable[..., Any]) -> Callable[..., Any]:
        CLOUD_ROUTES.append(
            CloudRoute(
                method.upper(),
                re.compile(pattern),
                function,
                getattr(function, "__name__", ""),
                auth,
                writes,
                verified,
                mfa,
                csrf,
            )
        )
        return function

    return wrap


@dataclass
class Req:
    """What a control-plane handler receives."""

    app: Any
    request: HttpRequest
    match: re.Match[str]
    principal: Any = None
    correlation_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        return self.payload

    def group(self, index: int = 1) -> str:
        return self.match.group(index)

    @property
    def query(self) -> dict[str, list[str]]:
        return self.request.query

    def q(self, name: str, default: str = "") -> str:
        return self.request.q(name, default)

    def qint(self, name: str, default: int, low: int, high: int) -> int:
        return self.request.qint(name, default, low, high)


# --------------------------------------------------------------------------- local handler shim


class Captured:
    def __init__(self) -> None:
        self.status = 200
        self.headers: list[tuple[str, str]] = []
        self.body = b""
        self.sent = False


def make_shim(base: Any) -> Any:
    """A subclass of the local ``RequestHandler`` that captures its output instead of using a socket."""

    class LocalShim(base):  # base is the local build's RequestHandler
        def __init__(
            self,
            request: HttpRequest,
            captured: Captured,
            transform: Callable[[int, dict[str, Any]], dict[str, Any]] | None = None,
        ) -> None:  # noqa: D107 - the socket setup is deliberately skipped
            message = Message()
            for name, value in request.headers.items():
                message[name] = value
            self.headers = message
            self.command = request.method
            self.path = request.path
            self.client_address = (request.client_ip, 0)
            self.wfile = io.BytesIO()
            self._captured = captured
            self._transform = transform
            self._status = 200

        def _write_headers(
            self,
            status: int,
            content_type: str,
            length: int,
            extra: list[tuple[str, str]] | None = None,
        ) -> None:
            self._captured.status = status
            self._captured.headers = [("Content-Type", content_type), *(extra or [])]
            self._captured.sent = True

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            if self._transform is not None:
                payload = self._transform(status, payload)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._captured.status = status
            self._captured.headers = [
                ("Content-Type", "application/json; charset=utf-8")
            ]
            self._captured.body = body
            self._captured.sent = True
            self._status = status

        def _send_bytes(
            self,
            status: int,
            data: bytes,
            content_type: str,
            *,
            filename: str | None = None,
        ) -> None:
            self._captured.status = status
            self._captured.headers = [("Content-Type", content_type)]
            if filename:
                self._captured.headers.append(
                    ("Content-Disposition", f'attachment; filename="{filename}"')
                )
            self._captured.body = data
            self._captured.sent = True
            self._status = status

        def _client_key(self) -> str:
            return str(self.client_address[0])

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:  # noqa: ARG002
            self._send_json(code, {"error": message or "Error"})

        def finish_capture(self) -> None:
            """Handlers that stream with ``wfile`` (the API's raw responses) are collected here."""
            data = self.wfile.getvalue()
            if data and not self._captured.body:
                self._captured.body = data

    return LocalShim


__all__ = [
    "HttpRequest",
    "HttpResponse",
    "Reply",
    "Req",
    "CloudRoute",
    "CLOUD_ROUTES",
    "cloud_route",
    "cookie_header",
    "make_shim",
    "Captured",
    "SESSION_COOKIE",
    "OPERATOR_COOKIE",
    "Iterable",
]
