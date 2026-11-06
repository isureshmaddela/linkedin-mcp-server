"""Incoming email: read the business's own mailbox over IMAP and hand each new message to the app it is for.

* Polled by the background pass (:func:`aira.suite.background.run`, every few minutes) and on demand (Business
  hub > Email > Incoming mail > Check now). Over SSL (or STARTTLS); plain IMAP only to this computer (a mail
  bridge on 127.0.0.1). A hosted build refuses mail servers on internal addresses and connects to the address it
  checked (no second DNS lookup), still verifying the certificate for the server's name.
* The password lives in the secret store, like the SMTP password (:data:`PASSWORD_SECRET`).
* The mailbox is never changed: the folder is opened read-only (EXAMINE) and messages are read with BODY.PEEK, so
  they stay unread for the person's own mail program. The highest UID seen is remembered with the folder's
  UIDVALIDITY; the first check starts from the newest message, so old mail is not imported.
* Each message is handled once (``suite_inbound`` keeps its Message-ID and what was done with it).

Where a message goes, in this order (the first that takes it wins):

1. A delivery failure report (a bounce, RFC 3464): each address that failed for good goes on the do-not-contact
   list, mail still queued for it is withdrawn, and Leads sequences to it stop.
2. An automatic reply (``Auto-Submitted``, an out-of-office, a list): recorded, nothing else, so two robots never
   answer each other.
3. "unsubscribe" in the subject (the ``mailto:`` of a newsletter's List-Unsubscribe header): Marketing unsubscribes
   the sender and Leads stops writing to them.
4. Consumers registered with :func:`consumer`. Careers mailbox sync (roadmap F19: recruiter replies and
   application updates read from the person's own mailbox) is meant to register here as ``"careers"``; it is not
   built yet.
5. Threading: the ``[#1234]`` reference in the subject names a Service case; otherwise In-Reply-To and References
   are matched against the Message-IDs of mail we sent (the shared outbox, then Leads). A reply to a case is added
   to it; a reply to a lead marks the lead replied and stops its sequences.
6. A message from a lead with a running follow-up sequence counts as their reply.
7. Anything else becomes a new Service case (email-to-ticket), when Service is on and its setting allows it.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import ipaddress
import re
import socket
import ssl
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from email.utils import getaddresses, parseaddr
from typing import Any

from aira import suite
from aira.suite import kit

SETTINGS_KEY = "inbound_mail"
STATE_KEY = "inbound_mail_state"
PASSWORD_SECRET = "suite_imap_password"
SECURITY = ("ssl", "starttls", "none")
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "imap_host": "",
    "imap_port": 993,
    "imap_security": "ssl",
    "imap_username": "",
    "folder": "INBOX",
}
MAX_PER_PASS = 50
MAX_BYTES = 5_000_000  # a larger message (big attachments) is recorded and skipped
TIMEOUT = 20
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS suite_inbound (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL,
        uid INTEGER NOT NULL DEFAULT 0,
        from_email TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        handled_by TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        received_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS suite_inbound_message ON suite_inbound (message_id)",
    "CREATE INDEX IF NOT EXISTS suite_inbound_at ON suite_inbound (received_at)",
)
REFERENCE = re.compile(r"\[#(\d{3,9})\]")
_ANGLE = re.compile(r"<[^<>\s]{3,300}>")
_QUOTE_START = re.compile(
    r"^(?:On .{4,200} wrote:|-{2,}\s*Original Message\s*-{2,}|_{5,}|From: .+)$",
    re.I | re.M,
)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "suite.inbound.v1", SCHEMA)


def settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, "core", SETTINGS_KEY, {}) or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def state(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "uidvalidity": 0,
        "last_uid": 0,
        "folder": "",
        "last_checked_at": "",
        "last_error": "",
        "handled": 0,
        **(kit.get_setting(connection, "core", STATE_KEY, {}) or {}),
    }


def _save_state(connection: sqlite3.Connection, values: Mapping[str, Any]) -> None:
    kit.set_setting(connection, "core", STATE_KEY, {**state(connection), **values})
    connection.commit()


def _loopback(host: str) -> bool:
    name = host.strip().lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        return False


def clean_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    merged = {**settings(connection), **{k: v for k, v in raw.items() if k in DEFAULTS}}
    security = kit.choice(merged["imap_security"], "security", SECURITY, default="ssl")
    host = kit.text(merged["imap_host"], "the mail server", max_len=200).lower()
    if security == "none" and host and not _loopback(host):
        raise ValueError(
            "Use SSL or STARTTLS: without them the mailbox password crosses the network unencrypted. Plain IMAP "
            "is only for a mail bridge on this computer (127.0.0.1)."
        )
    clean = {
        "enabled": kit.flag(merged["enabled"]),
        "imap_host": host,
        "imap_port": kit.integer(
            merged["imap_port"],
            "the mail server port",
            default=993,
            minimum=1,
            maximum=65535,
        ),
        "imap_security": security,
        "imap_username": kit.text(
            merged["imap_username"], "the mailbox username", max_len=200
        ),
        "folder": kit.text(merged["folder"], "the folder", max_len=100) or "INBOX",
    }
    if clean["enabled"] and not (clean["imap_host"] and clean["imap_username"]):
        raise ValueError(
            "Enter the mail server and the mailbox username to read incoming mail."
        )
    return clean


def save_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Check (a hosted build refuses internal mail servers), then save. A new server or folder starts again from
    its newest message."""
    before = settings(connection)
    clean = clean_settings(connection, raw)
    if clean["imap_host"]:
        check_host(clean["imap_host"], int(clean["imap_port"]))
    kit.set_setting(connection, "core", SETTINGS_KEY, clean)
    if (before["imap_host"], before["imap_username"], before["folder"]) != (
        clean["imap_host"],
        clean["imap_username"],
        clean["folder"],
    ):
        kit.set_setting(connection, "core", STATE_KEY, {})
    connection.commit()
    return clean


