"""Writing to the people on a recruiter list: careful email, and LinkedIn drafts the person sends themselves.

The list id (:mod:`aira.recruiter_contacts`) says who; this module says what each person gets and whether they may
get anything at all. Nothing here has its own mail sender or touches LinkedIn:

* **Email** is personalised from a template and the contact's details and story, previewed, then put in the shared
  email outbox (``suite_outbox``, the one Marketing and the Business hub use) as approved and sent with
  :func:`aira.suite.kit.send_outbox` through the person's own mail server (Business hub > Email). Every message
  ends with an opt-out line. A daily limit (default 20) counts every recruiter email of the day.
* A contact is skipped, with the reason shown, when they have no email address (LinkedIn only: nothing is looked
  up or guessed), are on the do-not-contact list (their own row here, or their address on the Leads suppression
  list), asked not to be contacted in a reply, replied after an email from this page (from then on the person
  writes to them themselves), or were emailed in the last :data:`FOLLOW_UP_DAYS` days.
* **LinkedIn** gets drafts only: one personalised draft per contact, saved in the conversation's reply box
  (``message_drafts``), where the person reads it and presses Send in the Messages tab, one conversation at a time,
  through the existing reply flow and its guardrails. Nothing is ever sent to LinkedIn from here.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from aira import recruiter_contacts as contacts
from aira.inbox_store import collapse

APP = "careers"
KIND = "recruiter_email"
REF_TYPE = "recruiter"
SETTINGS_APP = "careers"
SETTINGS_KEY = "recruiter_outreach"
DEFAULT_DAILY_LIMIT = 20
MAX_DAILY_LIMIT = 100
FOLLOW_UP_DAYS = 14
OPT_OUT_LINE = 'If you would rather not hear from me, reply "unsubscribe" and I will not email you again.'
PLACEHOLDERS = (
    "first_name",
    "name",
    "company",
    "title",
    "job",
    "last_contact",
    "my_name",
    "my_first_name",
)
DEFAULTS: dict[str, Any] = {
    "daily_limit": DEFAULT_DAILY_LIMIT,
    "email_subject": "Checking in about roles at {company|your team}",
    "email_body": (
        "Hi {first_name},\n\n"
        "I hope you are well. I am still very interested in opportunities at {company|your company} and wanted to "
        "check in. Is there anything new you could share, or a good time to talk this week?\n\n"
        "Thank you,\n{my_name}"
    ),
    "linkedin_body": (
        "Hi {first_name}, I hope you are well. I am still interested in roles at {company|your company}. "
        "Is there anything new you could share? Thank you, {my_first_name}"
    ),
}
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)(?:\|([^{}]*))?\}")
# Words that ask us to stop, in their own email or LinkedIn message.
_OPT_OUT_RE = re.compile(
    r"(?i)\b(?:unsubscribe|opt(?:\s|-)?out|stop (?:emailing|contacting|messaging|writing to) me|remove me from"
    r"|(?:do not|don't|dont) (?:contact|email|message) me|take me off)\b"
)
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


class MissingDetail(ValueError):
    """A template asks for a detail this contact does not have (and gives no fallback)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# --------------------------------------------------------------------------- #
# Settings and templates
# --------------------------------------------------------------------------- #


def _suite():
    from aira.suite import kit

    return kit


def get_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = _suite().get_setting(connection, SETTINGS_APP, SETTINGS_KEY, {}) or {}
    return {
        **DEFAULTS,
        **{key: value for key, value in stored.items() if key in DEFAULTS},
    }


def validate_template(text: Any, what: str, *, limit: int) -> str:
    value = str(text or "").replace("\r\n", "\n").strip()
    if not value:
        raise ValueError(f"Write the {what}.")
    if len(value) > limit:
        raise ValueError(f"The {what} can be at most {limit:,} characters.")
    for match in _PLACEHOLDER_RE.finditer(value):
        if match[1] not in PLACEHOLDERS:
            raise ValueError(
                f"{{{match[1]}}} is not a detail Monosage knows. Use one of: "
                + ", ".join(f"{{{key}}}" for key in PLACEHOLDERS)
                + "."
            )
    if re.search(r"\[[^\]]{1,40}\]", value):
        raise ValueError(
            "Replace the [bracketed] placeholders with real text or with {details}."
        )
    return value


