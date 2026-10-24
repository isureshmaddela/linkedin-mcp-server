"""Where the master key lives: the local file, AWS KMS, or HashiCorp Vault's transit engine.

Every organization has a data key; the master key wraps it (see ``crypto.SecretManager``). With a
KMS provider the master key never leaves the KMS: ``wrap`` and ``unwrap`` are calls to it, and the
only things it ever sees are data keys, never documents or secrets.

A provider that cannot answer raises :class:`KeyProviderError` (HTTP 503). There is no fallback to
the local key: an outage stops work that needs an organization's key instead of quietly using a
different one.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


from aira_cloud.errors import Unavailable
from aira_cloud.storage import sigv4_headers

if TYPE_CHECKING:
    from aira_cloud.config import CloudConfig
    from aira_cloud.crypto import MasterKeyProvider

logger = logging.getLogger("aira_cloud.kms")
KINDS = ("local", "aws", "vault")
TIMEOUT_SECONDS = 15


class KeyProviderError(Unavailable):
    """The key management service could not wrap or unwrap a key (fail closed)."""

    code = "key_provider_unavailable"

    def __init__(self, message: str = "", **extra: Any) -> None:
        super().__init__(
            message
            or "The key management service is unavailable, so encrypted data cannot be read right now.",
            **extra,
        )


def fernet_key_from_raw(raw: bytes) -> bytes:
    """A 32-byte data key as the urlsafe-base64 text Fernet expects (44-byte keys pass through)."""
    if len(raw) == 32:
        return base64.urlsafe_b64encode(raw)
    return raw


@dataclass(frozen=True)
class KmsSettings:
    """Everything needed to build one master-key provider."""

    kind: str = "local"
    master_key: str = ""
    key_id: str = ""
    endpoint: str = ""
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""
    session_token: str = ""
    vault_addr: str = ""
    vault_token: str = ""
    vault_mount: str = "transit"
    vault_namespace: str = ""
    cache_seconds: int = 300

    @classmethod
    def from_config(cls, config: CloudConfig) -> KmsSettings:
        return cls(
            kind=(config.kms or "local").strip().lower(),
            master_key=config.master_key,
            key_id=config.kms_key_id,
            endpoint=config.kms_endpoint,
            region=config.kms_region,
            access_key=config.kms_access_key,
            secret_key=config.kms_secret_key,
            session_token=config.kms_session_token,
            vault_addr=config.kms_vault_addr,
            vault_token=config.kms_vault_token,
            vault_mount=config.kms_vault_mount,
            vault_namespace=config.kms_vault_namespace,
            cache_seconds=config.kms_cache_seconds,
        )

    @classmethod
    def from_old_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> KmsSettings:
        """The provider being replaced during ``rewrap-keys``: the same names with an ``_OLD`` suffix."""
        env = os.environ if environ is None else environ
        return cls(
            kind=env.get("AIRA_CLOUD_KMS_OLD", "local").strip().lower() or "local",
            master_key=env.get("AIRA_CLOUD_MASTER_KEY_OLD", ""),
            key_id=env.get("AIRA_CLOUD_KMS_KEY_ID_OLD", ""),
            endpoint=env.get("AIRA_CLOUD_KMS_ENDPOINT_OLD", ""),
            region=env.get("AIRA_CLOUD_KMS_REGION_OLD", "us-east-1"),
            access_key=env.get("AIRA_CLOUD_KMS_ACCESS_KEY_OLD", ""),
            secret_key=env.get("AIRA_CLOUD_KMS_SECRET_KEY_OLD", ""),
            session_token=env.get("AIRA_CLOUD_KMS_SESSION_TOKEN_OLD", ""),
            vault_addr=env.get("AIRA_CLOUD_KMS_VAULT_ADDR_OLD", ""),
            vault_token=env.get("AIRA_CLOUD_KMS_VAULT_TOKEN_OLD", ""),
            vault_mount=env.get("AIRA_CLOUD_KMS_VAULT_MOUNT_OLD", "transit"),
            vault_namespace=env.get("AIRA_CLOUD_KMS_VAULT_NAMESPACE_OLD", ""),
        )


def _http(request: Request, *, what: str) -> bytes:
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - the endpoint is operator configuration
            return response.read()
    except HTTPError as error:
        detail = ""
        try:
            body = json.loads(error.read() or b"{}")
            detail = str(
                body.get("__type") or body.get("errors") or body.get("message") or ""
            )
        except (ValueError, AttributeError):
            pass
        logger.error("%s answered %s %s", what, error.code, detail)
        raise KeyProviderError(
            f"{what} refused the request ({error.code}"
            + (f": {detail}" if detail else "")
            + ")."
        ) from None
    except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        logger.error("%s is unreachable: %s", what, error)
        raise KeyProviderError(f"{what} is unreachable.") from None


class AwsKmsKey:
    """AWS KMS (JSON 1.1 API, Signature Version 4). One KMS key wraps every organization key."""

    def __init__(
        self,
        *,
        key_id: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: str = "",
        session_token: str = "",
        cache_seconds: int = 300,
    ) -> None:
        if not key_id:
            raise ValueError(
                "AIRA_CLOUD_KMS_KEY_ID is required for AIRA_CLOUD_KMS=aws."
            )
        if not (access_key and secret_key):
            raise ValueError(
                "AIRA_CLOUD_KMS_ACCESS_KEY and AIRA_CLOUD_KMS_SECRET_KEY are required for AIRA_CLOUD_KMS=aws."
            )
        self.key_id = key_id
        self.region = region or "us-east-1"
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.endpoint = (endpoint or f"https://kms.{self.region}.amazonaws.com").rstrip(
            "/"
        ) + "/"
        self.cache_ttl: float | None = float(cache_seconds)

    def _call(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        extra = {
            "content-type": "application/x-amz-json-1.1",
            "x-amz-target": f"TrentService.{target}",
        }
        headers = sigv4_headers(
            method="POST",
            url=self.endpoint,
            region=self.region,
            access_key=self.access_key,
            secret_key=self.secret_key,
            payload=body,
            service="kms",
            extra_headers=extra,
            session_token=self.session_token,
        )
        request = Request(
            self.endpoint, data=body, method="POST", headers={**headers, **extra}
        )
        raw = _http(request, what="The key management service")
        try:
            return dict(json.loads(raw))
        except ValueError:
            raise KeyProviderError(
                "The key management service sent an unreadable answer."
            ) from None

    def wrap(self, data: bytes) -> bytes:
        answer = self._call(
            "Encrypt",
            {"KeyId": self.key_id, "Plaintext": base64.b64encode(data).decode()},
        )
        return self._blob(answer, "CiphertextBlob")

    def unwrap(self, data: bytes) -> bytes:
        answer = self._call(
            "Decrypt",
            {
                "KeyId": self.key_id,
                "CiphertextBlob": base64.b64encode(data).decode(),
            },
        )
        return fernet_key_from_raw(self._blob(answer, "Plaintext"))

    def new_data_key(self) -> tuple[bytes, bytes]:
        """``(key, wrapped)`` from GenerateDataKey: the KMS generates the key and the random bytes."""
        answer = self._call(
            "GenerateDataKey", {"KeyId": self.key_id, "KeySpec": "AES_256"}
        )
        return (
            fernet_key_from_raw(self._blob(answer, "Plaintext")),
            self._blob(answer, "CiphertextBlob"),
        )

    @staticmethod
    def _blob(answer: dict[str, Any], name: str) -> bytes:
        try:
            return base64.b64decode(str(answer[name]), validate=True)
        except (KeyError, binascii.Error):
            raise KeyProviderError(
                "The key management service sent an unexpected answer."
            ) from None


class VaultTransitKey:
    """HashiCorp Vault's transit engine (token authentication)."""

    def __init__(
        self,
        *,
        address: str,
        token: str,
        key_name: str,
        mount: str = "transit",
        namespace: str = "",
        cache_seconds: int = 300,
    ) -> None:
        if not (address and token and key_name):
            raise ValueError(
                "AIRA_CLOUD_KMS_VAULT_ADDR, AIRA_CLOUD_KMS_VAULT_TOKEN and AIRA_CLOUD_KMS_KEY_ID "
                "(the transit key name) are required for AIRA_CLOUD_KMS=vault."
            )
        self.address = address.rstrip("/")
        self.token = token
        self.key_name = key_name
        self.mount = mount.strip("/") or "transit"
        self.namespace = namespace
        self.cache_ttl: float | None = float(cache_seconds)

    def _call(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.address}/v1/{self.mount}/{path}/{quote(self.key_name, safe='')}"
        headers = {
            "X-Vault-Token": self.token,
            "Content-Type": "application/json",
        }
        if self.namespace:
            headers["X-Vault-Namespace"] = self.namespace
        request = Request(
            url, data=json.dumps(payload).encode(), method="POST", headers=headers
        )
        raw = _http(request, what="The key management service")
        try:
            data = json.loads(raw).get("data")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise KeyProviderError(
                "The key management service sent an unreadable answer."
            )
        return data

    def wrap(self, data: bytes) -> bytes:
        answer = self._call("encrypt", {"plaintext": base64.b64encode(data).decode()})
        return self._cipher(answer)

    def unwrap(self, data: bytes) -> bytes:
        answer = self._call("decrypt", {"ciphertext": data.decode("ascii")})
        return fernet_key_from_raw(self._plain(answer))

    def new_data_key(self) -> tuple[bytes, bytes]:
        answer = self._call("datakey/plaintext", {"bits": 256})
        return fernet_key_from_raw(self._plain(answer)), self._cipher(answer)

    @staticmethod
    def _plain(answer: dict[str, Any]) -> bytes:
        try:
            return base64.b64decode(str(answer["plaintext"]), validate=True)
        except (KeyError, binascii.Error):
            raise KeyProviderError(
                "The key management service sent an unexpected answer."
            ) from None

    @staticmethod
    def _cipher(answer: dict[str, Any]) -> bytes:
        text = str(answer.get("ciphertext", ""))
        if not text.startswith("vault:"):
            raise KeyProviderError(
                "The key management service sent an unexpected answer."
            )
        return text.encode("ascii")


