"""Email outreach for leads, with a person in the loop.

The flow is draft -> review -> approve -> send, and Aira only ever sends what a person approved:

* Drafts are written by the model (or a template) from facts the lead really has, shown in the outbox, and
  can be edited or rejected. Nothing is sent from a draft.
* Sending goes through the person's **own** mail server (SMTP), never a shared one, one message at a time
  with a pause between messages, up to a daily cap (default 20, at most 100).
* Every message carries the sender's name and postal address and a one-line way to opt out; the
  ``List-Unsubscribe`` header is set. A suppression list (emails, domains, phone numbers) is checked before
  every send and a person who opted out or asked not to be called is added to it.
* Leads in regions with strict consent rules for cold email (the EU, UK and Canada) are refused unless the
  sender has said they have a lawful basis. This is a guard, not legal advice.

It is a tool for one person writing to a few dozen businesses a day, not for bulk mailing.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from aira.leads import enrich, intent, outreach, store

SETTINGS_KEY = "leads_outreach"
PASSWORD_SECRET = "leads_smtp_password"
MAX_DAILY_CAP = 100
DEFAULT_DAILY_CAP = 20
MIN_DELAY = 2
DEFAULT_DELAY = 5
SEND_BATCH = 10
SECURITY = ("starttls", "ssl", "none")
STATUSES = ("draft", "approved", "sent", "failed", "rejected", "skipped")
FREEMAIL = frozenset(
    "gmail.com googlemail.com yahoo.com outlook.com hotmail.com live.com icloud.com aol.com proton.me protonmail.com msn.com".split()
)
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

DEFAULTS: dict[str, Any] = {
    "from_name": "",
    "from_email": "",
    "reply_to": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_username": "",
    "postal_address": "",
    "signature": "",
    "offer": "",
    "services": [],
    "calling_number": "",
    "daily_cap": DEFAULT_DAILY_CAP,
    "delay_seconds": DEFAULT_DELAY,
    "allow_regulated_regions": False,
}

# Where cold email to individuals needs prior consent. A guard on the lead's own location text.
_REGULATED = re.compile(
    r"\b(united kingdom|england|scotland|wales|northern ireland|ireland|germany|france|spain|italy|portugal|netherlands|belgium|"
    r"luxembourg|austria|poland|sweden|denmark|finland|norway|czech|slovakia|hungary|romania|bulgaria|croatia|slovenia|greece|"
    r"estonia|latvia|lithuania|malta|cyprus|canada|london|paris|berlin|madrid|amsterdam|dublin|toronto|vancouver|montreal)\b"
    r"|,\s*(uk|gb|de|fr|es|it|nl|be|ie|ca|ontario|quebec|british columbia|alberta)\s*$",
    re.I,
)


class SendError(Exception):
    """A message could not be sent (the reason is shown to the person)."""


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def normalize_settings(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    base = {**DEFAULTS, **(current or {})}
    out = dict(base)
    for key in (
        "from_name",
        "reply_to",
        "smtp_host",
        "smtp_username",
        "calling_number",
        "offer",
    ):
        if key in raw:
            out[key] = store.clean(raw.get(key), 300)
    if "from_email" in raw:
        email = store.clean(raw.get("from_email"), 200).lower()
        if email and not _EMAIL.match(email):
            raise ValueError("The from address is not a valid email address.")
        out["from_email"] = email
    if out["reply_to"] and not _EMAIL.match(out["reply_to"]):
        raise ValueError("The reply-to address is not a valid email address.")
    if "postal_address" in raw:
        out["postal_address"] = store.clean(raw.get("postal_address"), 300)
    if "signature" in raw:
        out["signature"] = store.clean(raw.get("signature"), 200)
    if "smtp_port" in raw:
        try:
            port = int(raw.get("smtp_port") or 0)
        except (TypeError, ValueError):
            raise ValueError("The mail server port must be a number.") from None
        if not 1 <= port <= 65535:
            raise ValueError("The mail server port must be from 1 to 65535.")
        out["smtp_port"] = port
    if "smtp_security" in raw:
        security = str(raw.get("smtp_security") or "").lower()
        if security not in SECURITY:
            raise ValueError("Security must be starttls, ssl or none.")
        out["smtp_security"] = security
    if "daily_cap" in raw:
        try:
            cap = int(raw["daily_cap"])
        except (TypeError, ValueError):
            raise ValueError("The daily cap must be a number.") from None
        if not 1 <= cap <= MAX_DAILY_CAP:
            raise ValueError(f"The daily cap must be from 1 to {MAX_DAILY_CAP}.")
        out["daily_cap"] = cap
    if "delay_seconds" in raw:
        try:
            delay = int(raw["delay_seconds"])
        except (TypeError, ValueError):
            raise ValueError(
                "The pause between emails must be a number of seconds."
            ) from None
        out["delay_seconds"] = max(MIN_DELAY, min(delay, 300))
    if "services" in raw:
        services = raw.get("services") or []
        if isinstance(services, str):
            services = [s for s in re.split(r"[,\s]+", services) if s]
        chosen = [str(s).lower() for s in services]
        unknown = [s for s in chosen if s not in intent.SERVICES]
        if unknown:
            raise ValueError(f"Unknown service: {unknown[0]}.")
        out["services"] = list(dict.fromkeys(chosen))
    if "allow_regulated_regions" in raw:
        out["allow_regulated_regions"] = bool(
            raw.get("allow_regulated_regions")
        ) and str(raw.get("allow_regulated_regions")).lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    return out


def get_settings(connection: Any) -> dict[str, Any]:
    import json

    row = connection.execute(
        "SELECT value FROM lead_settings WHERE key = ?", (SETTINGS_KEY,)
    ).fetchone()
    try:
        raw = json.loads(row[0]) if row else {}
    except ValueError:
        raw = {}
    try:
        return normalize_settings(raw if isinstance(raw, dict) else {})
    except ValueError:
        return dict(DEFAULTS)


def save_settings(connection: Any, raw: Mapping[str, Any]) -> dict[str, Any]:
    import json

    settings = normalize_settings(raw, get_settings(connection))
    connection.execute(
        "INSERT INTO lead_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTINGS_KEY, json.dumps(settings, ensure_ascii=False)),
    )
    connection.commit()
    return settings


def settings_problems(settings: Mapping[str, Any], has_password: bool) -> list[str]:
    """What is still missing before a message can be sent."""
    missing = []
    if not str(settings.get("from_name") or "").strip():
        missing.append("your name")
    if not _EMAIL.match(str(settings.get("from_email") or "")):
        missing.append("a from address")
    if not str(settings.get("smtp_host") or "").strip():
        missing.append("your mail server")
    if len(str(settings.get("postal_address") or "").strip()) < 10:
        missing.append(
            "a postal address for the footer (commercial email rules require one)"
        )
    if str(settings.get("smtp_username") or "") and not has_password:
        missing.append("the mail server password")
    return missing


# ---------------------------------------------------------------------------
# the mail server, the message, the guards
# ---------------------------------------------------------------------------


def _strict() -> bool:
    """A hosted (multi-tenant) build must never be pointed at internal addresses."""
    import os

    if os.environ.get("AIRA_LEADS_STRICT_SMTP", "") == "1":
        return True
    try:
        from aira.hosting import HOOKS

        return HOOKS.documents is not None or HOOKS.settings_overlay is not None
    except Exception:  # noqa: BLE001 - an unreadable seam means local
        return False


def check_smtp_host(host: str, port: int) -> None:
    if not _strict():
        return
    try:
        # a hosted build never relaxes this, whatever the environment says
        enrich._check_addresses(host, port, allow_local=False)
    except enrich.FetchError as error:
        raise SendError(
            f"The mail server {host} cannot be used here: {error}"
        ) from None


def sender_domain(settings: Mapping[str, Any]) -> str:
    return str(settings.get("from_email") or "").split("@")[-1] or "localhost"


def build_message(
    settings: Mapping[str, Any], *, to: str, subject: str, body: str
) -> EmailMessage:
    """The email as it will be sent: the approved text plus the sender's identity and a way to opt out."""
    name = store.clean(settings.get("from_name"), 100)
    address = str(settings["from_email"])
    reply_to = str(settings.get("reply_to") or address)
    message = EmailMessage()
    message["From"] = formataddr((name, address))
    message["To"] = to
    message["Subject"] = store.clean(subject, 200) or "Hello"
    message["Reply-To"] = reply_to
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender_domain(settings))
    message["List-Unsubscribe"] = f"<mailto:{reply_to}?subject=unsubscribe>"
    message["X-Mailer"] = "Aira Leads"
    signature = store.clean(settings.get("signature"), 200) or name
    footer = (
        f"\n\n--\n{signature}\n{store.clean(settings.get('postal_address'), 300)}\n"
        'If you would rather not hear from me, reply "unsubscribe" and I will not contact you again.'
    )
    message.set_content(body.replace("\r\n", "\n").rstrip() + footer)
    return message