def save_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = get_settings(connection)
    merged = {**current, **{key: raw[key] for key in DEFAULTS if key in raw}}
    try:
        limit = int(merged["daily_limit"])
    except (TypeError, ValueError) as error:
        raise ValueError("The daily limit must be a whole number.") from error
    if not 1 <= limit <= MAX_DAILY_LIMIT:
        raise ValueError(f"The daily limit must be between 1 and {MAX_DAILY_LIMIT}.")
    clean = {
        "daily_limit": limit,
        "email_subject": validate_template(
            merged["email_subject"], "email subject", limit=200
        ),
        "email_body": validate_template(merged["email_body"], "email text", limit=5000),
        "linkedin_body": validate_template(
            merged["linkedin_body"], "LinkedIn message", limit=2000
        ),
    }
    _suite().set_setting(connection, SETTINGS_APP, SETTINGS_KEY, clean)
    return clean


def render(template: str, values: Mapping[str, str]) -> str:
    """Fill ``{detail}`` and ``{detail|fallback}``. A detail that is empty without a fallback raises MissingDetail."""

    def fill(match: re.Match[str]) -> str:
        value = collapse(str(values.get(match[1]) or ""))
        if value:
            return value
        if match[2] is not None:
            return match[2]
        raise MissingDetail(match[1])

    return _PLACEHOLDER_RE.sub(fill, template)


def _day(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp[:10])
    except ValueError:
        return ""
    return f"{_MONTHS[moment.month - 1]} {moment.day}, {moment.year}"


def details(
    connection: sqlite3.Connection, person: Mapping[str, Any], my_name: str
) -> dict[str, str]:
    """The values a template can use for one contact: their record and their story (the newest linked job)."""
    job = ""
    try:
        items = contacts.story(connection, str(person["recruiter_id"]))["items"]
    except LookupError:
        items = []
    for item in items:
        if item["kind"] == "job" and item.get("title"):
            job = str(item["title"]).split(" at ")[0]
            break
    name = collapse(str(person.get("name") or ""))
    mine = collapse(my_name)
    return {
        "first_name": name.split(" ")[0] if name else "",
        "name": name,
        "company": collapse(str(person.get("company") or "")),
        "title": collapse(str(person.get("title") or "")),
        "job": job,
        "last_contact": _day(str(person.get("last_contact_at") or "")),
        "my_name": mine,
        "my_first_name": mine.split(" ")[0] if mine else "",
    }


# --------------------------------------------------------------------------- #
# Who may be written to
# --------------------------------------------------------------------------- #


def do_not_contact_reason(
    connection: sqlite3.Connection, person: Mapping[str, Any]
) -> str:
    row = connection.execute(
        "SELECT reason FROM recruiter_do_not_contact WHERE recruiter_id = ?",
        (person["recruiter_id"],),
    ).fetchone()
    if row is not None:
        return "On your do-not-contact list" + (
            f": {row['reason']}" if row["reason"] else "."
        )
    if person.get("email"):
        from aira.leads import mail

        reason = mail.suppressed_reason(connection, {"email": person["email"]})
        if reason:
            return reason
    return ""


def add_do_not_contact(
    connection: sqlite3.Connection, recruiter_id: str, reason: str = ""
) -> dict[str, Any]:
    from aira import crm
    from aira.leads import mail

    recruiter = crm.get_recruiter(connection, recruiter_id)
    if recruiter is None:
        raise LookupError("Recruiter not found.")
    reason = collapse(reason)[:200]
    connection.execute(
        """INSERT INTO recruiter_do_not_contact (recruiter_id, reason, created_at) VALUES (?, ?, ?)
        ON CONFLICT(recruiter_id) DO UPDATE SET reason = excluded.reason""",
        (recruiter_id, reason, contacts._now()),
    )
    if recruiter["email"]:
        mail.add_suppression(
            connection, recruiter["email"], reason or "Recruiter contact"
        )
    return {"recruiter_id": recruiter_id, "reason": reason}


