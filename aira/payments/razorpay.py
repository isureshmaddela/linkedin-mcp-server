"""A small Razorpay client on the standard library (urllib), and Razorpay's two signature checks.

Every call uses HTTP Basic auth with the business's key id and key secret, a timeout, and answers a dict (the JSON
Razorpay sent) or raises :class:`RazorpayError` with a sentence a person can act on. Amounts are in the currency's
smallest unit (paise for INR), as Razorpay counts them.

The API base is ``https://api.razorpay.com/v1``; ``AIRA_RAZORPAY_API_BASE`` points it elsewhere (the tests' local
stub, ``tests/razorpay_payments_stub.py``). A base that is not https must be this machine's loopback address, so a
key secret is never sent in clear text over a network.

Signatures:

* webhooks: ``X-Razorpay-Signature`` is the HMAC-SHA256 hex of the raw request body with the webhook secret;
* the payment-link return (the customer's browser comes back to ``callback_url``): ``razorpay_signature`` is the
  HMAC-SHA256 hex of ``plink_id|reference_id|status|payment_id`` with the key secret.

Both are compared in constant time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

API_BASE_ENV = "AIRA_RAZORPAY_API_BASE"
DEFAULT_API_BASE = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 20
KEY_ID_PATTERN = re.compile(r"^rzp_(test|live)_[A-Za-z0-9]{6,40}$")
# Razorpay's limits for a payment link: the reference is at most 40 characters and must be unique for the account;
# a description at most 2048; notes at most 15 keys of 256 characters each.
MAX_REFERENCE = 40
MAX_DESCRIPTION = 2048
MAX_NOTES = 15
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


class RazorpayError(RuntimeError):
    """Razorpay refused a call or could not be reached (the route layer answers 502).

    ``status`` is Razorpay's HTTP status (0 when it was not reached), ``code`` its error code
    (``BAD_REQUEST_ERROR``...), ``field`` the request field it named, if any."""

    def __init__(
        self, message: str, *, status: int = 0, code: str = "", field: str = ""
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.field = field


def api_base() -> str:
    """The API base in use: ``AIRA_RAZORPAY_API_BASE`` when set (https, or http to this machine), else Razorpay's."""
    base = (os.environ.get(API_BASE_ENV) or DEFAULT_API_BASE).strip().rstrip("/")
    parts = urllib.parse.urlsplit(base)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host:
        return base
    if parts.scheme == "http" and host in _LOOPBACK:
        return base
    raise RazorpayError(
        f"{API_BASE_ENV} must be an https address (or http to 127.0.0.1 for a local test server)."
    )


def mode_of(key_id: str) -> str:
    """``test`` or ``live`` from the key id's prefix (``rzp_test_`` / ``rzp_live_``), '' for anything else."""
    found = KEY_ID_PATTERN.match(str(key_id or "").strip())
    return found.group(1) if found else ""


def valid_key_id(key_id: str) -> bool:
    return bool(mode_of(key_id))


def minor_units(amount_cents: int, currency: str) -> int:
    """The apps keep money in hundredths of the currency (cents); Razorpay counts the smallest unit, which for a
    zero-decimal currency (JPY) is the whole yen."""
    from aira.suite import kit

    if currency.upper() in kit.ZERO_DECIMAL:
        return int(amount_cents) // 100
    return int(amount_cents)


def from_minor_units(amount_minor: int, currency: str) -> int:
    from aira.suite import kit

    if currency.upper() in kit.ZERO_DECIMAL:
        return int(amount_minor) * 100
    return int(amount_minor)


# ---------------------------------------------------------------------------
# signatures
# ---------------------------------------------------------------------------


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(str(secret).encode("utf-8"), message, hashlib.sha256).hexdigest()


def webhook_signature(body: bytes, secret: str) -> str:
    """What Razorpay puts in ``X-Razorpay-Signature`` for this raw body (used by the tests' stub too)."""
    return _hmac_hex(secret, bytes(body))


def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    """Whether ``signature`` is the webhook secret's HMAC-SHA256 of the raw ``body`` (constant-time compare)."""
    if not secret or not signature:
        return False
    expected = webhook_signature(body, secret)
    return hmac.compare_digest(expected, str(signature).strip().lower())


def callback_signature(
    *,
    payment_link_id: str,
    reference_id: str,
    status: str,
    payment_id: str,
    key_secret: str,
) -> str:
    message = f"{payment_link_id}|{reference_id}|{status}|{payment_id}".encode("utf-8")
    return _hmac_hex(key_secret, message)


def verify_payment_link_callback(
    *,
    payment_link_id: str,
    reference_id: str,
    status: str,
    payment_id: str,
    signature: str,
    key_secret: str,
) -> bool:
    """The return from a payment link (``razorpay_payment_link_*`` query parameters) really came from Razorpay."""
    if not key_secret or not signature or not payment_link_id:
        return False
    expected = callback_signature(
        payment_link_id=payment_link_id,
        reference_id=reference_id,
        status=status,
        payment_id=payment_id,
        key_secret=key_secret,
    )
    return hmac.compare_digest(expected, str(signature).strip().lower())


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

Opener = Callable[..., Any]