def check_host(host: str, port: int) -> list[str] | None:
    """A hosted build's public addresses for ``host`` (internal ones refused, as for SMTP); None locally."""
    from aira.leads import enrich
    from aira.leads import mail as transport

    if not transport._strict():  # noqa: SLF001 - the same hosted/local test the SMTP check uses
        return None
    try:
        return enrich._check_addresses(host, port, allow_local=False)  # noqa: SLF001
    except enrich.FetchError as error:
        raise ValueError(
            f"The mail server {host} cannot be used here: {error}"
        ) from None


def view(connection: sqlite3.Connection, secret_store: Any) -> dict[str, Any]:
    """What the settings screen shows (never the password)."""
    ensure(connection)
    current = settings(connection)
    progress = state(connection)
    recent = kit.fetch_all(
        connection,
        "SELECT from_email, subject, handled_by, detail, received_at FROM suite_inbound ORDER BY received_at DESC LIMIT 10",
    )
    return {
        "settings": current,
        "has_password": bool(
            secret_store.get(connection, PASSWORD_SECRET) if secret_store else ""
        ),
        "last_checked_at": progress["last_checked_at"],
        "last_error": progress["last_error"],
        "handled": progress["handled"],
        "recent": recent,
    }


# ---------------------------------------------------------------------------
# a message, read
# ---------------------------------------------------------------------------


@dataclass
class Inbound:
    """One incoming message, read into what the apps need."""

    message_id: str
    from_email: str
    from_name: str = ""
    to: list[str] = field(default_factory=list)
    subject: str = ""
    text: str = ""  # the new text, without the quoted message it answers
    in_reply_to: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    automatic: bool = False  # an out-of-office, an auto-reply or list mail
    bounced: list[dict[str, str]] = field(
        default_factory=list
    )  # [{"email", "status", "diagnostic"}]
    is_report: bool = False  # a delivery status notification (failed for good or not)
    uid: int = 0

    @property
    def threads(self) -> list[str]:
        """The Message-IDs this message answers, nearest first."""
        seen: list[str] = []
        for item in [*self.in_reply_to, *reversed(self.references)]:
            if item not in seen:
                seen.append(item)
        return seen

    @property
    def reference(self) -> int | None:
        found = REFERENCE.search(self.subject)
        return int(found.group(1)) if found else None


