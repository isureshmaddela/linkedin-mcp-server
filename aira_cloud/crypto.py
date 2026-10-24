"""Passwords, tokens, TOTP, and the per-organization secret manager.

No secret is read from an environment file by tenant code: each organization
has a data key, wrapped by a master key from a ``MasterKeyProvider`` (a local
file or environment variable here; a KMS provider implements the same two
methods). Tenant secrets (SerpApi key, SSO client secret, model endpoint key)
are encrypted with the organization's data key and stored in ``org_secrets``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from aira_cloud.config import Clock
from aira_cloud.db import ControlDB, one
from aira_cloud.errors import ValidationFailed

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
WEAK_PASSWORDS = {
    "password1234",
    "123456789012",
    "qwertyuiop12",
    "letmein12345",
    "administrator",
    "passwordpassword",
    "iloveyou1234",
    "welcome12345",
}


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise ValidationFailed("Enter a valid email address.")
    return email


def check_password_policy(password: str, email: str = "") -> None:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValidationFailed("The password must be between 12 and 1,024 characters.")
    lowered = password.lower()
    if lowered in WEAK_PASSWORDS or len(set(password)) < 5:
        raise ValidationFailed("Choose a password that is harder to guess.")
    local = email.split("@", 1)[0].lower() if email else ""
    if local and len(local) >= 4 and local in lowered:
        raise ValidationFailed("The password must not contain your email name.")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=128 * 1024 * 1024,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=128 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()


def equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --------------------------------------------------------------------------- #
# TOTP (RFC 6238, HMAC-SHA1, 30 s, 6 digits)
# --------------------------------------------------------------------------- #


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (
        10**digits
    )
    return str(value).zfill(digits)


def totp_code(secret: str, at: int, step: int = 30) -> str:
    return _hotp(secret, at // step)


def verify_totp(
    secret: str,
    code: str,
    at: int,
    last_step: int = 0,
    *,
    window: int = 1,
    step: int = 30,
) -> int | None:
    """Return the matching time step, or None. A step at or before ``last_step`` is a replay."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        return None
    current = at // step
    for counter in range(current - window, current + window + 1):
        if counter <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def new_recovery_codes(count: int = 10) -> tuple[list[str], list[str]]:
    """Return ``(plaintext codes to show once, their hashes to store)``."""
    codes = [
        f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        for _ in range(count)
    ]
    return codes, [hash_token(code) for code in codes]


# --------------------------------------------------------------------------- #
# Secret manager
# --------------------------------------------------------------------------- #


class MasterKeyProvider(Protocol):
    """``wrap`` and ``unwrap`` are required. A provider may also offer ``new_data_key() -> (key, wrapped)``
    (a KMS that generates the key itself) and ``cache_ttl`` (seconds an unwrapped key may stay in
    memory; ``None`` or absent means until the process ends)."""

    def wrap(self, data: bytes) -> bytes: ...

    def unwrap(self, data: bytes) -> bytes: ...