def build_master_key(settings: KmsSettings, data_root: Path) -> MasterKeyProvider:
    """The provider the settings describe. Misconfiguration raises ``ValueError`` at start-up."""
    from aira_cloud.crypto import LocalMasterKey

    kind = settings.kind or "local"
    if kind == "local":
        return LocalMasterKey(settings.master_key, data_root / "secrets" / "master.key")
    if kind == "aws":
        return AwsKmsKey(
            key_id=settings.key_id,
            region=settings.region,
            access_key=settings.access_key or os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key=settings.secret_key
            or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            endpoint=settings.endpoint,
            session_token=settings.session_token
            or os.environ.get("AWS_SESSION_TOKEN", ""),
            cache_seconds=settings.cache_seconds,
        )
    if kind == "vault":
        return VaultTransitKey(
            address=settings.vault_addr or settings.endpoint,
            token=settings.vault_token,
            key_name=settings.key_id,
            mount=settings.vault_mount,
            namespace=settings.vault_namespace,
            cache_seconds=settings.cache_seconds,
        )
    raise ValueError(
        f"AIRA_CLOUD_KMS must be one of {', '.join(KINDS)} (got {kind!r})."
    )


__all__ = [
    "AwsKmsKey",
    "KeyProviderError",
    "KmsSettings",
    "VaultTransitKey",
    "build_master_key",
    "fernet_key_from_raw",
]
