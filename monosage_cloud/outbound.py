"""Calls to addresses an organization chose (its identity provider, its own model endpoint), checked when they run.

An address is checked when it is saved, but DNS can change afterwards and ``urlopen`` follows redirects, so a
public address could later lead the server to an internal one. Here every call:

* resolves the host and requires every address it resolves to be public (no private, loopback, link-local,
  reserved or multicast address), then connects to that checked address, so DNS cannot change between the check
  and the connection (the TLS certificate is still verified for the host name);
* never follows a redirect: a 3xx answer is an error;
* reads at most ``max_bytes`` of the answer.

The same rules as webhook deliveries (monosage_cloud/webhooks.py). ``AIRA_CLOUD_ALLOW_LOCAL_URLS=1`` (or the
older ``AIRA_CLOUD_ALLOW_LOCAL_IDP=1``) lets development and tests reach a stand-in on this machine.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class OutboundError(Exception):
    """The address may not be called, could not be reached, or redirected."""


class OutboundHTTPError(OutboundError):
    def __init__(self, status: int, body: bytes = b"") -> None:
        super().__init__(f"The address answered {status}.")
        self.status = status
        self.body = body


@dataclass
class Answer:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


def allow_local() -> bool:
    return (
        os.getenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", "") == "1"
        or os.getenv("AIRA_CLOUD_ALLOW_LOCAL_IDP", "") == "1"
    )


def _public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def resolve(host: str, port: int) -> str:
    """One address to connect to; every address ``host`` resolves to must be public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as error:
        raise OutboundError(f"Could not find {host}.") from error
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise OutboundError(f"Could not find {host}.")
    local_ok = allow_local()
    for text in addresses:
        address = ipaddress.ip_address(text.split("%", 1)[0])
        if not _public(address) and not local_ok:
            raise OutboundError(f"{host} is not a public address.")
    return addresses[0]


def request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    max_bytes: int = 2_000_000,
) -> Answer:
    """Send one request to ``url`` over a connection pinned to its checked address. A 4xx or 5xx answer raises
    :class:`OutboundHTTPError`; a redirect raises :class:`OutboundError`."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in {"https", "http"} or not host or parts.username:
        raise OutboundError("That address cannot be called.")
    if parts.scheme == "http" and not allow_local():
        raise OutboundError("The address must use https.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = resolve(host, port)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    if parts.scheme == "https":
        context_ = ssl.create_default_context()

        class Pinned(http.client.HTTPSConnection):
            def connect(self) -> None:
                raw = socket.create_connection((address, port), timeout=timeout)
                self.sock = context_.wrap_socket(raw, server_hostname=host)

        connection: http.client.HTTPConnection = Pinned(
            host, port, timeout=timeout, context=context_
        )
    else:
        connection = http.client.HTTPConnection(address, port, timeout=timeout)
    try:
        connection.request(
            method.upper(),
            path,
            body=body,
            headers={**(headers or {}), "Host": parts.netloc},
        )
        response = connection.getresponse()
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OutboundError("The answer is too large.")
        answer = Answer(
            response.status, data, {k.lower(): v for k, v in response.getheaders()}
        )
    except (OSError, http.client.HTTPException) as error:
        raise OutboundError(
            f"The address could not be reached: {type(error).__name__}."
        ) from None
    finally:
        connection.close()
    if 300 <= answer.status < 400:
        raise OutboundError("The address redirected; redirects are not followed.")
    if answer.status >= 400:
        raise OutboundHTTPError(answer.status, answer.body)
    return answer


__all__ = ["Answer", "OutboundError", "OutboundHTTPError", "request", "resolve"]