class LocalMasterKey:
    """A master key from ``AIRA_CLOUD_MASTER_KEY`` or a 0600 file under the data root.

    ``aira_cloud.kms`` has providers whose wrap/unwrap call a KMS (AWS KMS, Vault transit);
    nothing else in the shell sees the master key.
    """

    def __init__(self, configured: str, path: Path) -> None:
        if configured:
            key = configured.encode("ascii")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                key = path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(key + b"\n")
        self._fernet = Fernet(key)

    def wrap(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def unwrap(self, data: bytes) -> bytes:
        return self._fernet.decrypt(data)


class SecretManager:
    def __init__(
        self, db: ControlDB, clock: Clock, provider: MasterKeyProvider
    ) -> None:
        self.db = db
        self.clock = clock
        self.provider = provider
        self._cache: dict[tuple[str, int], tuple[bytes, float | None]] = {}
        self._lock = threading.Lock()

    # -- in-memory cache of unwrapped keys (never written anywhere) ------------

    def _remember(self, org_id: str, version: int, key: bytes) -> None:
        ttl = getattr(self.provider, "cache_ttl", None)
        expires = time.monotonic() + float(ttl) if ttl else None
        with self._lock:
            self._cache[(org_id, version)] = (key, expires)

    def _recall(self, org_id: str, version: int) -> bytes | None:
        with self._lock:
            entry = self._cache.get((org_id, version))
            if entry is None:
                return None
            key, expires = entry
            if expires is not None and expires <= time.monotonic():
                del self._cache[(org_id, version)]
                return None
            return key

    def _new_data_key(self) -> tuple[bytes, bytes]:
        """A fresh data key and its wrapped form (asks the KMS to generate it when it can)."""
        generate = getattr(self.provider, "new_data_key", None)
        if generate is not None:
            key, wrapped = generate()
            return bytes(key), bytes(wrapped)
        key = Fernet.generate_key()
        return key, self.provider.wrap(key)

    # -- keys ----------------------------------------------------------------

    def current_version(self, org_id: str) -> int:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT MAX(version) AS v FROM org_keys WHERE org_id = ? AND retired_at IS NULL",
                (org_id,),
            )
        version = int(row["v"]) if row and row["v"] else 0
        if version:
            return version
        return self._create_key(org_id)

    def _create_key(self, org_id: str) -> int:
        key, wrapped = (
            self._new_data_key()
        )  # a provider call: never made while holding the database write lock
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT MAX(version) AS v FROM org_keys WHERE org_id = ?",
                (org_id,),
            )
            current = one(
                connection,
                "SELECT MAX(version) AS v FROM org_keys WHERE org_id = ? AND retired_at IS NULL",
                (org_id,),
            )
            if current and current["v"]:
                return int(current["v"])
            version = int(row["v"] or 0) + 1 if row else 1
            connection.execute(
                "INSERT INTO org_keys (org_id, version, wrapped_key, created_at) VALUES (?, ?, ?, ?)",
                (org_id, version, wrapped, self.clock.now()),
            )
        self._remember(org_id, version, key)
        return version

    def key(self, org_id: str, version: int | None = None) -> bytes:
        version = version or self.current_version(org_id)
        cached = self._recall(org_id, version)
        if cached:
            return cached
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT wrapped_key FROM org_keys WHERE org_id = ? AND version = ?",
                (org_id, version),
            )
        if row is None:
            raise KeyError(f"No data key {version} for the organization.")
        key = self.provider.unwrap(bytes(row["wrapped_key"]))
        self._remember(org_id, version, key)
        return key

    def fernet(self, org_id: str) -> MultiFernet:
        """Current key first, then every retired key still needed to read older data."""
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT version FROM org_keys WHERE org_id = ? ORDER BY version DESC",
                (org_id,),
            ).fetchall()
        if not rows:
            self.current_version(org_id)
            with self.db.read() as connection:
                rows = connection.execute(
                    "SELECT version FROM org_keys WHERE org_id = ? ORDER BY version DESC",
                    (org_id,),
                ).fetchall()
        return MultiFernet(
            [Fernet(self.key(org_id, int(row["version"]))) for row in rows]
        )

    # -- secrets -------------------------------------------------------------

    def put(self, org_id: str, name: str, plaintext: str) -> None:
        version = self.current_version(org_id)
        token = Fernet(self.key(org_id, version)).encrypt(plaintext.encode("utf-8"))
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO org_secrets (org_id, name, ciphertext, key_version, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(org_id, name) DO UPDATE SET ciphertext = excluded.ciphertext,
                key_version = excluded.key_version, updated_at = excluded.updated_at""",
                (org_id, name, token, version, self.clock.now()),
            )

    def get(self, org_id: str, name: str) -> str | None:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT ciphertext, key_version FROM org_secrets WHERE org_id = ? AND name = ?",
                (org_id, name),
            )
        if row is None:
            return None
        try:
            return (
                Fernet(self.key(org_id, int(row["key_version"])))
                .decrypt(bytes(row["ciphertext"]))
                .decode("utf-8")
            )
        except InvalidToken:
            return None

    def exists(self, org_id: str, name: str) -> bool:
        with self.db.read() as connection:
            return (
                one(
                    connection,
                    "SELECT 1 AS x FROM org_secrets WHERE org_id = ? AND name = ?",
                    (org_id, name),
                )
                is not None
            )

    def delete(self, org_id: str, name: str) -> bool:
        with self.db.write() as connection:
            return (
                connection.execute(
                    "DELETE FROM org_secrets WHERE org_id = ? AND name = ?",
                    (org_id, name),
                ).rowcount
                > 0
            )

    def rotate(self, org_id: str) -> int:
        """Add a new data key and re-encrypt the organization's secrets under it.

        The previous key stays readable (``fernet`` still lists it) until whatever
        else it protects, such as stored documents, has been re-encrypted and
        :meth:`retire_old_keys` runs, so rotation needs no downtime.
        """
        self.current_version(org_id)
        key, wrapped = self._new_data_key()
        self.fernet(
            org_id
        )  # unwrap every key the secrets use now, not inside the write transaction
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT MAX(version) AS v FROM org_keys WHERE org_id = ?",
                (org_id,),
            )
            version = int(row["v"] or 0) + 1 if row else 1
            connection.execute(
                "INSERT INTO org_keys (org_id, version, wrapped_key, created_at) VALUES (?, ?, ?, ?)",
                (org_id, version, wrapped, self.clock.now()),
            )
            secrets_rows = connection.execute(
                "SELECT name, ciphertext, key_version FROM org_secrets WHERE org_id = ?",
                (org_id,),
            ).fetchall()
            for secret in secrets_rows:
                plaintext = Fernet(
                    self.key(org_id, int(secret["key_version"]))
                ).decrypt(bytes(secret["ciphertext"]))
                connection.execute(
                    "UPDATE org_secrets SET ciphertext = ?, key_version = ?, updated_at = ? WHERE org_id = ? AND name = ?",
                    (
                        Fernet(key).encrypt(plaintext),
                        version,
                        self.clock.now(),
                        org_id,
                        secret["name"],
                    ),
                )
        self._remember(org_id, version, key)
        return version

    def retire_old_keys(self, org_id: str) -> int:
        """Drop keys older than the current one. Call after everything is re-encrypted."""
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT MAX(version) AS v FROM org_keys WHERE org_id = ?",
                (org_id,),
            )
            current = int(row["v"] or 0) if row else 0
            deleted = connection.execute(
                "DELETE FROM org_keys WHERE org_id = ? AND version < ?",
                (org_id, current),
            ).rowcount
        with self._lock:
            for cache_key in [
                k for k in self._cache if k[0] == org_id and k[1] < current
            ]:
                del self._cache[cache_key]
        return deleted

    def rewrap_all(
        self,
        old: MasterKeyProvider,
        new: MasterKeyProvider,
        *,
        verify_only: bool = False,
    ) -> dict[str, Any]:
        """Re-wrap every organization data key from the ``old`` master key to the ``new`` one.

        Phase one makes every provider call (unwrap with old, wrap with new, unwrap again with new to
        prove the round trip) without touching the database, so a failure there changes nothing. Phase
        two replaces all the wrapped keys in one transaction, and only rows still holding the blob that
        was read; any mismatch rolls the whole transaction back. Data keys themselves do not change, so
        documents and secrets need no re-encryption.
        """
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT org_id, version, wrapped_key FROM org_keys ORDER BY org_id, version"
            ).fetchall()
        updates: list[tuple[bytes, str, int, bytes]] = []
        orgs: set[str] = set()
        for row in rows:
            blob = bytes(row["wrapped_key"])
            key = old.unwrap(blob)
            try:
                Fernet(key)
            except (ValueError, TypeError):
                raise ValidationFailed(
                    f"The old master key does not open organization {row['org_id']} (key {row['version']})."
                ) from None
            rewrapped = new.wrap(key)
            if new.unwrap(rewrapped) != key:
                raise ValidationFailed(
                    "The new master key did not return the key it wrapped; nothing was changed."
                )
            updates.append((rewrapped, row["org_id"], int(row["version"]), blob))
            orgs.add(row["org_id"])
        result: dict[str, Any] = {
            "keys": len(updates),
            "organizations": len(orgs),
            "verified": True,
            "applied": False,
        }
        if verify_only or not updates:
            return result
        with self.db.write() as connection:
            for rewrapped, org_id, version, blob in updates:
                changed = connection.execute(
                    "UPDATE org_keys SET wrapped_key = ? WHERE org_id = ? AND version = ? AND wrapped_key = ?",
                    (rewrapped, org_id, version, blob),
                ).rowcount
                if changed != 1:
                    raise ValidationFailed(
                        "An organization key changed while it was being re-wrapped; nothing was changed. Try again."
                    )
        with self._lock:
            self._cache.clear()
        result["applied"] = True
        return result

    def forget(self, org_id: str) -> None:
        """Drop cached keys (after a restore replaced the organization's key rows)."""
        with self._lock:
            for cache_key in [k for k in self._cache if k[0] == org_id]:
                del self._cache[cache_key]

    def delete_org(self, org_id: str) -> None:
        with self.db.write() as connection:
            connection.execute("DELETE FROM org_secrets WHERE org_id = ?", (org_id,))
            connection.execute("DELETE FROM org_keys WHERE org_id = ?", (org_id,))
        with self._lock:
            for cache_key in [k for k in self._cache if k[0] == org_id]:
                del self._cache[cache_key]
