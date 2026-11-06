"""Web Push: the server's VAPID identity (RFC 8292), message encryption (RFC 8291, ``aes128gcm`` from RFC 8188)
and one delivery to a browser's push service (RFC 8030).

The VAPID key pair is made once and kept in a file (the single-user build's data directory, the hosted build's
platform secrets directory; :func:`configure`). Browsers subscribe with its public key, so a new key would orphan
every subscription: it is never rotated on its own.

Endpoints come from browsers, so the server only ever posts to the push services browsers use (Google, Mozilla,
Microsoft, Apple) over https, and never follows a redirect: a subscription cannot point the server at an internal
address. Tests add their own stub with :data:`EXTRA_HOSTS`.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_FILE = "vapid_private.pem"
RECORD_SIZE = 4096
MAX_PAYLOAD = 3000  # push services take about 4 KB of encrypted body
DEFAULT_TTL = 24 * 3600
URGENCIES = ("very-low", "low", "normal", "high")
# The push services of the browsers people use. A subscription naming any other host is refused when saved and
# never posted to.
PUSH_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    ".notify.windows.com",
    "web.push.apple.com",
    ".push.apple.com",
)
# host[:port] values tests allow over plain http (a local stub push service); empty in production
EXTRA_HOSTS: set[str] = set()

_key_dir: Callable[[], Path] | None = None
_subject: Callable[[], str] | None = None
_key_lock = threading.Lock()
_cached: tuple[str, ec.EllipticCurvePrivateKey] | None = None


def configure(
    *,
    key_dir: Callable[[], Path] | None = None,
    subject: Callable[[], str] | None = None,
) -> None:
    """Where the key file lives and the contact (``mailto:`` or ``https:``) push services see in the token."""
    global _key_dir, _subject, _cached
    if key_dir is not None:
        _key_dir = key_dir
        _cached = None
    if subject is not None:
        _subject = subject


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    raw = str(text or "").strip().encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def _hosted(name: str) -> Any:
    """A hosting shell's own choice (aira.hosting.HOOKS.notify), or None."""
    from aira.hosting import HOOKS

    return (HOOKS.notify or {}).get(name)


def _key_path() -> Path:
    chosen = _hosted("key_dir") or _key_dir
    base = (
        chosen() if chosen is not None else Path(os.environ.get("AIRA_DATA_DIR") or ".")
    )
    return Path(base) / KEY_FILE


def private_key() -> ec.EllipticCurvePrivateKey:
    """The server's VAPID key, made on first use and kept in the key file (owner-only permissions)."""
    global _cached
    path = _key_path()
    with _key_lock:
        if _cached is not None and _cached[0] == str(path):
            return _cached[1]
        if path.is_file():
            loaded = serialization.load_pem_private_key(path.read_bytes(), None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                raise RuntimeError("The Web Push key file does not hold a P-256 key.")
            key = loaded
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            path.parent.mkdir(parents=True, exist_ok=True)
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            temporary = path.with_suffix(".tmp")
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(pem)
            os.replace(temporary, path)
        _cached = (str(path), key)
        return key


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def public_key() -> str:
    """The VAPID public key as browsers want it (``applicationServerKey``): base64url of the 65-byte point."""
    return b64url(_point(private_key().public_key()))


def subject() -> str:
    chosen = _hosted("subject") or _subject
    value = os.environ.get("AIRA_VAPID_SUBJECT", "") or (
        chosen() if chosen is not None else ""
    )
    return value or "mailto:notifications@monosage.invalid"


def vapid_header(endpoint: str, *, now: float | None = None, hours: int = 12) -> str:
    """``Authorization: vapid t=<JWT>, k=<public key>`` for one push service (its origin is the audience)."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now if now is not None else time.time()) + hours * 3600),
        "sub": subject(),
    }
    header = b64url(
        json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()
    )
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    der = private_key().sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{body}.{signature}, k={public_key()}"


def _hkdf(salt: bytes, info: bytes, length: int, material: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(
        material
    )


def encrypt(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """The message body for one subscription: RFC 8188 header (salt, record size, the sender's public key) then
    one ``aes128gcm`` record keyed per RFC 8291 from the browser's key and auth secret."""
    if len(plaintext) > MAX_PAYLOAD:
        raise ValueError("A push message holds at most 3000 bytes.")
    receiver = unb64url(p256dh)
    secret = unb64url(auth)
    if len(receiver) != 65 or receiver[0] != 4 or len(secret) < 16:
        raise ValueError("That push subscription's keys are not valid.")
    receiver_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), receiver
    )
    local = sender_key or ec.generate_private_key(ec.SECP256R1())
    local_public = _point(local.public_key())
    shared = local.exchange(ec.ECDH(), receiver_key)
    ikm = _hkdf(secret, b"WebPush: info\x00" + receiver + local_public, 32, shared)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return (
        salt
        + struct.pack("!IB", RECORD_SIZE, len(local_public))
        + local_public
        + record
    )