def _ids(value: Any) -> list[str]:
    return _ANGLE.findall(str(value or ""))


def _plain(part: Message) -> str:
    """A text part as text (an HTML part with its tags removed)."""
    import html

    reader = getattr(
        part, "get_content", None
    )  # an EmailMessage (policy "default") has it
    try:
        content = reader() if callable(reader) else None
    except (LookupError, ValueError):
        content = None
    if content is None:
        payload = part.get_payload(decode=True) or b""
        content = (
            payload.decode("utf-8", "replace")
            if isinstance(payload, bytes)
            else str(payload)
        )
    text = str(content or "")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
    return text


def strip_quote(text: str) -> str:
    """The new part of a reply: the quoted message it answers (``On ... wrote:``, ``>`` lines) is dropped."""
    body = text.replace("\r\n", "\n")
    found = _QUOTE_START.search(body)
    if found and found.start() > 0:
        body = body[: found.start()]
    lines = [line for line in body.split("\n") if not line.startswith(">")]
    return "\n".join(lines).strip()


def _delivery_status(mail: Message) -> list[dict[str, str]]:
    """Per-recipient fields of a delivery status report (``message/delivery-status``), as dicts."""
    out: list[dict[str, str]] = []
    for part in mail.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload()
        blocks = payload if isinstance(payload, list) else []
        if not blocks:
            raw = payload if isinstance(payload, str) else ""
            blocks = [
                email.message_from_string(chunk) for chunk in re.split(r"\n\s*\n", raw)
            ]
        for block in blocks:
            if isinstance(block, Message) and (
                block.get("Final-Recipient") or block.get("Original-Recipient")
            ):
                out.append({k.lower(): str(v) for k, v in block.items()})
    return out


def _bounces(mail: Message, text: str) -> tuple[bool, list[dict[str, str]]]:
    """(is a delivery report, the addresses that failed for good). Only a permanent failure (Action: failed, a 5.x.x
    status) counts; a delay is not a bounce."""
    report = mail.get_content_type() == "multipart/report" and "delivery-status" in str(
        mail.get_param("report-type") or ""
    )
    fields = _delivery_status(mail)
    sender = parseaddr(str(mail.get("From") or ""))[1].lower()
    if not fields and sender.split("@")[0] in ("mailer-daemon", "postmaster"):
        # an older bounce without a machine-readable part: read the same fields from the text
        for chunk in re.split(r"\n\s*\n", text):
            if re.search(r"(?im)^Final-Recipient:", chunk):
                fields.append(
                    {
                        key.strip().lower(): value.strip()
                        for key, value in re.findall(
                            r"(?m)^([A-Za-z-]+):\s*(.+)$", chunk
                        )
                    }
                )
    report = report or bool(fields)
    failed: list[dict[str, str]] = []
    for item in fields:
        recipient = item.get("final-recipient") or item.get("original-recipient") or ""
        address = recipient.split(";", 1)[-1].strip().strip("<>").lower()
        action = item.get("action", "").strip().lower()
        status = item.get("status", "").strip()
        if address and action == "failed" and status.startswith("5"):
            failed.append(
                {
                    "email": address,
                    "status": status,
                    "diagnostic": item.get("diagnostic-code", "")[:200],
                }
            )
    return report, failed


def _automatic(mail: Message) -> bool:
    submitted = str(mail.get("Auto-Submitted") or "").strip().lower()
    precedence = str(mail.get("Precedence") or "").strip().lower()
    return bool(
        (submitted and submitted != "no")
        or mail.get("X-Autoreply")
        or mail.get("X-Autorespond")
        or str(mail.get("X-Auto-Response-Suppress") or "").lower() in ("all", "oof")
        or precedence in ("bulk", "junk", "list", "auto_reply")
        or mail.get("List-Id")
    )


