"""Applying by email: the postings that ask for one, and the one message Monosage sends for them.

Most application systems take applications only through their web form (the browser engine,
:mod:`aira.apply_engine`). Some postings instead ask for an email: a ``mailto:`` apply link, or wording such as
"send your resume to jobs@example.com". This module finds those addresses, conservatively, and builds the
message; the server (``auto_apply_assistant``) sends it through the mail the account already has and records
the submission with method ``email``.

Rules, enforced here and by the caller:

* **Finding the address.** A ``mailto:`` application link is always an apply address. In a description, an
  address counts only when apply wording comes right before it in the same sentence ("send your CV to",
  "apply by email", "email your application to", the per-language table :data:`APPLY_CUES`) and nothing
  in that sentence says it is for something else (accommodations, privacy, questions, agencies, "do not
  send"): an address in a footer or a "questions? write to" line is not an apply address. No-reply and
  privacy mailboxes never are.
* **One email per job, ever.** :func:`claim` takes the job's row in ``email_applications`` before anything is
  sent; a job whose email was sent, or whose send was started and never confirmed, is never written to again
  on its own. Only a send the mail server refused (``failed``) may be tried again.
* **Nothing is invented.** The message carries the job's title, company and reference and the person's own
  name and contact details; the resume and the cover letter are attached as files (each at most
  :data:`MAX_ATTACHMENT_BYTES`, together at most :data:`MAX_TOTAL_BYTES`).

Locale note: apply wording is the only signal that an address is an apply address, so it is matched against
the explicit per-language table :data:`APPLY_CUES` (English, Spanish, French, German, Portuguese, Italian and
Dutch). A posting in another language that asks for an email is not detected; it is applied to some other way.
"""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

TABLE = "email_applications"
STATUSES = ("sending", "sent", "failed")
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MAX_SUBJECT = 200
MAX_BODY = 20_000
# How far before an address the apply wording may be (characters, within the same sentence).
_CUE_WINDOW = 160

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*\.[A-Za-z]{2,24})(?![\w-])"
)
# Mailboxes that are never where applications go.
_NEVER_LOCAL = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|privacy|dpo|gdpr|data-?protection|accommodations?|"
    r"accessibility|legal|compliance|security|abuse|fraud|press|media|webmaster|postmaster|unsubscribe)$",
    re.IGNORECASE,
)
# Sentence ends (an address in the next sentence does not borrow this one's apply wording).
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)|\n\s*\n|\n[-*•]")