def decrypt(body: bytes, receiver_key: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """What a browser does with :func:`encrypt`'s output (tests check the payload with it)."""
    salt = body[:16]
    record_size, id_length = struct.unpack("!IB", body[16:21])
    sender = body[21 : 21 + id_length]
    record = body[21 + id_length :]
    if len(record) > record_size:
        raise ValueError("The record is larger than its record size.")
    receiver = _point(receiver_key.public_key())
    shared = receiver_key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender)
    )
    ikm = _hkdf(unb64url(auth), b"WebPush: info\x00" + receiver + sender, 32, shared)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    padded = AESGCM(cek).decrypt(nonce, record, None).rstrip(b"\x00")
    if not padded.endswith(b"\x02"):
        raise ValueError("The last record has no delimiter.")
    return padded[:-1]


def endpoint_allowed(endpoint: str) -> bool:
    """Whether the server may post to this endpoint: https to a browser push service (or a test stub)."""
    try:
        parts = urlsplit(str(endpoint or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if not host or len(endpoint) > 1000 or parts.username or parts.password:
        return False
    if parts.netloc.lower() in EXTRA_HOSTS:
        return parts.scheme in {"http", "https"}
    if parts.scheme != "https" or (parts.port not in (None, 443)):
        return False
    return any(
        host == allowed or (allowed.startswith(".") and host.endswith(allowed))
        for allowed in PUSH_HOSTS
    )


@dataclass(frozen=True)
class Outcome:
    status: int
    gone: bool = False  # the subscription expired or was removed (404, 410): forget it
    retry_after: float = 0.0  # the push service is busy (429, 5xx): try again later
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def send(
    subscription: dict[str, Any],
    payload: dict[str, Any],
    *,
    ttl: int = DEFAULT_TTL,
    urgency: str = "normal",
    topic: str = "",
    timeout: float = 10.0,
) -> Outcome:
    """Encrypt ``payload`` (JSON) for one subscription and post it to its push service."""
    endpoint = str(subscription.get("endpoint") or "")
    if not endpoint_allowed(endpoint):
        return Outcome(0, gone=True, error="Not a browser push service.")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    body = encrypt(data, str(subscription["p256dh"]), str(subscription["auth"]))
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Encoding": "aes128gcm",
        "TTL": str(max(0, int(ttl))),
        "Urgency": urgency if urgency in URGENCIES else "normal",
        "Authorization": vapid_header(endpoint),
    }
    clean_topic = "".join(ch for ch in topic if ch.isalnum() or ch in "-_")[:32]
    if clean_topic:
        headers["Topic"] = clean_topic
    request = urllib.request.Request(
        endpoint, data=body, method="POST", headers=headers
    )
    try:
        with _opener.open(request, timeout=timeout) as response:
            return Outcome(response.status)
    except urllib.error.HTTPError as error:
        status = error.code
        if status in (404, 410):
            return Outcome(status, gone=True, error="The subscription has expired.")
        if status == 429 or status >= 500:
            try:
                wait = float(error.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0.0
            return Outcome(
                status, retry_after=min(max(wait, 5.0), 3600.0) if wait > 0 else 30.0
            )
        return Outcome(
            status, error=f"The push service refused the message ({status})."
        )
    except (urllib.error.URLError, OSError) as error:
        return Outcome(0, retry_after=60.0, error=str(error)[:200])