def remove_do_not_contact(connection: sqlite3.Connection, recruiter_id: str) -> None:
    from aira import crm

    recruiter = crm.get_recruiter(connection, recruiter_id)
    if recruiter is None:
        raise LookupError("Recruiter not found.")
    removed = connection.execute(
        "DELETE FROM recruiter_do_not_contact WHERE recruiter_id = ?", (recruiter_id,)
    ).rowcount
    if recruiter["email"]:
        removed += connection.execute(
            "DELETE FROM lead_suppressions WHERE key = ?",
            (f"email:{recruiter['email']}",),
        ).rowcount
    if not removed:
        raise LookupError("This contact is not on the do-not-contact list.")


def list_do_not_contact(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT recruiter_do_not_contact.recruiter_id, recruiter_do_not_contact.reason,
                recruiter_do_not_contact.created_at, recruiters.name, recruiters.company, recruiters.email
            FROM recruiter_do_not_contact JOIN recruiters ON recruiters.recruiter_id = recruiter_do_not_contact.recruiter_id
            WHERE recruiters.deleted_at IS NULL ORDER BY recruiter_do_not_contact.created_at DESC"""
        )
    ]


def _their_texts(
    connection: sqlite3.Connection, person: Mapping[str, Any]
) -> list[tuple[str, str, str]]:
    """(stamp, where, text) of everything they wrote: imported emails from their address, LinkedIn messages."""
    out: list[tuple[str, str, str]] = []
    email = str(person.get("email") or "").lower()
    if email:
        for row in connection.execute(
            "SELECT subject, body_excerpt, received_at, created_at FROM email_messages WHERE lower(sender_email) = ?",
            (email,),
        ):
            out.append(
                (
                    contacts._stamp(row["received_at"] or row["created_at"]),
                    "email",
                    f"{row['subject']}\n{row['body_excerpt']}",
                )
            )
    for thread_id in contacts._thread_ids(connection, str(person["recruiter_id"])):
        for row in connection.execute(
            "SELECT sent_on, time_label, text FROM inbox_messages WHERE thread_id = ? AND own = 0",
            (thread_id,),
        ):
            out.append(
                (
                    contacts._message_stamp(row["sent_on"], row["time_label"]),
                    "linkedin",
                    row["text"],
                )
            )
    return out


def record_opt_outs(
    connection: sqlite3.Connection, people: Sequence[Mapping[str, Any]]
) -> int:
    """Put everyone who asked us to stop (in an email or a LinkedIn message) on the do-not-contact list."""
    added = 0
    for person in people:
        if connection.execute(
            "SELECT 1 FROM recruiter_do_not_contact WHERE recruiter_id = ?",
            (person["recruiter_id"],),
        ).fetchone():
            continue
        for _stamp, where, text in _their_texts(connection, person):
            if _OPT_OUT_RE.search(text or ""):
                add_do_not_contact(
                    connection,
                    str(person["recruiter_id"]),
                    "Asked not to be contacted (their email)"
                    if where == "email"
                    else "Asked not to be contacted (their LinkedIn message)",
                )
                added += 1
                break
    return added


def _outbox_ready(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'suite_outbox'"
        ).fetchone()
        is not None
    )


def emails_to(
    connection: sqlite3.Connection, recruiter_id: str
) -> list[dict[str, Any]]:
    """Every recruiter email this page put in the outbox for one contact, newest first (their send log)."""
    if not _outbox_ready(connection):
        return []
    return [
        dict(row)
        for row in connection.execute(
            """SELECT id, recipient, subject, body, status, error, created_at, approved_at, sent_at FROM suite_outbox
            WHERE app = ? AND kind = ? AND ref_type = ? AND ref_id = ? ORDER BY created_at DESC""",
            (APP, KIND, REF_TYPE, recruiter_id),
        )
    ]


def sent_today(connection: sqlite3.Connection, now: datetime | None = None) -> int:
    """Recruiter emails sent today, or approved and on their way (they count against the daily limit)."""
    if not _outbox_ready(connection):
        return 0
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )
    return int(
        connection.execute(
            """SELECT COUNT(*) FROM suite_outbox WHERE app = ? AND kind = ?
            AND ((status = 'sent' AND sent_at >= ?) OR (status = 'approved' AND approved_at >= ?))""",
            (APP, KIND, start, start),
        ).fetchone()[0]
    )


def _latest_from_them(connection: sqlite3.Connection, person: Mapping[str, Any]) -> str:
    return contacts._later(
        str(person.get("last_inbound_at") or ""),
        *(stamp for stamp, _where, _text in _their_texts(connection, person)),
    )


def _email_block(
    connection: sqlite3.Connection, person: Mapping[str, Any], now: datetime
) -> str:
    """Why this contact gets no email now ('' when they may)."""
    if not person.get("email"):
        return "LinkedIn only: there is no email address for this contact. Use LinkedIn drafts instead."
    reason = do_not_contact_reason(connection, person)
    if reason:
        return reason
    log = [
        item
        for item in emails_to(connection, str(person["recruiter_id"]))
        if item["status"] in ("sent", "approved", "pending")
    ]
    if log:
        first = min(
            contacts._stamp(
                item["sent_at"] or item["approved_at"] or item["created_at"]
            )
            for item in log
        )
        replied = _latest_from_them(connection, person)
        if replied and first and replied >= first:
            return (
                f"They replied on {_day(replied)}. Write to them yourself from now on."
            )
        latest = max(
            item["sent_at"] or item["approved_at"] or item["created_at"] for item in log
        )
        try:
            moment = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
        except ValueError:
            moment = None
        if moment is not None:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            follow_up = moment + timedelta(days=FOLLOW_UP_DAYS)
            if follow_up > now:
                return (
                    f"You emailed them on {_day(moment.astimezone().strftime('%Y-%m-%d'))}. "
                    f"Wait for a reply; you can follow up from {_day(follow_up.astimezone().strftime('%Y-%m-%d'))}."
                )
    return ""


def _missing(key: str) -> str:
    return (
        f"No {key.replace('_', ' ')} for this contact. Add it to the contact, or give the detail a fallback "
        f"like {{{key}|your team}}."
    )


def email_plan(
    connection: sqlite3.Connection,
    list_id: str,
    *,
    subject: str,
    body: str,
    my_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What each person on the list would get (or why not). Nothing is written except opt-outs found in replies."""
    now = now or datetime.now(timezone.utc)
    subject = validate_template(subject, "email subject", limit=200)
    body = validate_template(body, "email text", limit=5000)
    if not collapse(my_name) and "{my_" in subject + body:
        raise ValueError(
            "Add your name in Profile & resume first; the email signs off with it."
        )
    people = contacts.members(connection, list_id, now=now)
    record_opt_outs(connection, people)
    settings = get_settings(connection)
    remaining = max(0, int(settings["daily_limit"]) - sent_today(connection, now))
    items: list[dict[str, Any]] = []
    ready = 0
    for person in people:
        item: dict[str, Any] = {
            "recruiter_id": person["recruiter_id"],
            "name": person["name"],
            "company": person["company"],
            "email": person["email"],
            "linkedin_only": not person["email"],
        }
        reason = _email_block(connection, person, now)
        if not reason:
            values = details(connection, person, my_name)
            try:
                item["subject"] = render(subject, values)[:200]
                item["body"] = f"{render(body, values).rstrip()}\n\n{OPT_OUT_LINE}"
            except MissingDetail as error:
                reason = _missing(error.key)
        if reason:
            item.update(status="skipped", reason=reason)
        elif ready >= remaining:
            item.update(
                status="later",
                reason=f"Over today's limit of {settings['daily_limit']} emails. Send the rest tomorrow.",
            )
        else:
            item["status"] = "ready"
            ready += 1
        items.append(item)
    return {
        "list": contacts.get_list(connection, list_id),
        "items": items,
        "ready": ready,
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
        "later": sum(1 for item in items if item["status"] == "later"),
        "linkedin_only": sum(1 for item in items if item["linkedin_only"]),
        "daily_limit": int(settings["daily_limit"]),
        "sent_today": sent_today(connection, now),
        "remaining_today": remaining,
    }


def send_emails(
    connection: sqlite3.Connection,
    list_id: str,
    recruiter_ids: Sequence[str],
    *,
    subject: str,
    body: str,
    my_name: str,
    password: str,
    sender: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send the previewed emails the person confirmed. The plan is worked out again first, so a contact who
    replied, opted out or reached the daily limit since the preview is not written to."""
    kit = _suite()
    chosen = {str(item) for item in recruiter_ids if item}
    if not chosen:
        raise ValueError("Choose at least one contact to email.")
    settings = kit.email_settings(connection)
    if not (settings.get("smtp_host") and settings.get("from_email")):
        raise ValueError(
            "Set up your email first in Business hub > Email (your own mail server or Gmail)."
        )
    plan = email_plan(
        connection, list_id, subject=subject, body=body, my_name=my_name, now=now
    )
    queued: list[str] = []
    for item in plan["items"]:
        if item["status"] != "ready" or item["recruiter_id"] not in chosen:
            continue
        message = kit.queue_email(
            connection,
            APP,
            KIND,
            to=item["email"],
            subject=item["subject"],
            body=item["body"],
            ref_type=REF_TYPE,
            ref_id=item["recruiter_id"],
        )
        if message is None:
            continue
        kit.set_outbox_status(
            connection, [message["id"]], "approved"
        )  # the person approved it in the preview
        queued.append(message["id"])
    connection.commit()
    if not queued:
        return {
            "queued": 0,
            "sent": 0,
            "failed": 0,
            "details": [],
            "skipped": len(chosen),
        }
    result = kit.send_outbox(
        connection, password, limit=len(queued), sender=sender, ids=queued
    )
    return {
        "queued": len(queued),
        "sent": result["sent"],
        "failed": result["failed"],
        "details": result["details"],
        "skipped": len(chosen) - len(queued),
    }


# --------------------------------------------------------------------------- #
# LinkedIn drafts (never sent from here)
# --------------------------------------------------------------------------- #


def _latest_thread(
    connection: sqlite3.Connection, recruiter_id: str
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_order: tuple[int, str] = (0, "")
    for thread_id in contacts._thread_ids(connection, recruiter_id):
        row = connection.execute(
            "SELECT thread_id, participant, last_message_at, listed_at FROM inbox_threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        participant = row["participant"] if row else ""
        if not participant:
            policy = connection.execute(
                "SELECT participant FROM message_threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            participant = policy["participant"] if policy else ""
        order = (
            int(row["listed_at"] or 0) if row else 0,
            str(row["last_message_at"] or "") if row else "",
        )
        if best is None or order > best_order:
            best, best_order = (
                {"thread_id": thread_id, "participant": participant},
                order,
            )
    return best


def linkedin_plan(
    connection: sqlite3.Connection,
    list_id: str,
    *,
    body: str,
    my_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    body = validate_template(body, "LinkedIn message", limit=2000)
    if not collapse(my_name) and "{my_" in body:
        raise ValueError(
            "Add your name in Profile & resume first; the message signs off with it."
        )
    people = contacts.members(connection, list_id, now=now)
    record_opt_outs(connection, people)
    items = []
    for person in people:
        item: dict[str, Any] = {
            "recruiter_id": person["recruiter_id"],
            "name": person["name"],
            "company": person["company"],
        }
        thread = _latest_thread(connection, str(person["recruiter_id"]))
        reason = do_not_contact_reason(connection, person)
        if not reason and thread is None:
            reason = "No LinkedIn conversation with this contact is saved in Monosage, so there is nowhere to put a draft."
        if not reason and thread is not None:
            waiting = connection.execute(
                "SELECT 1 FROM message_drafts WHERE thread_id = ? AND trim(reply) != ''",
                (thread["thread_id"],),
            ).fetchone()
            if waiting:
                reason = "A draft is already waiting in this conversation. Send or change it in Messages."
        if not reason:
            try:
                item["text"] = render(body, details(connection, person, my_name))[:3000]
            except MissingDetail as error:
                reason = _missing(error.key)
        if thread is not None:
            item["thread_id"] = thread["thread_id"]
            item["link"] = contacts.thread_link(
                thread["thread_id"], thread["participant"] or person["name"]
            )
        item.update(status="skipped", reason=reason) if reason else item.update(
            status="ready"
        )
        items.append(item)
    return {
        "list": contacts.get_list(connection, list_id),
        "items": items,
        "ready": sum(1 for item in items if item["status"] == "ready"),
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
    }


def make_linkedin_drafts(
    connection: sqlite3.Connection,
    list_id: str,
    recruiter_ids: Sequence[str],
    *,
    body: str,
    my_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Save one draft per chosen contact in their conversation's reply box. Never sends anything."""
    chosen = {str(item) for item in recruiter_ids if item}
    if not chosen:
        raise ValueError("Choose at least one contact to draft a message for.")
    plan = linkedin_plan(connection, list_id, body=body, my_name=my_name, now=now)
    drafted = []
    for item in plan["items"]:
        if item["status"] != "ready" or item["recruiter_id"] not in chosen:
            continue
        connection.execute(
            """INSERT INTO message_drafts (thread_id, participant, reply) VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET participant = excluded.participant, reply = excluded.reply,
                updated_at = CURRENT_TIMESTAMP""",
            (item["thread_id"], item["name"][:200], item["text"]),
        )
        drafted.append(
            {key: item[key] for key in ("recruiter_id", "name", "thread_id", "link")}
        )
    return {
        "drafted": len(drafted),
        "drafts": drafted,
        "skipped": len(chosen) - len(drafted),
    }


def story_items(
    connection: sqlite3.Connection, recruiter_id: str
) -> list[dict[str, Any]]:
    """The contact's send log for their story: emails from the outbox and LinkedIn drafts waiting to be sent."""
    items = []
    for message in emails_to(connection, recruiter_id):
        items.append(
            {
                "kind": "outreach",
                "at": contacts._stamp(
                    message["sent_at"]
                    or message["approved_at"]
                    or message["created_at"]
                ),
                "title": f"Email to them: {message['subject']}",
                "text": collapse(message["body"])[:400],
                "status": message["status"],
                "error": message["error"],
                "direction": "outbound" if message["status"] == "sent" else "",
                "link": "",
            }
        )
    for thread_id in contacts._thread_ids(connection, recruiter_id):
        row = connection.execute(
            "SELECT reply, updated_at FROM message_drafts WHERE thread_id = ? AND trim(reply) != ''",
            (thread_id,),
        ).fetchone()
        if row:
            items.append(
                {
                    "kind": "linkedin_draft",
                    "at": contacts._stamp(
                        str(row["updated_at"] or "").replace(" ", "T") + "+00:00"
                    )
                    if row["updated_at"]
                    else "",
                    "title": "LinkedIn draft waiting for you to send",
                    "text": row["reply"][:400],
                    "status": "draft",
                    "direction": "",
                    "link": contacts.thread_link(thread_id, ""),
                }
            )
    return items