def smtp_send(
    settings: Mapping[str, Any],
    password: str,
    message: EmailMessage,
    *,
    timeout: float = 20,
) -> None:
    host = str(settings["smtp_host"])
    port = int(settings.get("smtp_port") or 587)
    check_smtp_host(host, port)
    security = str(settings.get("smtp_security") or "starttls")
    try:
        if security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if settings.get("smtp_username"):
                server.login(str(settings["smtp_username"]), password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise SendError("The mail server refused the username or password.") from None
    except smtplib.SMTPRecipientsRefused as error:
        raise SendError(
            f"The recipient was refused: {next(iter(error.recipients.values()))[1].decode('utf-8', 'replace')[:120]}"
        ) from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        raise SendError(
            f"The mail server could not send it ({type(error).__name__}: {str(error)[:120]})."
        ) from None


def regulated_region(lead: Mapping[str, Any]) -> bool:
    return bool(_REGULATED.search(str(lead.get("location") or "")))


# ---------------------------------------------------------------------------
# suppression
# ---------------------------------------------------------------------------


def _suppression_key(value: str) -> tuple[str, str, str]:
    text = store.clean(value, 200).lower()
    if "@" in text:
        if not _EMAIL.match(text):
            raise ValueError("That is not a valid email address.")
        return f"email:{text}", "email", text
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 7 and re.fullmatch(r"[+\d\s().\-]+", text):
        return f"phone:{digits[-10:]}", "phone", digits
    if re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", text):
        if text in FREEMAIL:
            raise ValueError(
                "Suppress a free-mail provider's address, not the whole provider."
            )
        return f"domain:{text}", "domain", text
    raise ValueError("Enter an email address, a domain or a phone number.")


def add_suppression(connection: Any, value: str, reason: str = "") -> dict[str, Any]:
    key, kind, normalized = _suppression_key(value)
    connection.execute(
        "INSERT OR REPLACE INTO lead_suppressions (key, kind, value, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (key, kind, normalized, store.clean(reason, 200), store.now()),
    )
    connection.commit()
    return {
        "key": key,
        "kind": kind,
        "value": normalized,
        "reason": store.clean(reason, 200),
    }


def remove_suppression(connection: Any, key: str) -> None:
    removed = connection.execute(
        "DELETE FROM lead_suppressions WHERE key = ?", (key,)
    ).rowcount
    connection.commit()
    if not removed:
        raise LookupError("That entry is not on the list.")


def list_suppressions(connection: Any) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in connection.execute(
            "SELECT * FROM lead_suppressions ORDER BY created_at DESC"
        )
    ]