def parse(raw: bytes, *, uid: int = 0) -> Inbound:
    """Read one message (the raw RFC 5322 bytes)."""
    mail = email.message_from_bytes(raw, policy=email.policy.default)
    name, address = parseaddr(str(mail.get("From") or ""))
    body_part = None
    try:
        body_part = mail.get_body(preferencelist=("plain", "html"))  # type: ignore[attr-defined]
    except (AttributeError, KeyError):
        body_part = None
    text = _plain(body_part) if body_part is not None else ""
    report, failed = _bounces(mail, text)
    message_id = (_ids(mail.get("Message-ID")) or [""])[0]
    if not message_id:
        message_id = f"<{hashlib.sha256(raw).hexdigest()[:40]}@inbound.monosage>"
    return Inbound(
        message_id=message_id,
        from_email=address.strip().lower(),
        from_name=kit.text(name, "a name", max_len=200) if name else "",
        to=[a.lower() for _, a in getaddresses([str(mail.get("To") or "")]) if a],
        subject=" ".join(str(mail.get("Subject") or "").split())[:300],
        text=strip_quote(text)[:10000],
        in_reply_to=_ids(mail.get("In-Reply-To")),
        references=_ids(mail.get("References")),
        automatic=_automatic(mail),
        bounced=failed,
        is_report=report,
        uid=uid,
    )


# ---------------------------------------------------------------------------
# where a message goes
# ---------------------------------------------------------------------------

ConsumerFn = Callable[[sqlite3.Connection, Inbound], str]
# Consumers other parts of Monosage register: fn(connection, message) -> a short note of what was done ("" passes
# the message on). They run after bounces, auto-replies and unsubscribes, before threading and email-to-ticket.
# Careers mailbox sync (F19) is meant to register here as "careers"; it is not built yet.
CONSUMERS: dict[str, ConsumerFn] = {}


def consumer(name: str) -> Callable[[ConsumerFn], ConsumerFn]:
    def wrap(fn: ConsumerFn) -> ConsumerFn:
        CONSUMERS[name] = fn
        return fn

    return wrap


def _enabled(app: str) -> bool:
    return suite.app_enabled(app)


def _bounce(connection: sqlite3.Connection, message: Inbound) -> str:
    from aira.leads import mail as transport
    from aira.leads import sequences

    added = []
    for failure in message.bounced:
        detail = f"{failure['status']} {failure['diagnostic']}".strip()
        transport.record_bounce(connection, failure["email"], detail)
        sequences.stop_for_address(connection, failure["email"], "bounced")
        added.append(failure["email"])
    return f"bounce: {', '.join(added)}" if added else "delivery report"


def _unsubscribe(connection: sqlite3.Connection, message: Inbound) -> str:
    if not message.subject.strip().lower().startswith("unsubscribe"):
        return ""
    from aira.leads import mail as transport
    from aira.leads import sequences

    done = []
    if _enabled("marketing"):
        from aira.suite.marketing import service as marketing

        if marketing.unsubscribe_address(connection, message.from_email):
            done.append("newsletter")
    if sequences.stop_for_address(connection, message.from_email, "unsubscribed"):
        done.append("sequences")
    try:
        transport.add_suppression(
            connection, message.from_email, "Asked to unsubscribe by email"
        )
        done.append("do-not-contact")
    except ValueError:
        pass
    return "unsubscribe: " + ", ".join(done) if done else "unsubscribe"


def _service_reply(connection: sqlite3.Connection, message: Inbound) -> str:
    if not _enabled("service"):
        return ""
    from aira.suite.service import desk

    case = None
    if message.reference is not None:
        case = desk.case_by_number(connection, message.reference)
    if case is None:
        sent = kit.sent_message(connection, message.threads)
        if sent and sent.get("ref_type") == "case":
            case = desk.store.get_case(connection, sent["ref_id"])
    if case is None:
        return ""
    outcome = desk.email_reply(connection, case, _as_dict(message))
    return f"service: {outcome}" if outcome else ""


def _lead_reply(connection: sqlite3.Connection, message: Inbound) -> str:
    from aira.leads import sequences

    found = sequences.record_reply(
        connection, message.from_email, message.threads, message.subject
    )
    return f"leads: {found}" if found else ""


def _new_case(connection: sqlite3.Connection, message: Inbound) -> str:
    if not _enabled("service"):
        return ""
    from aira.suite.service import desk

    case = desk.email_to_case(connection, _as_dict(message))
    return f"service: new case {case['reference']}" if case else ""


