"""Reading a file from an address a business pasted (a staff member's private calendar feed), safely.

The same rules as the News feed reader (aira/suite/news/feeds.py) and the Leads website reader
(aira/leads/enrich.py), for any app:

* https only (``webcal://``, what calendar apps hand out, is read as https);
* the host must resolve to public internet addresses only (no loopback, private, link-local, reserved or multicast
  address), and the connection goes to the address that was checked, so DNS cannot change in between (the TLS
  certificate is still verified for the host name);
* at most :data:`MAX_REDIRECTS` redirects, each one checked again by the same rules (a redirect to a private host
  or to plain http is refused);
* a size cap and a time cap.

``AIRA_SUITE_FETCH_ALLOW_LOCAL=1`` (tests only) also allows plain http to the loopback address, never to a site on
the internet.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

ALLOW_LOCAL_ENV = (
    "AIRA_SUITE_FETCH_ALLOW_LOCAL"  # tests only: plain http to the loopback address
)
USER_AGENT = "Monosage/1.0 (+calendar reader)"
TIMEOUT = 10.0
MAX_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 3


class FetchError(Exception):
    """The address may not be read, or could not be read (the reason is shown to the person)."""


def _allow_local() -> bool:
    return os.environ.get(ALLOW_LOCAL_ENV, "") == "1"


def normalise(url: str) -> str:
    """``webcal://`` (and ``webcals://``) as ``https://``; anything else unchanged."""
    text = str(url or "").strip()
    lowered = text.lower()
    for prefix in ("webcals://", "webcal://"):
        if lowered.startswith(prefix):
            return "https://" + text[len(prefix) :]
    return text


def check_url(url: str) -> str:
    """The address as it will be read, or FetchError when it never could be (not https, a user name in it)."""
    text = normalise(url)
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if (
        scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise FetchError("The address must start with https://.")
    if scheme == "http" and not _allow_local():
        raise FetchError("The address must start with https://.")
    if len(text) > 2000:
        raise FetchError("That address is too long.")
    return text


def _check_addresses(host: str, port: int) -> str:
    """Resolve ``host``; refuse anything that is not a public address (loopback only with the test switch)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise FetchError(f"{host} could not be found.") from None
    allowed: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_global or (ip.is_loopback and _allow_local()):
            allowed.append(address)
        else:
            raise FetchError(f"{host} is not a public address.")
    if not allowed:
        raise FetchError(f"{host} could not be found.")
    return allowed[0]


def _open(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str, str]:
    text = check_url(url)
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    address = _check_addresses(host, port)
    if scheme == "http" and not ipaddress.ip_address(address).is_loopback:
        # the test switch allows plain http to this computer only
        raise FetchError("The address must start with https://.")
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
    except OSError as error:
        raise FetchError(f"{host} did not answer ({type(error).__name__}).") from None
    if scheme == "https":
        context = ssl.create_default_context()
        try:
            sock = context.wrap_socket(sock, server_hostname=host)
        except (ssl.SSLError, OSError) as error:
            sock.close()
            raise FetchError(
                f"{host} has no valid HTTPS certificate ({type(error).__name__})."
            ) from None
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    connection.sock = sock
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return connection, host, target


def fetch(
    url: str,
    *,
    accept: str = "*/*",
    max_bytes: int = MAX_BYTES,
    timeout: float = TIMEOUT,
) -> bytes:
    """GET ``url`` by the rules above; the body. FetchError says why not."""
    current = check_url(url)
    deadline = time.monotonic() + timeout * 2
    for _ in range(MAX_REDIRECTS + 1):
        connection, host, target = _open(current, timeout)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Host": urlsplit(current).netloc,
                    "User-Agent": USER_AGENT,
                    "Accept": accept,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location") or ""
                if not location:
                    raise FetchError(f"{host} redirected without saying where.")
                current = check_url(urljoin(current, location))
                continue
            if response.status >= 400:
                raise FetchError(
                    f"{host} answered {response.status}"
                    + (
                        ": the address may have been reset. Copy it again."
                        if response.status in (401, 403, 404)
                        else "."
                    )
                )
            chunks: list[bytes] = []
            size = 0
            while (
                True
            ):  # in pieces, so a server that answers slowly still stops at the time cap
                piece = response.read(65536)
                if not piece:
                    break
                size += len(piece)
                if size > max_bytes:
                    raise FetchError("That file is too large to read.")
                if time.monotonic() > deadline:
                    raise FetchError(f"{host} took too long.")
                chunks.append(piece)
            return b"".join(chunks)
        except (OSError, http.client.HTTPException) as error:
            raise FetchError(
                f"{host} did not answer ({type(error).__name__})."
            ) from None
        finally:
            connection.close()
        if time.monotonic() > deadline:
            raise FetchError(f"{host} took too long.")
    raise FetchError("The address redirected too many times.")


__all__ = ["FetchError", "check_url", "fetch", "normalise"]