def suppressed_reason(connection: Any, lead: Mapping[str, Any]) -> str:
    """Why this lead must not be contacted ('' when it may be): its email, email domain or phone is on the list."""
    keys: list[str] = []
    email = str(lead.get("email") or "").lower()
    if email:
        keys.append(f"email:{email}")
        domain = email.split("@")[-1]
        if domain not in FREEMAIL:
            keys.append(f"domain:{domain}")
    site = store.domain_of(str(lead.get("website") or ""))
    if site:
        keys.append(f"domain:{site}")
    phone = re.sub(r"\D", "", str(lead.get("phone") or ""))
    if len(phone) >= 7:
        keys.append(f"phone:{phone[-10:]}")
    for key in keys:
        row = connection.execute(
            "SELECT reason FROM lead_suppressions WHERE key = ?", (key,)
        ).fetchone()
        if row is not None:
            return f"On your do-not-contact list ({key.split(':', 1)[0]}){': ' + row[0] if row[0] else ''}"
    return ""


# ---------------------------------------------------------------------------
# the outbox
# ---------------------------------------------------------------------------


def _message(connection: Any, message_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM lead_messages WHERE id = ?", (message_id,)
    ).fetchone()
    if row is None:
        raise LookupError("Message not found.")
    return dict(row)


def list_messages(
    connection: Any, *, status: str = "", lead_id: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    clauses, args = [], []
    if status:
        if status not in STATUSES:
            raise ValueError("Unknown status.")
        clauses.append("m.status = ?")
        args.append(status)
    if lead_id:
        clauses.append("m.lead_id = ?")
        args.append(lead_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = connection.execute(
        f"""SELECT m.*, l.name AS lead_name, l.company AS lead_company FROM lead_messages m
            LEFT JOIN leads l ON l.id = m.lead_id {where} ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?""",
        [*args, max(1, min(limit, 500))],
    ).fetchall()
    return [dict(r) for r in rows]


def counts(connection: Any) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for row in connection.execute(
        "SELECT status, COUNT(*) FROM lead_messages GROUP BY status"
    ):
        out[row[0]] = row[1]
    out["sent_today"] = sent_today(connection)
    return out


def sent_today(connection: Any, now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM lead_messages WHERE status = 'sent' AND sent_at >= ?",
            (start,),
        ).fetchone()[0]
    )


def lead_facts(
    connection: Any, lead: Mapping[str, Any], offered: Sequence[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """What is really known about the lead's need for the services on offer: (service, reason) and audit findings."""
    needs = intent.needed_services(lead, offered)
    audit = (lead.get("enrichment") or {}).get("audit") or {}
    findings = [
        str(f.get("label")) for f in (audit.get("findings") or [])[:3] if f.get("label")
    ]
    return needs, findings


def create_drafts(
    connection: Any,
    lead_ids: Sequence[str],
    *,
    settings: Mapping[str, Any],
    offer: str = "",
    services: Sequence[str] = (),
    tone: str = "friendly",
    model: Callable[[str], str | None] | None = None,
    sender: str = "",
) -> dict[str, Any]:
    """One email draft per lead that has an email and may be contacted. Nothing is sent."""
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    offered = list(services) or list(settings.get("services") or [])
    pitch = store.clean(offer, 400) or str(settings.get("offer") or "")
    for lead_id in list(dict.fromkeys(lead_ids))[:200]:
        try:
            lead = store.get_lead(connection, lead_id)
        except LookupError:
            skipped.append(
                {"lead_id": lead_id, "name": "", "reason": "The lead no longer exists."}
            )
            continue
        why = ""
        if lead["kind"] == "request":
            why = "A public post has no email; reply on the post itself."
        elif not lead["email"]:
            why = "No email address. Enrich the lead from its website, or add one yourself."
        elif lead["status"] == "disqualified":
            why = "The lead is disqualified."
        else:
            why = suppressed_reason(connection, lead)
        if (
            not why
            and regulated_region(lead)
            and not settings.get("allow_regulated_regions")
        ):
            why = "Located where cold email to individuals needs prior consent (EU, UK, Canada). Confirm a lawful basis in the sender settings first."
        if not why:
            open_row = connection.execute(
                "SELECT 1 FROM lead_messages WHERE lead_id = ? AND status IN ('draft', 'approved')",
                (lead_id,),
            ).fetchone()
            if open_row:
                why = "A draft for this lead is already waiting in the outbox."
        if why:
            skipped.append({"lead_id": lead_id, "name": lead["name"], "reason": why})
            continue
        needs, findings = lead_facts(connection, lead, offered)
        draft = outreach.draft(
            lead,
            offer=pitch,
            sender=sender or str(settings.get("from_name") or ""),
            tone=tone,
            channel="email",
            model=model,
            needs=needs,
            findings=findings,
        )
        message_id = store.new_id()
        connection.execute(
            """INSERT INTO lead_messages (id, lead_id, channel, to_address, subject, body, status, source, created_at)
               VALUES (?, ?, 'email', ?, ?, ?, 'draft', ?, ?)""",
            (
                message_id,
                lead_id,
                lead["email"],
                draft["subject"],
                draft["body"],
                draft["source"],
                store.now(),
            ),
        )
        created.append(_message(connection, message_id))
    connection.commit()
    return {"created": len(created), "messages": created, "skipped": skipped}


def update_message(
    connection: Any, message_id: str, subject: str | None, body: str | None
) -> dict[str, Any]:
    row = _message(connection, message_id)
    if row["status"] not in {"draft", "approved", "failed", "skipped"}:
        raise ValueError("A sent or rejected message cannot be edited.")
    new_subject = store.clean(subject, 200) if subject is not None else row["subject"]
    new_body = (
        str(body).replace("\r\n", "\n").strip()[:4000]
        if body is not None
        else row["body"]
    )
    if not new_subject or not new_body:
        raise ValueError("A message needs a subject and a body.")
    connection.execute(
        "UPDATE lead_messages SET subject = ?, body = ?, status = 'draft', approved_at = '', error = '', source = 'edited' WHERE id = ?",
        (new_subject, new_body, message_id),
    )
    connection.commit()
    return _message(connection, message_id)


def approve(connection: Any, ids: Sequence[str]) -> dict[str, int]:
    changed = 0
    for message_id in list(dict.fromkeys(ids))[:500]:
        row = _message(connection, message_id)
        if row["status"] in {"draft", "failed", "skipped"}:
            connection.execute(
                "UPDATE lead_messages SET status = 'approved', approved_at = ?, error = '' WHERE id = ?",
                (store.now(), message_id),
            )
            changed += 1
    connection.commit()
    return {"approved": changed}


def reject(connection: Any, ids: Sequence[str]) -> dict[str, int]:
    changed = 0
    for message_id in list(dict.fromkeys(ids))[:500]:
        row = _message(connection, message_id)
        if row["status"] in {"draft", "approved", "failed", "skipped"}:
            connection.execute(
                "UPDATE lead_messages SET status = 'rejected' WHERE id = ?",
                (message_id,),
            )
            changed += 1
    connection.commit()
    return {"rejected": changed}


def delete_message(connection: Any, message_id: str) -> None:
    row = _message(connection, message_id)
    if row["status"] == "sent":
        raise ValueError("A sent message stays in the log.")
    connection.execute("DELETE FROM lead_messages WHERE id = ?", (message_id,))
    connection.commit()


def send_approved(
    connection: Any,
    settings: Mapping[str, Any],
    password: str,
    *,
    limit: int = SEND_BATCH,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    sender: Callable[..., None] = smtp_send,
) -> dict[str, Any]:
    """Send approved messages, one at a time with a pause, up to the daily cap. Returns what happened to each."""
    problems = settings_problems(settings, bool(password))
    if problems:
        raise ValueError(
            "Finish the sender settings first: " + ", ".join(problems) + "."
        )
    moment = now or datetime.now(timezone.utc)
    cap = int(settings.get("daily_cap") or DEFAULT_DAILY_CAP)
    room = cap - sent_today(connection, moment)
    result: dict[str, Any] = {
        "sent": 0,
        "failed": 0,
        "skipped": [],
        "remaining_today": max(0, room),
        "daily_cap": cap,
        "details": [],
    }
    if room <= 0:
        result["note"] = (
            f"The daily cap of {cap} is reached. Approved messages wait until tomorrow."
        )
        return result
    rows = connection.execute(
        "SELECT * FROM lead_messages WHERE status = 'approved' ORDER BY approved_at, rowid LIMIT ?",
        (max(1, min(limit, SEND_BATCH, room)),),
    ).fetchall()
    delay = max(MIN_DELAY, int(settings.get("delay_seconds") or DEFAULT_DELAY))
    first = True
    for row in rows:
        message = dict(row)
        try:
            lead = store.get_lead(connection, message["lead_id"])
        except LookupError:
            _mark(connection, message["id"], "skipped", "The lead was deleted.")
            result["skipped"].append(
                {"id": message["id"], "reason": "The lead was deleted."}
            )
            continue
        why = ""
        if lead["status"] == "disqualified":
            why = "The lead is disqualified."
        elif not message["to_address"] or not _EMAIL.match(message["to_address"]):
            why = "The address is not a valid email."
        else:
            why = suppressed_reason(
                connection, {**lead, "email": message["to_address"]}
            )
        if (
            not why
            and regulated_region(lead)
            and not settings.get("allow_regulated_regions")
        ):
            why = "Located where cold email needs prior consent; not sent."
        if why:
            _mark(connection, message["id"], "skipped", why)
            result["skipped"].append({"id": message["id"], "reason": why})
            continue
        if not first:
            sleep(delay)
        first = False
        try:
            built = build_message(
                settings,
                to=message["to_address"],
                subject=message["subject"],
                body=message["body"],
            )
            sender(settings, password, built)
        except SendError as error:
            _mark(connection, message["id"], "failed", str(error))
            result["failed"] += 1
            result["details"].append(
                {"id": message["id"], "status": "failed", "error": str(error)}
            )
            if "refused the username or password" in str(
                error
            ) or "cannot be used here" in str(error):
                break  # the same error would repeat for every message
            continue
        connection.execute(
            "UPDATE lead_messages SET status = 'sent', sent_at = ?, message_id = ?, error = '' WHERE id = ?",
            (store.now(), str(built["Message-ID"]), message["id"]),
        )
        if lead["status"] == "new":
            connection.execute(
                "UPDATE leads SET status = 'contacted', updated_at = ? WHERE id = ?",
                (store.now(), lead["id"]),
            )
        connection.commit()
        result["sent"] += 1
        result["details"].append(
            {"id": message["id"], "status": "sent", "to": message["to_address"]}
        )
    result["remaining_today"] = max(0, cap - sent_today(connection, moment))
    return result


def _mark(connection: Any, message_id: str, status: str, error: str) -> None:
    connection.execute(
        "UPDATE lead_messages SET status = ?, error = ? WHERE id = ?",
        (status, store.clean(error, 300), message_id),
    )
    connection.commit()


def send_test(
    settings: Mapping[str, Any],
    password: str,
    *,
    sender: Callable[..., None] = smtp_send,
) -> dict[str, str]:
    """Send one test message to the sender's own address to prove the mail server settings work."""
    problems = settings_problems(settings, bool(password))
    if problems:
        raise ValueError(
            "Finish the sender settings first: " + ", ".join(problems) + "."
        )
    built = build_message(
        settings,
        to=str(settings["from_email"]),
        subject="Aira Leads: test message",
        body="This is a test from Aira Leads. If you can read it, your mail server settings work.",
    )
    sender(settings, password, built)
    return {"sent_to": str(settings["from_email"])}
