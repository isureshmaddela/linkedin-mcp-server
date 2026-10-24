"""Stand-in key management services for tests (loopback only): AWS KMS and Vault's transit engine.

``AwsKmsStub`` speaks the KMS JSON 1.1 wire format (``X-Amz-Target: TrentService.Encrypt`` /
``Decrypt`` / ``GenerateDataKey``) and verifies every request's Signature Version 4 with its own,
independent implementation, including the signed ``content-type`` and ``x-amz-target`` headers, so
the client's signer is checked against the specification and not against itself. Both stubs keep
the length of every plaintext they were asked to handle, which lets tests prove that a KMS only
ever sees data keys and never documents or secrets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

ACCESS_KEY = "AKIAKMSTESTACCESS"
SECRET_KEY = "kms-test-secret-key-0123456789"
REGION = "eu-west-1"
KEY_ID = "alias/aira-master"
VAULT_TOKEN = "s.vault-test-token"


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class _Server:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rejected: list[str] = []
        self.plaintext_lengths: list[int] = []
        self.plaintexts: list[bytes] = []
        self.fail_with: int | None = None  # answer every request with this HTTP status
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def _handler(self) -> type[BaseHTTPRequestHandler]:  # pragma: no cover - overridden
        raise NotImplementedError

    def start(self, port: int = 0) -> _Server:
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def note_plaintext(self, data: bytes) -> None:
        with self._lock:
            self.plaintext_lengths.append(len(data))
            self.plaintexts.append(data)

    def saw(self, needle: bytes) -> bool:
        """Did any plaintext, or any request body, contain these bytes?"""
        with self._lock:
            return any(needle in item for item in self.plaintexts)


class AwsKmsStub(_Server):
    def __init__(self, key_ids: tuple[str, ...] = (KEY_ID,)) -> None:
        super().__init__()
        self.masters = {key_id: Fernet.generate_key() for key_id in key_ids}
        self.bodies: list[bytes] = []

    def saw(self, needle: bytes) -> bool:
        with self._lock:
            bodies = list(self.bodies)
        return super().saw(needle) or any(needle in body for body in bodies)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _verify(self, body: bytes) -> bool:
                header = self.headers.get("Authorization", "")
                match = re.fullmatch(
                    r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/kms/aws4_request, SignedHeaders=([^,]+), Signature=([0-9a-f]{64})",
                    header,
                )
                if not match:
                    return False
                access, date, region, signed, signature = match.groups()
                if access != ACCESS_KEY or region != REGION:
                    return False
                names = signed.split(";")
                for required in ("host", "x-amz-date", "x-amz-target", "content-type"):
                    if required not in names:
                        return False  # a KMS call must sign its target and content type
                payload_hash = self.headers.get("x-amz-content-sha256", "")
                if payload_hash != hashlib.sha256(body).hexdigest():
                    return False
                canonical_headers = "".join(
                    f"{name}:{' '.join(self.headers.get(name, '').split())}\n"
                    for name in names
                )
                canonical = "\n".join(
                    [
                        self.command,
                        self.path.split("?")[0] or "/",
                        "",
                        canonical_headers,
                        signed,
                        payload_hash,
                    ]
                )
                stamp = self.headers.get("x-amz-date", "")
                scope = f"{date}/{region}/kms/aws4_request"
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
                        _sign(_sign(f"AWS4{SECRET_KEY}".encode(), date), region), "kms"
                    ),
                    "aws4_request",
                )
                expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
                return hmac.compare_digest(expected, signature)

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/x-amz-json-1.1")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                target = self.headers.get("X-Amz-Target", "")
                with stub._lock:
                    stub.calls.append(target)
                    stub.bodies.append(body)
                if stub.fail_with:
                    self._reply(
                        stub.fail_with,
                        {"__type": "KMSInternalException", "message": "down"},
                    )
                    return
                if not self._verify(body):
                    with stub._lock:
                        stub.rejected.append(target)
                    self._reply(
                        400,
                        {
                            "__type": "InvalidSignatureException",
                            "message": "signature mismatch",
                        },
                    )
                    return
                try:
                    request = json.loads(body or b"{}")
                except ValueError:
                    self._reply(400, {"__type": "SerializationException"})
                    return
                key_id = str(request.get("KeyId", ""))
                master = stub.masters.get(key_id)
                if target == "TrentService.Decrypt" and master is None and key_id == "":
                    self._reply(400, {"__type": "InvalidCiphertextException"})
                    return
                if master is None:
                    self._reply(
                        400, {"__type": "NotFoundException", "message": "no such key"}
                    )
                    return
                fernet = Fernet(master)
                if target == "TrentService.Encrypt":
                    plain = base64.b64decode(request["Plaintext"])
                    stub.note_plaintext(plain)
                    blob = key_id.encode() + b"|" + fernet.encrypt(plain)
                    self._reply(
                        200,
                        {
                            "KeyId": key_id,
                            "CiphertextBlob": base64.b64encode(blob).decode(),
                        },
                    )
                elif target == "TrentService.Decrypt":
                    blob = base64.b64decode(request["CiphertextBlob"])
                    owner, _, token = blob.partition(b"|")
                    if owner.decode() != key_id:
                        self._reply(
                            400,
                            {
                                "__type": "IncorrectKeyException",
                                "message": "the key does not match the ciphertext",
                            },
                        )
                        return
                    try:
                        plain = fernet.decrypt(token)
                    except InvalidToken:
                        self._reply(400, {"__type": "InvalidCiphertextException"})
                        return
                    stub.note_plaintext(plain)
                    self._reply(
                        200,
                        {
                            "KeyId": key_id,
                            "Plaintext": base64.b64encode(plain).decode(),
                        },
                    )
                elif target == "TrentService.GenerateDataKey":
                    if request.get("KeySpec") != "AES_256":
                        self._reply(400, {"__type": "ValidationException"})
                        return
                    plain = os.urandom(32)
                    stub.note_plaintext(plain)
                    blob = key_id.encode() + b"|" + fernet.encrypt(plain)
                    self._reply(
                        200,
                        {
                            "KeyId": key_id,
                            "Plaintext": base64.b64encode(plain).decode(),
                            "CiphertextBlob": base64.b64encode(blob).decode(),
                        },
                    )
                else:
                    self._reply(400, {"__type": "UnknownOperationException"})

        return Handler


class VaultTransitStub(_Server):
    def __init__(self, key_names: tuple[str, ...] = ("aira-master",)) -> None:
        super().__init__()
        self.masters = {name: Fernet.generate_key() for name in key_names}
        self.headers_seen: list[dict[str, str]] = []

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                with stub._lock:
                    stub.calls.append(self.path)
                    stub.headers_seen.append(
                        {k.lower(): v for k, v in self.headers.items()}
                    )
                if stub.fail_with:
                    self._reply(stub.fail_with, {"errors": ["down"]})
                    return
                if self.headers.get("X-Vault-Token") != VAULT_TOKEN:
                    with stub._lock:
                        stub.rejected.append(self.path)
                    self._reply(403, {"errors": ["permission denied"]})
                    return
                match = re.fullmatch(
                    r"/v1/([^/]+)/(encrypt|decrypt|datakey/plaintext)/([^/]+)",
                    self.path,
                )
                if not match:
                    self._reply(404, {"errors": []})
                    return
                _mount, operation, name = match.groups()
                master = stub.masters.get(name)
                if master is None:
                    self._reply(400, {"errors": ["encryption key not found"]})
                    return
                fernet = Fernet(master)
                request = json.loads(body or b"{}")
                if operation == "encrypt":
                    plain = base64.b64decode(request["plaintext"])
                    stub.note_plaintext(plain)
                    token = base64.b64encode(fernet.encrypt(plain)).decode()
                    self._reply(200, {"data": {"ciphertext": f"vault:v1:{token}"}})
                elif operation == "decrypt":
                    text = str(request["ciphertext"])
                    if not text.startswith("vault:v1:"):
                        self._reply(400, {"errors": ["invalid ciphertext"]})
                        return
                    try:
                        plain = fernet.decrypt(
                            base64.b64decode(text[len("vault:v1:") :])
                        )
                    except (InvalidToken, ValueError):
                        self._reply(
                            400, {"errors": ["cipher: message authentication failed"]}
                        )
                        return
                    stub.note_plaintext(plain)
                    self._reply(
                        200, {"data": {"plaintext": base64.b64encode(plain).decode()}}
                    )
                else:
                    if request.get("bits") != 256:
                        self._reply(400, {"errors": ["bad bits"]})
                        return
                    plain = os.urandom(32)
                    stub.note_plaintext(plain)
                    token = base64.b64encode(fernet.encrypt(plain)).decode()
                    self._reply(
                        200,
                        {
                            "data": {
                                "plaintext": base64.b64encode(plain).decode(),
                                "ciphertext": f"vault:v1:{token}",
                            }
                        },
                    )

        return Handler
