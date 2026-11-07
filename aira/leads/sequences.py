"""Follow-up sequences for leads: a few emails with waits between them, that stop when the lead answers.

A sequence is up to :data:`MAX_STEPS` steps (``delay_days``, ``subject``, ``body``). Enrolling a lead starts it; the
background pass (Marketing's task, :func:`tick`) writes each step into the Leads outbox when it is due, counting
the wait from when the previous step was **sent**. Nothing is sent from here: each step goes through the outbox
like any other lead email (the daily cap, the pause between messages, the do-not-contact list and the consent
guard all apply). With ``review`` on (the default) a step waits as a draft for a person to approve; off, the
person approved the sequence's texts once and each step is approved as it is written.

A sequence stops for a lead when they reply (read from the incoming mailbox, :mod:`aira.inbound_mail`), when they
unsubscribe or go on the do-not-contact list (a bounce puts them there), when the lead is disqualified or becomes
a customer, or when a person stops it. Unsent steps are withdrawn when it stops.

Placeholders in a step: ``{first_name}``, ``{name}``, ``{company}``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from aira.leads import mail, store
from aira.suite import kit

MAX_STEPS = 10
MAX_DELAY_DAYS = 90
STATUSES = ("active", "paused")
ENROLLMENT_STATUSES = ("active", "finished", "stopped")
# A lead in one of these states is past cold follow-ups.
DONE_LEAD_STATUSES = ("replied", "qualified", "disqualified", "customer")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS lead_sequences (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        steps TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active',
        review INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS lead_enrollments (
        id TEXT PRIMARY KEY,
        sequence_id TEXT NOT NULL,
        lead_id TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        step INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        stop_reason TEXT NOT NULL DEFAULT '',
        next_at TEXT NOT NULL DEFAULT '',
        last_message_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (sequence_id, lead_id)
    )""",
    "CREATE INDEX IF NOT EXISTS lead_enrollments_due ON lead_enrollments (status, next_at)",
    "CREATE INDEX IF NOT EXISTS lead_enrollments_email ON lead_enrollments (email)",
    "CREATE INDEX IF NOT EXISTS lead_enrollments_lead ON lead_enrollments (lead_id)",
)


def _has_leads(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lead_messages'"
        ).fetchone()
        is not None
    )


def ensure(connection: sqlite3.Connection) -> None:
    store.ensure_schema(connection)
    kit.ensure(connection, "leads.sequences.v1", SCHEMA)


def _now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# sequences
# ---------------------------------------------------------------------------


def clean_steps(raw: Any) -> list[dict[str, Any]]:
    steps = raw if isinstance(raw, list) else []
    if not steps:
        raise ValueError("Add at least one step: what to send and after how many days.")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"A sequence has at most {MAX_STEPS} steps.")
    out = []
    for index, step in enumerate(steps, start=1):
        item: Mapping[str, Any] = step if isinstance(step, Mapping) else {}
        try:
            delay = int(item.get("delay_days") or 0)
        except (TypeError, ValueError):
            raise ValueError(
                f"Step {index}: the wait must be a number of days."
            ) from None
        if not 0 <= delay <= MAX_DELAY_DAYS:
            raise ValueError(f"Step {index}: wait from 0 to {MAX_DELAY_DAYS} days.")
        if index > 1 and delay < 1:
            raise ValueError(
                f"Step {index}: wait at least a day after the step before."
            )
        subject = store.clean(item.get("subject"), 200)
        body = str(item.get("body") or "").replace("\r\n", "\n").strip()[:4000]
        if not body:
            raise ValueError(f"Step {index}: write the email.")
        if index == 1 and not subject:
            raise ValueError("Step 1: write a subject.")
        out.append({"delay_days": delay, "subject": subject, "body": body})
    return out