def _as_dict(message: Inbound) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "from_email": message.from_email,
        "from_name": message.from_name,
        "subject": message.subject,
        "text": message.text,
        "threads": message.threads,
    }


def dispatch(connection: sqlite3.Connection, message: Inbound) -> str:
    """Hand one message to whatever it is for; returns what was done (recorded in ``suite_inbound``)."""
    if message.is_report or message.bounced:
        return _bounce(connection, message)
    own = {
        str(kit.email_settings(connection).get(key) or "").lower()
        for key in ("from_email", "reply_to")
    } - {""}
    if not message.from_email or message.from_email in own:
        return "ignored: our own address"
    if message.automatic:
        return "ignored: automatic reply"
    steps: list[tuple[str, ConsumerFn]] = [
        ("unsubscribe", _unsubscribe),
        *CONSUMERS.items(),
        ("service", _service_reply),
        ("leads", _lead_reply),
        ("new case", _new_case),
    ]
    for name, step in steps:
        try:
            done = step(connection, message)
        except Exception:  # noqa: BLE001 - one consumer must not lose the message for the others
            suite.logger.exception("Incoming mail: %s failed", name)
            connection.rollback()
            continue
        if done:
            connection.commit()
            return done
    return "not matched"


def handle(
    connection: sqlite3.Connection, raw: bytes, *, uid: int = 0
) -> dict[str, Any]:
    """Read, route and record one message, once (a Message-ID already handled is skipped)."""
    ensure(connection)
    message = parse(raw, uid=uid)
    if kit.fetch_one(
        connection,
        "SELECT id FROM suite_inbound WHERE message_id = ?",
        (message.message_id,),
    ):
        return {"message_id": message.message_id, "handled_by": "duplicate"}
    done = dispatch(connection, message)
    connection.execute(
        """INSERT OR IGNORE INTO suite_inbound (id, message_id, uid, from_email, subject, handled_by, detail, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kit.new_id(),
            message.message_id,
            uid,
            message.from_email[:254],
            message.subject[:300],
            done.split(":", 1)[0][:40],
            done[:300],
            kit.now(),
        ),
    )
    connection.commit()
    return {"message_id": message.message_id, "handled_by": done}


# ---------------------------------------------------------------------------
# the mailbox (IMAP)
# ---------------------------------------------------------------------------


class _PinnedIMAP(imaplib.IMAP4):
    """IMAP (plain or STARTTLS) to an address that was already checked; STARTTLS verifies the server's name."""

    def __init__(self, host: str, port: int, address: str, *, timeout: float) -> None:
        self._pinned = address
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        return socket.create_connection((self._pinned, self.port), timeout)


class _PinnedIMAPSSL(imaplib.IMAP4_SSL):
    """IMAP over TLS to an address that was already checked, verifying the certificate for the server's name."""

    def __init__(
        self,
        host: str,
        port: int,
        address: str,
        *,
        timeout: float,
        ssl_context: ssl.SSLContext,
    ) -> None:
        self._pinned = address
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        raw = socket.create_connection((self._pinned, self.port), timeout)
        return self.ssl_context.wrap_socket(raw, server_hostname=self.host)


def open_mailbox(current: Mapping[str, Any], password: str) -> imaplib.IMAP4:
    """Connect and sign in (the caller logs out)."""
    host = str(current["imap_host"])
    port = int(current["imap_port"])
    security = str(current["imap_security"])
    if security == "none" and not _loopback(host):
        raise ValueError("Plain IMAP is only for a mail bridge on this computer.")
    pinned = check_host(host, port)
    context = ssl.create_default_context()
    client: imaplib.IMAP4
    if security == "ssl":
        client = (
            _PinnedIMAPSSL(host, port, pinned[0], timeout=TIMEOUT, ssl_context=context)
            if pinned
            else imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=TIMEOUT)
        )
    else:
        client = (
            _PinnedIMAP(host, port, pinned[0], timeout=TIMEOUT)
            if pinned
            else imaplib.IMAP4(host, port, timeout=TIMEOUT)
        )
        if security == "starttls":
            client.starttls(ssl_context=context)
    try:
        client.login(str(current["imap_username"]), password)
    except imaplib.IMAP4.error:
        try:
            client.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        raise ValueError("The mail server refused the username or password.") from None
    return client