# Apply wording that comes before an apply address, per language. Each entry is a regular expression matched
# (case-insensitively) against the text between the start of the sentence and the address.
APPLY_CUES: dict[str, tuple[str, ...]] = {
    "en": (
        r"\b(?:send|email|e-mail|mail|forward|submit|direct)\b[^@]{0,80}\b(?:resumes?|résumés?|cvs?|curriculum vitae|"
        r"applications?|cover letters?|portfolios?)\b[^@]{0,40}\bto\b",
        r"\bapply\b[^@]{0,40}\b(?:by|via|through|over)\s+e-?mail\b",
        r"\bto apply\b[^@]{0,60}\b(?:send|email|e-mail|write|contact)\b",
        r"\b(?:applications?|resumes?|cvs?)\b[^@]{0,40}\b(?:should|can|may|must|are to)\s+be\s+(?:sent|emailed|e-mailed|"
        r"submitted|directed|forwarded)\b",
        r"\b(?:interested|qualified)\s+(?:candidates|applicants|persons|people|individuals)\b[^@]{0,60}\b(?:send|email|"
        r"e-mail|submit|forward|apply)\b",
        r"\bemail\s+(?:us\s+)?your\s+(?:resume|cv|application)\b",
    ),
    "es": (
        r"\b(?:envía|envia|envíe|envie|envíen|envien|manda|mande|remite|remita)\b[^@]{0,80}\b(?:cv|currículum|curriculum|"
        r"hoja de vida|solicitud|candidatura)\b[^@]{0,40}\b(?:a|al)\b",
        r"\b(?:postula|postúlate|postulate|aplica)\b[^@]{0,40}\b(?:por|vía|via)\s+(?:correo|e-?mail)\b",
    ),
    "fr": (
        r"\b(?:envoyez|envoyer|adressez|adresser|transmettez|transmettre)\b[^@]{0,80}\b(?:cv|candidature|lettre de "
        r"motivation)\b[^@]{0,40}(?:\bà\b|\ba\b|\bau\b)",
        r"\bpostulez\b[^@]{0,40}\bpar\s+(?:e-?mail|courriel|mail)\b",
    ),
    "de": (
        r"\b(?:senden|schicken|richten)\s+sie\b[^@]{0,80}\b(?:bewerbung|bewerbungsunterlagen|lebenslauf|cv)\b[^@]{0,40}"
        r"\b(?:an|per)\b",
        r"\b(?:bewerbung|bewerbungsunterlagen)\b[^@]{0,60}\bper\s+e-?mail\b[^@]{0,30}\ban\b",
    ),
    "pt": (
        r"\b(?:envie|enviar|mande|encaminhe)\b[^@]{0,80}\b(?:cv|currículo|curriculo|candidatura)\b[^@]{0,40}\bpara\b",
        r"\bcandidate-se\b[^@]{0,40}\bpor\s+e-?mail\b",
    ),
    "it": (
        r"\b(?:invia|inviate|inviare|manda|mandate|inoltra)\b[^@]{0,80}\b(?:cv|curriculum|candidatura)\b[^@]{0,40}\b(?:a|al|"
        r"all)\b",
        r"\bcandidati\b[^@]{0,40}\b(?:via|per)\s+e-?mail\b",
    ),
    "nl": (
        r"\b(?:stuur|stuurt|mail|mailt|zend|zendt)\b[^@]{0,80}\b(?:cv|sollicitatie|motivatiebrief)\b[^@]{0,40}\bnaar\b",
        r"\bsolliciteren\b[^@]{0,40}\bper\s+e-?mail\b",
    ),
}
_CUES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for patterns in APPLY_CUES.values()
    for pattern in patterns
)
# Wording that says the address in this sentence is for something other than applying.
_NOT_FOR_APPLYING = re.compile(
    r"accommodat|disabilit|assistance|accessib|questions?\b|inquir|enquir|privacy|personal data|data protection|gdpr|"
    r"unsolicited|agenc(?:y|ies)|recruit(?:ers|ment agencies)\b|do not (?:send|email|e-mail)|don't (?:send|email)|"
    r"not accept|no (?:longer )?accept|phishing|fraud|scam|report|complain|press|media inquir|unsubscribe|"
    r"no enví|no envi|ne pas envoyer|nicht (?:senden|schicken)|não envie|non inviare|niet (?:sturen|mailen)",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"\b(?:reference|ref|job\s*(?:id|code|number|no)|requisition(?:\s*(?:id|number|no))?|req(?:\s*(?:id|no))?|"
    r"vacancy\s*(?:id|number)|posting\s*(?:id|number))(?![a-z])\.?\s*(?:number|no\.?|id)?\s*[:#]?\s*#?\s*"
    r"([A-Z0-9][A-Z0-9_/-]{2,30})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ApplyAddress:
    """Where a posting asks for applications by email."""

    address: str
    how: str  # "mailto" (the application link) or "posting" (the description's wording)
    evidence: str  # the sentence or link that says so (shown to the person)
    subject: str = ""  # the subject the posting asks for (a mailto subject), if any
    reference: str = ""  # the job reference the posting names, if any

    def public(self) -> dict[str, str]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# finding the address
# --------------------------------------------------------------------------- #


def _usable(address: str) -> bool:
    local, _, domain = address.partition("@")
    if not local or not domain or _NEVER_LOCAL.match(local):
        return False
    # an example or placeholder address is not an apply address
    return domain.casefold() not in {
        "example.com",
        "example.org",
        "domain.com",
        "email.com",
    }


def _mailto(url: str) -> ApplyAddress | None:
    text = str(url or "").strip()
    if not text.casefold().startswith("mailto:"):
        return None
    parts = urlsplit(text)
    address = unquote(parts.path).split(",")[0].strip()
    match = _EMAIL_RE.fullmatch(address)
    if not match or not _usable(address):
        return None
    query = parse_qs(parts.query)
    subject = " ".join(str((query.get("subject") or [""])[0]).split())[:MAX_SUBJECT]
    return ApplyAddress(
        address=address.casefold(),
        how="mailto",
        evidence=text[:300],
        subject=subject,
    )


def _sentence_start(text: str, index: int) -> int:
    start = max(0, index - _CUE_WINDOW)
    last = start
    for match in _SENTENCE_END.finditer(text, start, index):
        last = match.end()
    return last


def _sentence_end(text: str, index: int) -> int:
    match = _SENTENCE_END.search(text, index)
    return match.start() + 1 if match else min(len(text), index + 200)


def reference_in(text: str) -> str:
    """The job reference a posting names ("Reference: ENG-2041"), if any (it has at least one digit)."""
    for match in _REFERENCE_RE.finditer(str(text or "")):
        token = match.group(1)
        if any(char.isdigit() for char in token):
            return token[:40]
    return ""


def find_apply_address(
    description: str, application_url: str = "", job_url: str = ""
) -> ApplyAddress | None:
    """The address a posting asks applications to be emailed to, or None.

    A ``mailto:`` application (or job) link wins; otherwise the first address in the description whose own
    sentence asks for applications by email and says nothing that makes it something else.
    """
    for url in (application_url, job_url):
        found = _mailto(url)
        if found:
            return found
    text = str(description or "")[:50_000]
    reference = reference_in(text)
    for match in _EMAIL_RE.finditer(text):
        address = match.group(1)
        if not _usable(address):
            continue
        start = _sentence_start(text, match.start())
        before = text[start : match.start()]
        after = text[match.end() : _sentence_end(text, match.end())]
        if not any(cue.search(before) for cue in _CUES):
            continue
        if _NOT_FOR_APPLYING.search(before) or _NOT_FOR_APPLYING.search(after):
            continue
        sentence = " ".join((before + address + after).split())
        return ApplyAddress(
            address=address.casefold(),
            how="posting",
            evidence=sentence[:300],
            reference=reference_in(sentence) or reference,
        )
    return None


def target_for(job: Mapping[str, Any]) -> ApplyAddress | None:
    """:func:`find_apply_address` for a job row (its description and links)."""
    return find_apply_address(
        str(job.get("description") or ""),
        str(job.get("application_url") or ""),
        str(job.get("job_url") or ""),
    )


# --------------------------------------------------------------------------- #
# the message
# --------------------------------------------------------------------------- #


def _clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def person_name(profile: Mapping[str, Any]) -> str:
    return _clean(
        profile.get("full_name")
        or " ".join(
            filter(
                None,
                (_clean(profile.get("first_name")), _clean(profile.get("last_name"))),
            )
        ),
        120,
    )


def default_subject(job: Mapping[str, Any], target: ApplyAddress, name: str) -> str:
    if target.subject:
        return target.subject
    title = _clean(job.get("title"), 120) or "the open position"
    subject = f"Application for {title}"
    if target.reference:
        subject += f" ({target.reference})"
    if name:
        subject += f" - {name}"
    return subject[:MAX_SUBJECT]


def default_body(
    job: Mapping[str, Any],
    profile: Mapping[str, Any],
    target: ApplyAddress,
    *,
    has_letter: bool,
) -> str:
    """A short, factual cover email: the role, what is attached and the person's own contact details."""
    name = person_name(profile)
    company = _clean(job.get("company"), 120)
    title = _clean(job.get("title"), 120) or "the open position"
    greeting = f"Dear {company} hiring team," if company else "Dear hiring team,"
    role = f"the {title} position" + (
        f" (reference {target.reference})" if target.reference else ""
    )
    attached = (
        "My resume and cover letter are attached."
        if has_letter
        else "My resume is attached."
    )
    contact = [
        line
        for line in (
            name,
            _clean(profile.get("email"), 200),
            _clean(profile.get("phone"), 60),
            _clean(profile.get("linkedin_url"), 300),
        )
        if line
    ]
    return "\n".join(
        [
            greeting,
            "",
            f"I would like to apply for {role}. {attached}",
            "",
            "Thank you for your time and consideration. I look forward to hearing from you.",
            "",
            "Best regards,",
            *contact,
        ]
    )


@dataclass(frozen=True, slots=True)
class Attachment:
    path: str
    kind: str  # "resume" or "cover_letter"

    @property
    def name(self) -> str:
        return Path(self.path).name

    def size(self) -> int:
        return Path(self.path).stat().st_size


def check_attachments(attachments: Sequence[Attachment]) -> list[dict[str, Any]]:
    """The files as the preview lists them; ValueError when one is too large to email."""
    listed = []
    total = 0
    for item in attachments:
        size = item.size()
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"{item.name} is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB, which mail servers "
                "reject. Upload a smaller file in Documents."
            )
        total += size
        listed.append({"name": item.name, "kind": item.kind, "bytes": size})
    if total > MAX_TOTAL_BYTES:
        raise ValueError(
            f"The attachments come to more than {MAX_TOTAL_BYTES // (1024 * 1024)} MB, which mail servers reject."
        )
    return listed