def _clean_notes(notes: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (notes or {}).items():
        if len(out) >= MAX_NOTES:
            break
        if value is None or value == "":
            continue
        out[str(key)[:40]] = str(value)[:256]
    return out


class Client:
    """One business's Razorpay account. ``opener`` replaces ``urllib.request.urlopen`` (tests)."""

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        *,
        base: str | None = None,
        timeout: float = TIMEOUT_SECONDS,
        opener: Opener | None = None,
    ) -> None:
        if not valid_key_id(key_id):
            raise ValueError(
                "The Razorpay key id starts with rzp_test_ or rzp_live_ (Razorpay dashboard > Account & Settings > API keys)."
            )
        if not key_secret:
            raise ValueError("Enter the Razorpay key secret.")
        self.key_id = key_id.strip()
        self._secret = key_secret
        self.base = (base or api_base()).rstrip("/")
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    @property
    def mode(self) -> str:
        return mode_of(self.key_id)

    def _auth(self) -> str:
        token = base64.b64encode(f"{self.key_id}:{self._secret}".encode("utf-8"))
        return "Basic " + token.decode("ascii")

    def _call(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", self._auth())
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "Monosage-Payments/1.0")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_000)
        except urllib.error.HTTPError as error:
            raise self._refused(error) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise RazorpayError(
                f"Could not reach Razorpay ({reason}). Check the internet connection and try again."
            ) from None
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            raise RazorpayError("Razorpay sent an answer that is not JSON.") from None
        if not isinstance(value, dict):
            raise RazorpayError("Razorpay sent an unexpected answer.")
        return value

    @staticmethod
    def _refused(error: urllib.error.HTTPError) -> RazorpayError:
        status = int(error.code or 0)
        try:
            detail = json.loads(error.read(200_000) or b"{}").get("error") or {}
        except (ValueError, AttributeError, OSError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        code = str(detail.get("code") or "")
        field = str(detail.get("field") or "")
        description = str(detail.get("description") or "").strip()
        if status == 401:
            message = "Razorpay refused the key id or key secret. Copy them again from the Razorpay dashboard (API keys)."
        elif status == 429:
            message = "Razorpay is receiving too many requests. Try again in a minute."
        elif status >= 500:
            message = (
                "Razorpay is having trouble right now. Try again in a few minutes."
            )
        else:
            message = f"Razorpay refused this: {description or f'HTTP {status}'}"
        return RazorpayError(message[:400], status=status, code=code, field=field)

    # -- payment links ---------------------------------------------------------

    def create_payment_link(
        self,
        *,
        amount: int,
        currency: str,
        reference_id: str,
        description: str,
        customer: Mapping[str, Any] | None = None,
        notify: Mapping[str, bool] | None = None,
        callback_url: str = "",
        expire_by: int = 0,
        notes: Mapping[str, Any] | None = None,
        accept_partial: bool = False,
    ) -> dict[str, Any]:
        """``POST /payment_links``: a hosted page for one amount (UPI, cards, netbanking, wallets)."""
        if int(amount) < 100 and currency.upper() == "INR":
            raise ValueError("Razorpay takes payments of at least ₹1.00.")
        if int(amount) <= 0:
            raise ValueError("A payment link needs an amount.")
        body: dict[str, Any] = {
            "amount": int(amount),
            "currency": currency.upper(),
            "accept_partial": bool(accept_partial),
            "reference_id": str(reference_id)[:MAX_REFERENCE],
            "description": str(description)[:MAX_DESCRIPTION],
            "notify": {
                "sms": bool((notify or {}).get("sms")),
                "email": bool((notify or {}).get("email")),
            },
            "reminder_enable": False,
            "notes": _clean_notes(notes),
        }
        person = {
            key: str(value)
            for key, value in (customer or {}).items()
            if key in ("name", "email", "contact") and value
        }
        if person:
            body["customer"] = person
        if callback_url:
            body["callback_url"] = callback_url
            body["callback_method"] = "get"
        if expire_by:
            body["expire_by"] = int(expire_by)
        return self._call("POST", "/payment_links", body)

    def fetch_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        return self._call("GET", f"/payment_links/{_ident(payment_link_id, 'plink_')}")

    def cancel_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        return self._call(
            "POST", f"/payment_links/{_ident(payment_link_id, 'plink_')}/cancel", {}
        )

    # -- orders (for Razorpay Checkout later) ------------------------------------

    def create_order(
        self,
        *,
        amount: int,
        currency: str,
        receipt: str,
        notes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/orders",
            {
                "amount": int(amount),
                "currency": currency.upper(),
                "receipt": str(receipt)[:MAX_REFERENCE],
                "notes": _clean_notes(notes),
            },
        )

    def fetch_order(self, order_id: str) -> dict[str, Any]:
        return self._call("GET", f"/orders/{_ident(order_id, 'order_')}")

    # -- payments and refunds ----------------------------------------------------

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return self._call("GET", f"/payments/{_ident(payment_id, 'pay_')}")

    def refund(
        self,
        payment_id: str,
        *,
        amount: int | None = None,
        notes: Mapping[str, Any] | None = None,
        receipt: str = "",
        speed: str = "normal",
    ) -> dict[str, Any]:
        """``POST /payments/{id}/refund``: the whole payment (``amount`` None) or part of it."""
        body: dict[str, Any] = {"speed": speed, "notes": _clean_notes(notes)}
        if amount is not None:
            if int(amount) <= 0:
                raise ValueError("A refund needs an amount.")
            body["amount"] = int(amount)
        if receipt:
            body["receipt"] = str(receipt)[:MAX_REFERENCE]
        return self._call(
            "POST", f"/payments/{_ident(payment_id, 'pay_')}/refund", body
        )

    def check(self) -> dict[str, Any]:
        """Test the keys: one small read (``GET /payment_links?count=1``). Raises RazorpayError when refused."""
        self._call("GET", "/payment_links", query={"count": 1})
        return {"ok": True, "mode": self.mode}


def _ident(value: str, prefix: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(rf"{prefix}[A-Za-z0-9]{{4,40}}", text):
        raise ValueError(f"That is not a Razorpay {prefix.rstrip('_')} id.")
    return text