def _quoted(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _number(data: Any, name: str = "") -> int:
    """The number after ``name`` in an IMAP response (the first number when ``name`` is empty)."""
    text = b" ".join(item for item in (data or []) if isinstance(item, bytes)).decode(
        "ascii", "replace"
    )
    found = re.search(rf"{re.escape(name)}\s+(\d+)" if name else r"(\d+)", text, re.I)
    return int(found.group(1)) if found else 0


def _literal(data: Any) -> bytes:
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _uids(data: Any) -> list[int]:
    text = b" ".join(item for item in (data or []) if isinstance(item, bytes)).decode(
        "ascii", "replace"
    )
    return sorted({int(part) for part in text.split() if part.isdigit()})


def poll(
    connection: sqlite3.Connection,
    secret_store: Any,
    *,
    opener: Callable[[Mapping[str, Any], str], Any] | None = None,
    limit: int = MAX_PER_PASS,
) -> dict[str, Any]:
    """Read new messages from the mailbox and route each one. Never raises for a mailbox problem: it is recorded
    (``last_error``) and shown in Business hub > Email."""
    ensure(connection)
    current = settings(connection)
    if not current["enabled"] or not current["imap_host"]:
        return {"ran": False}
    password = (
        secret_store.get(connection, PASSWORD_SECRET) if secret_store else ""
    ) or ""
    progress = state(connection)
    result: dict[str, Any] = {"ran": True, "read": 0, "handled": {}}
    client = None
    try:
        client = (opener or open_mailbox)(current, password)
        folder = _quoted(str(current["folder"]))
        status, _ = client.select(
            folder, readonly=True
        )  # EXAMINE: nothing in the mailbox changes
        if status != "OK":
            raise ValueError(f"The folder {current['folder']} could not be opened.")
        validity = _number(client.response("UIDVALIDITY")[1])
        if not validity:
            validity = int(progress["uidvalidity"] or 0) or 1
        last = int(progress["last_uid"] or 0)
        if (
            validity != int(progress["uidvalidity"] or 0)
            or progress["folder"] != current["folder"]
        ):
            # the first check (or the server renumbered the folder): start from the newest message
            _, data = client.status(folder, "(UIDNEXT)")
            last = max(0, _number(data, "UIDNEXT") - 1)
            if not last:
                _, data = client.uid("SEARCH", "ALL")
                found = _uids(data)
                last = found[-1] if found else 0
            _save_state(
                connection,
                {
                    "uidvalidity": validity,
                    "last_uid": last,
                    "folder": current["folder"],
                    "last_checked_at": kit.now(),
                    "last_error": "",
                },
            )
            result["started_at_uid"] = last
            return result
        _, data = client.uid("SEARCH", "UID", f"{last + 1}:*")
        for uid in [u for u in _uids(data) if u > last][: max(1, min(limit, 500))]:
            _, size_data = client.uid("FETCH", str(uid), "(RFC822.SIZE)")
            size = _number(size_data, "RFC822.SIZE")
            if size > MAX_BYTES:
                outcome = {"handled_by": "skipped: too large"}
            else:
                _, fetched = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = _literal(fetched)
                outcome = (
                    handle(connection, raw, uid=uid) if raw else {"handled_by": "empty"}
                )
            kind = str(outcome["handled_by"]).split(":", 1)[0]
            result["handled"][kind] = result["handled"].get(kind, 0) + 1
            result["read"] += 1
            last = uid
            _save_state(connection, {"last_uid": last})
        _save_state(
            connection,
            {
                "last_checked_at": kit.now(),
                "last_error": "",
                "handled": int(state(connection)["handled"] or 0) + result["read"],
            },
        )
    except (imaplib.IMAP4.error, OSError, ssl.SSLError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, ValueError)
            else f"{type(error).__name__}: {error}"
        )
        _save_state(
            connection, {"last_checked_at": kit.now(), "last_error": message[:300]}
        )
        result["error"] = message[:300]
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
    return result