def _row(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        raise LookupError("That sequence was not found.")
    out = dict(row)
    out["steps"] = json.loads(out.get("steps") or "[]")
    out["review"] = bool(out.get("review"))
    return out


def get_sequence(connection: sqlite3.Connection, sequence_id: str) -> dict[str, Any]:
    ensure(connection)
    return _row(
        kit.fetch_one(
            connection, "SELECT * FROM lead_sequences WHERE id = ?", (sequence_id,)
        )
    )


def _counts(connection: sqlite3.Connection, sequence_id: str) -> dict[str, int]:
    counts = {status: 0 for status in ENROLLMENT_STATUSES}
    for row in connection.execute(
        "SELECT status, COUNT(*) FROM lead_enrollments WHERE sequence_id = ? GROUP BY status",
        (sequence_id,),
    ):
        counts[row[0]] = int(row[1])
    counts["replied"] = kit.count(
        connection,
        "SELECT COUNT(*) FROM lead_enrollments WHERE sequence_id = ? AND stop_reason = 'replied'",
        (sequence_id,),
    )
    return counts


def list_sequences(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure(connection)
    return [
        {**_row(row), "counts": _counts(connection, row["id"])}
        for row in kit.fetch_all(
            connection, "SELECT * FROM lead_sequences ORDER BY created_at DESC"
        )
    ]


def save_sequence(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    sequence_id: str = "",
) -> dict[str, Any]:
    ensure(connection)
    current = get_sequence(connection, sequence_id) if sequence_id else None
    merged = {**(current or {}), **raw}
    name = store.clean(merged.get("name"), 120)
    if not name:
        raise ValueError("Name the sequence.")
    status = str(merged.get("status") or "active").lower()
    if status not in STATUSES:
        raise ValueError("A sequence is active or paused.")
    values = {
        "name": name,
        "steps": json.dumps(clean_steps(merged.get("steps")), ensure_ascii=False),
        "status": status,
        "review": 1 if kit.flag(merged.get("review", True)) else 0,
        "updated_at": store.now(),
    }
    if current:
        kit.update_row(connection, "lead_sequences", current["id"], values)
        row_id = current["id"]
    else:
        row_id = store.new_id()
        connection.execute(
            "INSERT INTO lead_sequences (id, name, steps, status, review, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                values["name"],
                values["steps"],
                values["status"],
                values["review"],
                values["updated_at"],
                values["updated_at"],
            ),
        )
    connection.commit()
    return get_sequence(connection, row_id)


def delete_sequence(connection: sqlite3.Connection, sequence_id: str) -> None:
    sequence = get_sequence(connection, sequence_id)
    for row in kit.fetch_all(
        connection,
        "SELECT * FROM lead_enrollments WHERE sequence_id = ? AND status = 'active'",
        (sequence["id"],),
    ):
        _stop(connection, row, "the sequence was deleted")
    connection.execute(
        "DELETE FROM lead_enrollments WHERE sequence_id = ?", (sequence["id"],)
    )
    connection.execute("DELETE FROM lead_sequences WHERE id = ?", (sequence["id"],))
    connection.commit()


def sequence_detail(connection: sqlite3.Connection, sequence_id: str) -> dict[str, Any]:
    sequence = get_sequence(connection, sequence_id)
    rows = kit.fetch_all(
        connection,
        """SELECT e.*, l.name AS lead_name, l.company AS lead_company, l.status AS lead_status
        FROM lead_enrollments e LEFT JOIN leads l ON l.id = e.lead_id
        WHERE e.sequence_id = ? ORDER BY e.created_at DESC LIMIT 500""",
        (sequence["id"],),
    )
    return {
        "sequence": {**sequence, "counts": _counts(connection, sequence["id"])},
        "enrollments": rows,
    }


STOP_LABELS = {
    "replied": "They replied",
    "unsubscribed": "They unsubscribed",
    "bounced": "The email bounced",
    "stopped by you": "You stopped it",
    "the sequence was deleted": "The sequence was deleted",
    "the lead was deleted": "The lead was deleted",
    "no email address": "The lead has no email address",
    "on the do-not-contact list": "They are on the do-not-contact list",
    "a step was not sent": "A step was rejected or skipped in the outbox",
}


def lead_enrollments(
    connection: sqlite3.Connection, lead_id: str
) -> list[dict[str, Any]]:
    """Every sequence this lead is (or was) in: where it is, when the next email is due, and when and why it
    stopped. Shown in the lead's drawer."""
    if not _has_leads(connection):
        return []
    ensure(connection)
    out = []
    for row in kit.fetch_all(
        connection,
        """SELECT e.*, s.name AS sequence_name, s.steps AS sequence_steps, s.status AS sequence_status
        FROM lead_enrollments e LEFT JOIN lead_sequences s ON s.id = e.sequence_id
        WHERE e.lead_id = ? ORDER BY e.created_at DESC""",
        (lead_id,),
    ):
        steps = json.loads(row.pop("sequence_steps") or "[]")
        reason = str(row.get("stop_reason") or "")
        out.append(
            {
                **row,
                "steps_total": len(steps),
                "stop_label": STOP_LABELS.get(
                    reason, reason[:1].upper() + reason[1:] if reason else ""
                ),
                "stopped_at": row["updated_at"] if row["status"] == "stopped" else "",
                "finished_at": row["updated_at"] if row["status"] == "finished" else "",
            }
        )
    return out


# ---------------------------------------------------------------------------
# enrolling and stopping
# ---------------------------------------------------------------------------


def enroll(
    connection: sqlite3.Connection,
    sequence_id: str,
    lead_ids: Sequence[str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Start the sequence for these leads. A lead without an email, on the do-not-contact list, past cold
    follow-ups or already in this sequence is skipped with the reason."""
    sequence = get_sequence(connection, sequence_id)
    moment = _now(now)
    added, skipped = 0, []
    for lead_id in list(dict.fromkeys(str(item) for item in lead_ids))[:500]:
        try:
            lead = store.get_lead(connection, lead_id)
        except LookupError:
            skipped.append({"lead_id": lead_id, "reason": "The lead no longer exists."})
            continue
        why = ""
        if not lead.get("email"):
            why = "No email address."
        elif lead["status"] in DONE_LEAD_STATUSES:
            why = f"The lead is {lead['status']}."
        else:
            why = mail.suppressed_reason(connection, lead)
        if not why and kit.fetch_one(
            connection,
            "SELECT 1 FROM lead_enrollments WHERE sequence_id = ? AND lead_id = ?",
            (sequence["id"], lead_id),
        ):
            why = "Already in this sequence."
        if why:
            skipped.append(
                {"lead_id": lead_id, "name": lead.get("name", ""), "reason": why}
            )
            continue
        first_due = moment + timedelta(days=int(sequence["steps"][0]["delay_days"]))
        connection.execute(
            """INSERT INTO lead_enrollments (id, sequence_id, lead_id, email, step, status, next_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, 'active', ?, ?, ?)""",
            (
                store.new_id(),
                sequence["id"],
                lead_id,
                str(lead["email"]).lower(),
                _stamp(first_due),
                _stamp(moment),
                _stamp(moment),
            ),
        )
        added += 1
    connection.commit()
    return {"enrolled": added, "skipped": skipped}


def _withdraw(
    connection: sqlite3.Connection, enrollment: Mapping[str, Any], reason: str
) -> int:
    """Unsent steps of this enrollment leave the outbox."""
    return int(
        connection.execute(
            "UPDATE lead_messages SET status = 'rejected', error = ? WHERE lead_id = ? AND campaign LIKE ? AND status IN ('draft', 'approved', 'failed')",
            (
                store.clean(f"Sequence stopped: {reason}", 300),
                enrollment["lead_id"],
                f"sequence:{enrollment['sequence_id']}:%",
            ),
        ).rowcount
        or 0
    )


def _stop(
    connection: sqlite3.Connection, enrollment: Mapping[str, Any], reason: str
) -> None:
    connection.execute(
        "UPDATE lead_enrollments SET status = 'stopped', stop_reason = ?, next_at = '', updated_at = ? WHERE id = ?",
        (reason[:120], store.now(), enrollment["id"]),
    )
    _withdraw(connection, enrollment, reason)


def stop(
    connection: sqlite3.Connection,
    sequence_id: str,
    lead_ids: Iterable[str],
    reason: str = "stopped by you",
) -> int:
    ensure(connection)
    stopped = 0
    for lead_id in lead_ids:
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM lead_enrollments WHERE sequence_id = ? AND lead_id = ? AND status = 'active'",
            (sequence_id, str(lead_id)),
        ):
            _stop(connection, row, reason)
            stopped += 1
    connection.commit()
    return stopped


def stop_for_lead(connection: sqlite3.Connection, lead_id: str, reason: str) -> int:
    if not _has_leads(connection):
        return 0
    ensure(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM lead_enrollments WHERE lead_id = ? AND status = 'active'",
        (lead_id,),
    )
    for row in rows:
        _stop(connection, row, reason)
    connection.commit()
    return len(rows)


def stop_for_address(connection: sqlite3.Connection, address: str, reason: str) -> int:
    """Stop every running sequence to ``address`` (a bounce, an unsubscribe)."""
    if not _has_leads(connection):
        return 0
    ensure(connection)
    text = str(address or "").strip().lower()
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM lead_enrollments WHERE email = ? AND status = 'active'",
        (text,),
    )
    for row in rows:
        _stop(connection, row, reason)
    connection.commit()
    return len(rows)


def record_reply(
    connection: sqlite3.Connection,
    from_email: str,
    threads: Sequence[str] = (),
    subject: str = "",
) -> str:
    """An email from a lead (it answers one we sent, or comes from a lead we wrote to): the lead is marked
    replied and their sequences stop. Returns who replied ('' when it is not from a lead)."""
    if not _has_leads(connection):
        return ""
    ensure(connection)
    lead_id = ""
    ids = [item for item in threads if item]
    if ids:
        marks = ", ".join("?" for _ in ids)
        row = kit.fetch_one(
            connection,
            f"SELECT lead_id FROM lead_messages WHERE message_id IN ({marks}) LIMIT 1",  # noqa: S608
            tuple(ids),
        )
        lead_id = str(row["lead_id"]) if row else ""
    address = str(from_email or "").strip().lower()
    if not lead_id and address:
        row = kit.fetch_one(
            connection,
            """SELECT l.id FROM leads l WHERE lower(l.email) = ? AND (
                EXISTS (SELECT 1 FROM lead_messages m WHERE m.lead_id = l.id AND m.status = 'sent')
                OR EXISTS (SELECT 1 FROM lead_enrollments e WHERE e.lead_id = l.id AND e.status = 'active'))
            ORDER BY l.updated_at DESC LIMIT 1""",
            (address,),
        )
        lead_id = str(row["id"]) if row else ""
    if not lead_id:
        return ""
    lead = store.get_lead(connection, lead_id)
    if lead["status"] in ("new", "contacted"):
        connection.execute(
            "UPDATE leads SET status = 'replied', updated_at = ? WHERE id = ?",
            (store.now(), lead_id),
        )
    stop_for_lead(connection, lead_id, "replied")
    connection.commit()
    return f"reply from {lead.get('name') or address}"[:120]


# ---------------------------------------------------------------------------
# the background pass
# ---------------------------------------------------------------------------


def _fill(text: str, lead: Mapping[str, Any]) -> str:
    name = str(lead.get("name") or "").strip()
    first = str(lead.get("first_name") or "").strip() or (
        name.split(" ")[0] if name else "there"
    )
    return (
        text.replace("{first_name}", first)
        .replace("{name}", name or first)
        .replace("{company}", str(lead.get("company") or name or "your business"))
    )


def tick(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> dict[str, int]:
    """Write the steps that are due into the Leads outbox, and stop the sequences that should stop."""
    if not _has_leads(connection):
        return {"written": 0, "stopped": 0, "finished": 0}
    ensure(connection)
    moment = _now(now)
    written = stopped = finished = 0
    sequences = {
        row["id"]: _row(row)
        for row in kit.fetch_all(connection, "SELECT * FROM lead_sequences")
    }
    for enrollment in kit.fetch_all(
        connection,
        "SELECT * FROM lead_enrollments WHERE status = 'active' ORDER BY next_at LIMIT 2000",
    ):
        sequence = sequences.get(enrollment["sequence_id"])
        if sequence is None or sequence["status"] != "active":
            continue
        try:
            lead = store.get_lead(connection, enrollment["lead_id"])
        except LookupError:
            _stop(connection, enrollment, "the lead was deleted")
            stopped += 1
            continue
        why = ""
        if lead["status"] in DONE_LEAD_STATUSES:
            why = (
                "replied"
                if lead["status"] == "replied"
                else f"the lead is {lead['status']}"
            )
        elif not lead.get("email"):
            why = "no email address"
        elif mail.suppressed_reason(connection, lead):
            why = "on the do-not-contact list"
        if why:
            _stop(connection, enrollment, why)
            stopped += 1
            continue
        base = _parse(enrollment["created_at"])
        if enrollment["last_message_id"]:
            previous = kit.fetch_one(
                connection,
                "SELECT * FROM lead_messages WHERE id = ?",
                (enrollment["last_message_id"],),
            )
            if previous is None or previous["status"] in ("rejected", "skipped"):
                _stop(connection, enrollment, "a step was not sent")
                stopped += 1
                continue
            if previous["status"] != "sent" or not previous["sent_at"]:
                continue  # the step before still waits in the outbox
            base = _parse(previous["sent_at"])
        step = int(enrollment["step"])
        if step >= len(sequence["steps"]):
            connection.execute(
                "UPDATE lead_enrollments SET status = 'finished', next_at = '', updated_at = ? WHERE id = ?",
                (_stamp(moment), enrollment["id"]),
            )
            finished += 1
            continue
        spec = sequence["steps"][step]
        due = (
            base + timedelta(days=int(spec["delay_days"]))
            if step
            else _parse(enrollment["next_at"] or enrollment["created_at"])
        )
        if due > moment:
            if enrollment["next_at"] != _stamp(due):
                connection.execute(
                    "UPDATE lead_enrollments SET next_at = ? WHERE id = ?",
                    (_stamp(due), enrollment["id"]),
                )
            continue
        first_subject = sequence["steps"][0]["subject"]
        subject = _fill(spec["subject"] or f"Re: {first_subject}", lead)
        message_id = store.new_id()
        connection.execute(
            """INSERT INTO lead_messages (id, lead_id, channel, to_address, subject, body, status, source, campaign, created_at, approved_at)
            VALUES (?, ?, 'email', ?, ?, ?, ?, 'sequence', ?, ?, ?)""",
            (
                message_id,
                lead["id"],
                lead["email"],
                store.clean(subject, 200),
                _fill(spec["body"], lead),
                "draft" if sequence["review"] else "approved",
                f"sequence:{sequence['id']}:{step + 1}",
                store.now(),
                "" if sequence["review"] else store.now(),
            ),
        )
        connection.execute(
            "UPDATE lead_enrollments SET step = ?, last_message_id = ?, next_at = '', updated_at = ? WHERE id = ?",
            (step + 1, message_id, _stamp(moment), enrollment["id"]),
        )
        written += 1
    connection.commit()
    return {"written": written, "stopped": stopped, "finished": finished}