def compose(
    settings: Mapping[str, Any],
    *,
    to: str,
    subject: str,
    body: str,
    attachments: Sequence[Attachment],
    from_name: str = "",
    reply_to: str = "",
) -> EmailMessage:
    """The email as it is sent: the person's name on it, Reply-To their own address, the files attached."""
    sender = str(settings.get("from_email") or "")
    if not sender:
        raise ValueError("Set up email first: no sender address.")
    message = EmailMessage()
    message["From"] = formataddr(
        (_clean(from_name or settings.get("from_name"), 100), sender)
    )
    message["To"] = to
    message["Subject"] = _clean(subject, MAX_SUBJECT) or "Application"
    message["Reply-To"] = str(reply_to or settings.get("reply_to") or sender)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or "localhost")
    message["X-Mailer"] = "Monosage"
    message.set_content(str(body).replace("\r\n", "\n").strip()[:MAX_BODY] + "\n")
    check_attachments(attachments)
    for item in attachments:
        kind, _ = mimetypes.guess_type(item.name)
        maintype, _, subtype = (kind or "application/octet-stream").partition("/")
        message.add_attachment(
            Path(item.path).read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=item.name,
        )
    return message


def evidence_text(message: EmailMessage, sent_at: str) -> str:
    """What is kept as the application's evidence: the headers, the text and the attachment names (no files)."""
    lines = [
        f"Sent: {sent_at}",
        *(
            f"{name}: {message[name]}"
            for name in ("From", "To", "Reply-To", "Subject", "Date", "Message-ID")
        ),
        "",
    ]
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if filename:
            size = len(part.get_payload(decode=True) or b"")
            lines.append(f"Attachment: {filename} ({max(1, round(size / 1024))} KB)")
        elif part.get_content_type() == "text/plain":
            lines.extend(["", str(part.get_content()).strip(), ""])
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------- #
# the ledger: one email per job, ever
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection) -> None:
    """The ledger table and the plan switch. Additive and idempotent."""
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            job_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
            attachments TEXT NOT NULL DEFAULT '[]', run_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sent_at TEXT
        )"""
    )
    plans = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'autopilot_plans'"
    ).fetchone()
    if plans:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(autopilot_plans)")
        }
        if "email_apply" not in columns:
            connection.execute(
                "ALTER TABLE autopilot_plans ADD COLUMN email_apply INTEGER NOT NULL DEFAULT 0"
            )


def record(connection: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    ensure_schema(connection)
    row = connection.execute(
        f"SELECT * FROM {TABLE} WHERE job_id = ?", (job_id,)
    ).fetchone()  # noqa: S608
    if row is None:
        return None
    data = dict(row)
    data["attachments"] = json.loads(data.get("attachments") or "[]")
    return data


def claim(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    recipient: str,
    subject: str,
    attachments: Sequence[Mapping[str, Any]],
    run_id: str = "",
) -> bool:
    """Take the job's one email. True when this caller may send it now; False when it was sent, or a send was
    started and never confirmed (never sent again on its own). A refused send (``failed``) may be claimed again."""
    ensure_schema(connection)
    moment = _now()
    changed = connection.execute(
        f"""INSERT INTO {TABLE} (job_id, recipient, subject, status, attachments, run_id, created_at, updated_at)
        VALUES (?, ?, ?, 'sending', ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET recipient = excluded.recipient, subject = excluded.subject,
            status = 'sending', error = '', attachments = excluded.attachments, run_id = excluded.run_id,
            updated_at = excluded.updated_at
        WHERE {TABLE}.status = 'failed'""",  # noqa: S608
        (
            job_id,
            recipient,
            subject[:MAX_SUBJECT],
            json.dumps(list(attachments)),
            run_id,
            moment,
            moment,
        ),
    ).rowcount
    connection.commit()
    return changed > 0


def mark_sent(connection: sqlite3.Connection, job_id: str, message_id: str) -> str:
    moment = _now()
    connection.execute(
        f"UPDATE {TABLE} SET status = 'sent', message_id = ?, sent_at = ?, updated_at = ? WHERE job_id = ?",  # noqa: S608
        (message_id[:300], moment, moment, job_id),
    )
    connection.commit()
    return moment


def mark_failed(connection: sqlite3.Connection, job_id: str, error: str) -> None:
    connection.execute(
        f"UPDATE {TABLE} SET status = 'failed', error = ?, updated_at = ? WHERE job_id = ? AND status = 'sending'",  # noqa: S608
        (str(error)[:300], _now(), job_id),
    )
    connection.commit()


def blocked_reason(existing: Mapping[str, Any] | None) -> str:
    """Why the job's email may not be sent again ('' when it may)."""
    if not existing or existing.get("status") == "failed":
        return ""
    if existing.get("status") == "sent":
        return f"The application was already emailed to {existing.get('recipient')}."
    return (
        f"An email application to {existing.get('recipient')} was started and not confirmed. Check your Sent "
        "folder; if it went out, record it as applied by email."
    )


__all__ = [
    "APPLY_CUES",
    "MAX_ATTACHMENT_BYTES",
    "MAX_TOTAL_BYTES",
    "ApplyAddress",
    "Attachment",
    "blocked_reason",
    "check_attachments",
    "claim",
    "compose",
    "default_body",
    "default_subject",
    "ensure_schema",
    "evidence_text",
    "find_apply_address",
    "mark_failed",
    "mark_sent",
    "person_name",
    "record",
    "reference_in",
    "target_for",
]
