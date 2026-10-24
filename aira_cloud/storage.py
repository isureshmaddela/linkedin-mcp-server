"""Where encrypted documents live: local disk or an S3-compatible object store.

Both backends implement the document store's three calls (``put``, ``get``, ``delete``) and lay
objects out as ``tenant/<org_id>/<candidate_id>/<document_id>.bin`` (the local backend keeps the
same shape under ``tenants/<org_id>/``). Content is already encrypted with the organization's
data key before it reaches either backend, so object storage never sees plaintext.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import Request, urlopen

from aira.documents import LocalBlobBackend

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.storage")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(
    *,
    method: str,
    url: str,
    region: str,
    access_key: str,
    secret_key: str,
    payload: bytes = b"",
    now: dt.datetime | None = None,
    service: str = "s3",
    extra_headers: dict[str, str] | None = None,
    session_token: str = "",
) -> dict[str, str]:
    """AWS Signature Version 4 headers for one request (path-style URL, no query unless in ``url``).

    ``extra_headers`` (for example ``content-type`` and ``x-amz-target`` on a KMS call) are signed too and
    must be sent unchanged; they are not part of the returned dictionary.
    """
    parts = urlsplit(url)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    payload_hash = hashlib.sha256(payload).hexdigest()
    host = parts.netloc
    canonical_uri = quote(parts.path or "/", safe="/-_.~")
    # The URL's query is already percent-encoded: decode, then encode each name and value once, sorted (the canonical form).
    query_pairs = sorted(
        (quote(k, safe="-_.~"), quote(v, safe="-_.~"))
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    )
    canonical_query = "&".join(f"{k}={v}" for k, v in query_pairs)
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": stamp}
    if session_token:
        headers["x-amz-security-token"] = session_token
    for name, value in (extra_headers or {}).items():
        headers[name.lower()] = " ".join(str(value).split())
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical = "\n".join(
        [
            method,
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed,
            payload_hash,
        ]
    )
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            stamp,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ]
    )
    key = _hmac(
        _hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), date), region), service),
        "aws4_request",
    )
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    result = {
        "x-amz-date": stamp,
        "x-amz-content-sha256": payload_hash,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}",
    }
    if session_token:
        result["x-amz-security-token"] = session_token
    return result


class S3BlobBackend:
    """An S3-compatible bucket (path-style addressing), scoped to one organization's prefix."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        org_id: str,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.org_id = org_id

    def key(self, owner_id: str, document_id: str) -> str:
        return f"tenant/{self.org_id}/{owner_id}/{document_id}.bin"

    def _url(self, key: str, query: str = "") -> str:
        return f"{self.endpoint}/{self.bucket}/{quote(key, safe='/-_.~')}" + (
            f"?{query}" if query else ""
        )

    def _request(self, method: str, url: str, payload: bytes = b"") -> bytes:
        headers = sigv4_headers(
            method=method,
            url=url,
            region=self.region,
            access_key=self.access_key,
            secret_key=self.secret_key,
            payload=payload,
        )
        request = Request(
            url,
            data=payload if method == "PUT" else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - the endpoint is operator configuration
                return response.read()
        except HTTPError as error:
            if error.code == 404:
                raise FileNotFoundError(url) from None
            raise OSError(f"Object storage answered {error.code}.") from None
        except (URLError, TimeoutError) as error:
            raise OSError(f"Object storage is unreachable: {error}") from None

    def put(self, owner_id: str, document_id: str, token: bytes) -> None:
        self._request("PUT", self._url(self.key(owner_id, document_id)), token)

    def get(self, owner_id: str, document_id: str) -> bytes:
        return self._request("GET", self._url(self.key(owner_id, document_id)))

    def delete(self, owner_id: str, document_id: str) -> None:
        try:
            self._request("DELETE", self._url(self.key(owner_id, document_id)))
        except FileNotFoundError:
            pass

    def delete_prefix(self, owner_id: str = "") -> int:
        prefix = f"tenant/{self.org_id}/" + (f"{owner_id}/" if owner_id else "")
        removed = 0
        token = ""
        while True:
            query = f"list-type=2&prefix={quote(prefix, safe='')}" + (
                f"&continuation-token={quote(token, safe='')}" if token else ""
            )
            body = self._request("GET", f"{self.endpoint}/{self.bucket}?{query}")
            root = ET.fromstring(body)
            namespace = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            for item in root.findall(f"{namespace}Contents"):
                key = item.findtext(f"{namespace}Key") or ""
                try:
                    self._request("DELETE", self._url(key))
                    removed += 1
                except OSError:
                    logger.warning("Could not delete object %s", key)
            if (root.findtext(f"{namespace}IsTruncated") or "false") != "true":
                break
            token = root.findtext(f"{namespace}NextContinuationToken") or ""
            if not token:
                break
        return removed


def ensure_bucket(config: Any) -> str:
    """Create the documents bucket if it does not exist (any S3-compatible store; AWS buckets come from Terraform).

    Returns ``created`` or ``exists``. Raises OSError when the store cannot be reached or refuses.
    """
    if not (
        config.s3_endpoint
        and config.s3_bucket
        and config.s3_access_key
        and config.s3_secret_key
    ):
        raise OSError(
            "Object storage is not configured (endpoint, bucket and keys are needed)."
        )
    url = f"{config.s3_endpoint.rstrip('/')}/{config.s3_bucket}"
    headers = sigv4_headers(
        method="PUT",
        url=url,
        region=config.s3_region,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
    )
    try:
        with urlopen(  # noqa: S310 - the endpoint is operator configuration
            Request(url, method="PUT", headers=headers), timeout=30
        ):
            return "created"
    except HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        if error.code == 409 and "BucketAlready" in body:
            return "exists"
        raise OSError(
            f"Object storage answered {error.code} creating the bucket."
        ) from None
    except (URLError, TimeoutError) as error:
        raise OSError(f"Object storage is unreachable: {error}") from None


class StorageService:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def remote(self) -> bool:
        return self.app.config.storage_backend == "s3"

    def backend(self, org_id: str) -> Any:
        config = self.app.config
        if self.remote:
            if not (
                config.s3_endpoint
                and config.s3_bucket
                and config.s3_access_key
                and config.s3_secret_key
            ):
                raise RuntimeError(
                    "Object storage is selected but not fully configured (endpoint, bucket and keys)."
                )
            return S3BlobBackend(
                endpoint=config.s3_endpoint,
                bucket=config.s3_bucket,
                region=config.s3_region,
                access_key=config.s3_access_key,
                secret_key=config.s3_secret_key,
                org_id=org_id,
            )
        return LocalBlobBackend(self.app.workspaces.org_dir(org_id))

    def delete_prefix(self, org_id: str, candidate_id: str) -> int:
        """Remove a candidate's (or the whole organization's, with an empty id) objects from remote storage."""
        if not self.remote:
            return 0
        try:
            return self.backend(org_id).delete_prefix(candidate_id)
        except (OSError, RuntimeError):
            logger.warning(
                "Could not clear object storage for %s", org_id, exc_info=True
            )
            return 0

    def usage_bytes(self, org_id: str, *, refresh_candidate: str = "") -> int:
        """Document bytes across the organization, from workspace snapshots (the writing candidate's is refreshed)."""
        if refresh_candidate:
            self.app.workspaces.refresh(org_id, refresh_candidate, max_age=0)
        with self.app.db.read() as connection:
            rows = connection.execute(
                "SELECT candidate_id FROM workspaces WHERE org_id = ? AND status != 'deleted'",
                (org_id,),
            ).fetchall()
        total = 0
        for row in rows:
            try:
                total += int(
                    self.app.workspaces.refresh(
                        org_id, row["candidate_id"], max_age=60
                    ).get("documents_bytes", 0)
                )
            except Exception:  # noqa: BLE001 - a missing workspace does not count
                continue
        return total
