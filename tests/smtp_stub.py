"""A tiny SMTP server for tests: it speaks just enough SMTP to receive messages and never relays anything.

    server = SmtpStub().start()
    ... point Aira's mail settings at 127.0.0.1:server.port with security "none" ...
    server.messages    # [{"from": ..., "to": [...], "data": "raw message text"}]
    server.stop()

``require_login=("user", "secret")`` makes it demand AUTH PLAIN or AUTH LOGIN with those values (535 otherwise);
``refuse={"bad@example.test"}`` answers 550 to those recipients.
"""

from __future__ import annotations

import base64
import socketserver
import threading
from email import message_from_string
from typing import Any


class _Handler(socketserver.StreamRequestHandler):
    server: Any

    def _send(self, line: str) -> None:
        self.wfile.write(line.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def _readline(self) -> str:
        return self.rfile.readline().decode("utf-8", "replace").rstrip("\r\n")

    def handle(self) -> None:
        stub: SmtpStub = self.server.stub
        self._send("220 stub.local ESMTP ready")
        sender, recipients, authed = "", [], stub.require_login is None
        while True:
            line = self._readline()
            if not line:
                return
            command = line.split(" ", 1)[0].upper()
            if command in {"EHLO", "HELO"}:
                self._send("250-stub.local")
                if stub.require_login is not None:
                    self._send("250-AUTH PLAIN LOGIN")
                self._send("250 SIZE 10485760")
            elif command == "AUTH":
                parts = line.split()
                if parts[1].upper() == "PLAIN":
                    token = (
                        parts[2]
                        if len(parts) > 2
                        else (self._send("334 ") or self._readline())
                    )
                    _, user, password = base64.b64decode(token).decode().split("\x00")
                else:
                    self._send("334 VXNlcm5hbWU6")
                    user = base64.b64decode(self._readline()).decode()
                    self._send("334 UGFzc3dvcmQ6")
                    password = base64.b64decode(self._readline()).decode()
                if stub.require_login == (user, password):
                    authed = True
                    self._send("235 2.7.0 Authentication successful")
                else:
                    self._send("535 5.7.8 Authentication credentials invalid")
            elif command == "MAIL":
                if not authed:
                    self._send("530 5.7.0 Authentication required")
                    continue
                sender, recipients = (
                    line.split(":", 1)[1].strip().strip("<>").split(">")[0],
                    [],
                )
                self._send("250 OK")
            elif command == "RCPT":
                address = line.split(":", 1)[1].strip().split(">")[0].strip("<> ")
                if address in stub.refuse:
                    self._send("550 5.1.1 No such user")
                else:
                    recipients.append(address)
                    self._send("250 OK")
            elif command == "DATA":
                self._send("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    row = self.rfile.readline().decode("utf-8", "replace")
                    if row in {".\r\n", ".\n", ""}:
                        break
                    lines.append(row[1:] if row.startswith("..") else row)
                with stub.lock:
                    stub.messages.append(
                        {"from": sender, "to": recipients, "data": "".join(lines)}
                    )
                self._send("250 OK queued")
            elif command == "RSET":
                sender, recipients = "", []
                self._send("250 OK")
            elif command == "NOOP":
                self._send("250 OK")
            elif command == "QUIT":
                self._send("221 Bye")
                return
            else:
                self._send("502 Command not implemented")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class SmtpStub:
    def __init__(
        self,
        *,
        require_login: tuple[str, str] | None = None,
        refuse: set[str] | None = None,
    ) -> None:
        self.require_login = require_login
        self.refuse = refuse or set()
        self.messages: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.stub = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]

    def start(self) -> SmtpStub:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def parsed(self, index: int = -1):
        return message_from_string(self.messages[index]["data"])
