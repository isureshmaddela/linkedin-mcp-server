"""A tiny IMAP server for tests: just enough IMAP4rev1 for imaplib to sign in, open a folder read-only and read
messages by UID. It never connects anywhere.

    server = ImapStub(user="me@biz.test", password="pw").start()
    server.add(raw_bytes)          # a new message; returns its UID
    ... point incoming mail at 127.0.0.1:server.port with security "none" ...
    server.commands                # every command received, e.g. ["CAPABILITY", "LOGIN", "EXAMINE", ...]
    server.stop()
"""

from __future__ import annotations

import socketserver
import threading
from typing import Any


class _Handler(socketserver.StreamRequestHandler):
    server: Any

    def _send(self, line: str) -> None:
        self.wfile.write(line.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        stub: ImapStub = self.server.stub
        self._send("* OK [CAPABILITY IMAP4rev1] stub ready")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            tag, _, rest = line.partition(" ")
            command, _, args = rest.partition(" ")
            command = command.upper()
            with stub.lock:
                stub.commands.append(
                    f"{command} {args.split(' ', 1)[0].upper()}"
                    if command == "UID"
                    else command
                )
            if command == "CAPABILITY":
                self._send("* CAPABILITY IMAP4rev1")
                self._send(f"{tag} OK CAPABILITY completed")
            elif command == "LOGIN":
                user, _, password = args.partition(" ")
                if (user.strip('"'), password.strip('"')) == (stub.user, stub.password):
                    self._send(f"{tag} OK LOGIN completed")
                else:
                    self._send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials")
            elif command in ("SELECT", "EXAMINE"):
                with stub.lock:
                    count = len(stub.messages)
                self._send(f"* {count} EXISTS")
                self._send(f"* OK [UIDVALIDITY {stub.uidvalidity}] UIDs valid")
                self._send(f"* OK [UIDNEXT {stub.next_uid}] next")
                mode = "READ-ONLY" if command == "EXAMINE" else "READ-WRITE"
                self._send(f"{tag} OK [{mode}] {command} completed")
            elif command == "STATUS":
                self._send(f'* STATUS "INBOX" (UIDNEXT {stub.next_uid})')
                self._send(f"{tag} OK STATUS completed")
            elif command == "UID":
                self._uid(tag, args, stub)
            elif command == "NOOP":
                self._send(f"{tag} OK NOOP completed")
            elif command == "LOGOUT":
                self._send("* BYE logging out")
                self._send(f"{tag} OK LOGOUT completed")
                return
            else:
                self._send(f"{tag} BAD unknown command")

    def _uid(self, tag: str, args: str, stub: ImapStub) -> None:
        sub, _, rest = args.partition(" ")
        sub = sub.upper()
        with stub.lock:
            uids = sorted(stub.messages)
        if sub == "SEARCH":
            words = rest.split()
            if words and words[0].upper() == "UID" and len(words) > 1:
                low = int(words[1].split(":")[0])
                found = [uid for uid in uids if uid >= low] or uids[
                    -1:
                ]  # n:* always has the highest
            else:
                found = uids
            self._send("* SEARCH" + "".join(f" {uid}" for uid in found))
            self._send(f"{tag} OK SEARCH completed")
            return
        if sub == "FETCH":
            wanted, _, what = rest.partition(" ")
            uid = int(wanted)
            with stub.lock:
                raw = stub.messages.get(uid)
            if raw is None:
                self._send(f"{tag} OK FETCH completed")
                return
            if "RFC822.SIZE" in what.upper():
                self._send(
                    f"* {uids.index(uid) + 1} FETCH (UID {uid} RFC822.SIZE {len(raw)})"
                )
            else:
                with stub.lock:
                    stub.peeked.append("BODY.PEEK" in what.upper())
                self.wfile.write(
                    f"* {uids.index(uid) + 1} FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode()
                    + raw
                    + b")\r\n"
                )
                self.wfile.flush()
            self._send(f"{tag} OK FETCH completed")
            return
        self._send(f"{tag} BAD unknown UID command")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ImapStub:
    def __init__(
        self, *, user: str = "me@biz.test", password: str = "pw", uidvalidity: int = 7
    ) -> None:
        self.user = user
        self.password = password
        self.uidvalidity = uidvalidity
        self.messages: dict[int, bytes] = {}
        self.next_uid = 1
        self.commands: list[str] = []
        self.peeked: list[bool] = []
        self.lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.stub = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]

    def add(self, raw: bytes) -> int:
        with self.lock:
            uid = self.next_uid
            self.messages[uid] = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            self.next_uid += 1
        return uid

    def start(self) -> ImapStub:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
