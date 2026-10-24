"""A stand-in S3-compatible object store for tests (loopback only).

It verifies every request's AWS Signature Version 4 with its own, independent implementation of the
algorithm (so the client's signer is checked against the specification, not against itself), keeps objects
in memory, and supports PUT, GET, DELETE and ListObjectsV2 with prefix and continuation tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from xml.sax.saxutils import escape

ACCESS_KEY = "AKIATESTACCESSKEY"
SECRET_KEY = "test-secret-key-0123456789"
REGION = "us-east-1"
BUCKET = "aira-test"


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class S3Stub:
    def __init__(self, page_size: int = 1000) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.rejected: list[str] = []
        self.page_size = page_size
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                return

            def _verify(self, body: bytes) -> bool:
                header = self.headers.get("Authorization", "")
                match = re.fullmatch(
                    r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request, SignedHeaders=([^,]+), Signature=([0-9a-f]{64})",
                    header,
                )
                if not match:
                    return False
                access, date, region, signed, signature = match.groups()
                if access != ACCESS_KEY or region != REGION:
                    return False
                payload_hash = self.headers.get("x-amz-content-sha256", "")
                if payload_hash != hashlib.sha256(body).hexdigest():
                    return False
                parts = urlsplit(self.path)
                query = "&".join(
                    f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                    for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True))
                )
                names = signed.split(";")
                canonical_headers = "".join(
                    f"{name}:{self.headers.get(name, '').strip()}\n" for name in names
                )
                canonical = "\n".join(
                    [
                        self.command,
                        parts.path,
                        query,
                        canonical_headers,
                        signed,
                        payload_hash,
                    ]
                )
                stamp = self.headers.get("x-amz-date", "")
                scope = f"{date}/{region}/s3/aws4_request"
                to_sign = "\n".join(
                    [
                        "AWS4-HMAC-SHA256",
                        stamp,
                        scope,
                        hashlib.sha256(canonical.encode()).hexdigest(),
                    ]
                )
                key = _sign(
                    _sign(
                        _sign(_sign(f"AWS4{SECRET_KEY}".encode(), date), region), "s3"
                    ),
                    "aws4_request",
                )
                expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
                return hmac.compare_digest(expected, signature)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                with stub._lock:
                    stub.calls.append((self.command, self.path))
                if not self._verify(body):
                    with stub._lock:
                        stub.rejected.append(f"{self.command} {self.path}")
                    self._reply(
                        403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>"
                    )
                    return
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                prefix = f"/{BUCKET}/"
                if self.command == "GET" and path == f"/{BUCKET}":
                    self._list(dict(parse_qsl(parts.query)))
                    return
                if not path.startswith(prefix):
                    self._reply(404, b"")
                    return
                key = path[len(prefix) :]
                with stub._lock:
                    if self.command == "PUT":
                        stub.objects[key] = body
                        self._reply(200, b"")
                    elif self.command == "GET":
                        if key in stub.objects:
                            self._reply(200, stub.objects[key])
                        else:
                            self._reply(404, b"<Error><Code>NoSuchKey</Code></Error>")
                    elif self.command == "DELETE":
                        stub.objects.pop(key, None)
                        self._reply(204, b"")
                    else:
                        self._reply(405, b"")

            def _list(self, query: dict[str, str]) -> None:
                prefix = query.get("prefix", "")
                token = query.get("continuation-token", "")
                with stub._lock:
                    keys = sorted(k for k in stub.objects if k.startswith(prefix))
                start = keys.index(token) + 1 if token in keys else 0
                page = keys[start : start + stub.page_size]
                truncated = start + stub.page_size < len(keys)
                items = "".join(
                    f"<Contents><Key>{escape(k)}</Key></Contents>" for k in page
                )
                more = (
                    f"<NextContinuationToken>{escape(page[-1])}</NextContinuationToken>"
                    if truncated
                    else ""
                )
                xml = f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{str(truncated).lower()}</IsTruncated>{items}{more}</ListBucketResult>'
                self._reply(200, xml.encode())

            def _reply(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_PUT = do_DELETE = _handle  # noqa: N815

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> S3Stub:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            return sorted(k for k in self.objects if k.startswith(prefix))
