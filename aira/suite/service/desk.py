"""Service: cases, replies, SLA due times, satisfaction, saved replies, the help centre and live chat.

Times are stored as ISO UTC; business hours are read in the business time zone (kit.business).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit, public
from aira.suite.service import store
from aira.suite.service.store import APP, CHANNELS, OPEN_STATUSES, PRIORITIES, STATUSES

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": ("open", "waiting", "solved", "closed"),
    "open": ("waiting", "solved", "closed"),
    "waiting": ("open", "solved", "closed"),
    "solved": ("open", "waiting", "closed"),
    "closed": (),
}
DUE_SOON = timedelta(hours=2)
MAX_ATTACHMENTS = 5


def _now(now: datetime | None) -> datetime:
    return (now or kit.utcnow()).astimezone(timezone.utc)


def _zone(connection: sqlite3.Connection) -> ZoneInfo:
    try:
        return ZoneInfo(kit.business(connection).get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# business hours and SLA
# ---------------------------------------------------------------------------


def _window(
    day: datetime, settings: Mapping[str, Any]
) -> tuple[datetime, datetime] | None:
    """The working window of the local calendar day of ``day`` (local datetimes), or None on a day off."""
    if day.weekday() not in settings["days"]:
        return None
    sh, sm = (int(part) for part in settings["start"].split(":"))
    eh, em = (int(part) for part in settings["end"].split(":"))
    start = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start, end


def add_working_minutes(
    connection: sqlite3.Connection,
    start: datetime,
    minutes: int,
    settings: Mapping[str, Any],
) -> datetime:
    """``start`` plus ``minutes``, counting only business hours when the business turned them on."""
    if not settings.get("business_hours"):
        return start + timedelta(minutes=minutes)
    zone = _zone(connection)
    cursor = start.astimezone(zone)
    left = timedelta(minutes=minutes)
    for _ in range(400):  # at most a year of days
        window = _window(cursor, settings)
        if window is not None:
            begin, end = window
            if cursor < begin:
                cursor = begin
            if cursor < end:
                room = end - cursor
                if left <= room:
                    return (cursor + left).astimezone(timezone.utc)
                left -= room
        nxt = (cursor + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cursor = nxt
    return (start + timedelta(minutes=minutes)).astimezone(timezone.utc)


def due_times(
    connection: sqlite3.Connection,
    created: datetime,
    priority: str,
    settings: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    rules = settings or store.settings(connection)
    sla = rules["sla"][priority]
    first = add_working_minutes(connection, created, int(sla["first"]), rules)
    resolve = add_working_minutes(connection, created, int(sla["resolve"]), rules)
    return kit.iso(first), kit.iso(resolve)


def is_overdue(case: Mapping[str, Any], now: datetime | None = None) -> bool:
    if case["status"] not in OPEN_STATUSES:
        return False
    moment = _now(now)
    if not case.get("first_responded_at") and case.get("first_response_due"):
        if kit.parse_time(case["first_response_due"]) < moment:
            return True
    if case.get("resolution_due") and kit.parse_time(case["resolution_due"]) < moment:
        return True
    return False


def due_soon(case: Mapping[str, Any], now: datetime | None = None) -> bool:
    if case["status"] not in OPEN_STATUSES or is_overdue(case, now):
        return False
    moment = _now(now)
    marks = [case.get("resolution_due")]
    if not case.get("first_responded_at"):
        marks.append(case.get("first_response_due"))
    return any(mark and kit.parse_time(mark) - moment <= DUE_SOON for mark in marks)


def _view(case: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    case["overdue"] = is_overdue(case, now)
    case["due_soon"] = due_soon(case, now)
    return case


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


def _attachments(value: Any) -> list[str]:
    items = (
        value if isinstance(value, list) else [v for v in str(value or "").split() if v]
    )
    out = []
    for item in items[:MAX_ATTACHMENTS]:
        link = kit.url(item, "an attachment link")
        if link and not link.lower().startswith("https://"):
            raise ValueError("Attachment links must start with https://.")
        if link:
            out.append(link)
    return out


def _message(
    connection: sqlite3.Connection,
    case_id: str,
    kind: str,
    body: str,
    *,
    name: str = "",
    internal: bool = False,
    attachments: list[str] | None = None,
    at: str = "",
) -> dict[str, Any]:
    row = {
        "id": kit.new_id(),
        "case_id": case_id,
        "author_kind": kind,
        "author_name": name[:120],
        "body": body,
        "internal": 1 if internal else 0,
        "attachments": kit.dumps(attachments or []),
        "created_at": at or kit.now(),
    }
    connection.execute(
        """INSERT INTO sv_messages (id, case_id, author_kind, author_name, body, internal, attachments, created_at)
        VALUES (:id, :case_id, :author_kind, :author_name, :body, :internal, :attachments, :created_at)""",
        row,
    )
    return {**row, "internal": bool(internal), "attachments": attachments or []}


def case_link(
    connection: sqlite3.Connection, case: Mapping[str, Any]
) -> dict[str, Any]:
    return public.create_link(
        connection,
        APP,
        "case",
        case["id"],
        title=f"Case {case['reference']}: {case['subject']}"[:200],
    )


def create_case(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    channel: str = "manual",
    origin: Any = None,
    now: datetime | None = None,
    acknowledge: bool = True,
) -> dict[str, Any]:
    store.ensure(connection)
    channel = kit.choice(channel, "the channel", CHANNELS, default="manual")
    subject = kit.text(raw.get("subject"), "a subject", max_len=200, required=True)
    description = kit.text(
        raw.get("description") or raw.get("message"),
        "a description",
        max_len=10000,
        multiline=True,
    )
    name = kit.text(
        raw.get("customer_name") or raw.get("name"), "the customer's name", max_len=120
    )
    email = kit.email(
        raw.get("customer_email") or raw.get("email"), "the customer's email"
    )
    phone = kit.phone(
        raw.get("customer_phone") or raw.get("phone"), "the customer's phone"
    )
    if channel in ("web", "chat", "email") and not email:
        raise ValueError("Enter an email address so we can reply.")
    priority = kit.choice(
        raw.get("priority"), "the priority", PRIORITIES, default="normal"
    )
    moment = _now(now)
    first_due, resolve_due = due_times(connection, moment, priority)
    case_id = kit.new_id()
    stamp = kit.iso(moment)
    connection.execute(
        """INSERT INTO sv_cases (id, number, subject, description, customer_name, customer_email, customer_phone, channel,
        priority, status, assignee, tags, first_response_due, resolution_due, last_customer_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?)""",
        (
            case_id,
            store.next_number(connection),
            subject,
            description,
            name,
            email,
            phone,
            channel,
            priority,
            kit.text(raw.get("assignee"), "the assignee", max_len=120),
            kit.dumps(kit.tags(raw.get("tags"))),
            first_due,
            resolve_due,
            stamp if channel != "manual" else None,
            stamp,
            stamp,
        ),
    )
    if description:
        _message(
            connection,
            case_id,
            "customer",
            description,
            name=name or email,
            attachments=_attachments(raw.get("attachments")),
            at=stamp,
        )
    case = store.get_case(connection, case_id)
    link = case_link(connection, case)
    kit.log(
        connection,
        APP,
        "case",
        f"New case {case['reference']}: {subject}",
        detail=name or email,
        ref_type="case",
        ref_id=case_id,
        public=channel in ("web", "chat", "email"),
    )
    if acknowledge and email and channel != "manual":
        kit.queue_email(
            connection,
            APP,
            "case_received",
            to=email,
            subject=f"We received your request {case['reference']}",
            body=(
                f"Hello {(name or 'there').split(' ')[0]},\n\nThanks for getting in touch. We received your request "
                f'"{subject}" and will reply soon.\n\nYou can follow it and reply here: '
                f"{kit.public_url(link['path'], origin)}\n\nYour reference is {case['reference']}."
            ),
            ref_type="case",
            ref_id=case_id,
        )
    connection.commit()
    return _view(store.get_case(connection, case_id), now)


def get_case(
    connection: sqlite3.Connection, case_id: str, now: datetime | None = None
) -> dict[str, Any]:
    return _view(store.get_case(connection, case_id), now)


def case_detail(
    connection: sqlite3.Connection, case_id: str, now: datetime | None = None
) -> dict[str, Any]:
    case = get_case(connection, case_id, now)
    return {
        "case": case,
        "messages": store.messages(connection, case_id),
        "link": public.link_for(connection, APP, "case", case_id),
    }


def _set_status(case: Mapping[str, Any], status: str, stamp: str) -> dict[str, Any]:
    status = kit.choice(status, "the status", STATUSES)
    current = case["status"]
    if status == current:
        return {}
    if status not in TRANSITIONS[current]:
        if current == "closed":
            raise ValueError(
                "A closed case cannot be reopened. Create a new case instead."
            )
        raise ValueError(f"A case cannot go from {current} to {status}.")
    values: dict[str, Any] = {"status": status}
    if status == "solved":
        values["solved_at"] = stamp
    elif status == "closed":
        values["closed_at"] = stamp
        if not case.get("solved_at"):
            values["solved_at"] = stamp
    elif current == "solved":
        values["solved_at"] = None  # reopened
    return values


def _after_solved(
    connection: sqlite3.Connection, case: Mapping[str, Any], origin: Any
) -> None:
    """Ask the customer how we did, once."""
    if (
        case.get("csat_requested")
        or not case.get("customer_email")
        or case.get("merged_into")
    ):
        return
    link = case_link(connection, case)
    kit.queue_email(
        connection,
        APP,
        "csat_request",
        to=case["customer_email"],
        subject=f"How did we do with {case['reference']}?",
        body=(
            f"Hello {(case.get('customer_name') or 'there').split(' ')[0]},\n\nWe marked your request "
            f'"{case["subject"]}" as solved. Could you tell us how we did? It takes a few seconds:\n'
            f"{kit.public_url(link['path'], origin)}\n\nIf something is still not right, just reply on that page."
        ),
        ref_type="case",
        ref_id=case["id"],
    )
    connection.execute(
        "UPDATE sv_cases SET csat_requested = 1 WHERE id = ?", (case["id"],)
    )


def update_case(
    connection: sqlite3.Connection,
    case_id: str,
    raw: Mapping[str, Any],
    *,
    actor: str = "",
    origin: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    case = store.get_case(connection, case_id)
    if case["merged_into"]:
        raise ValueError("This case was merged into another one.")
    stamp = kit.iso(_now(now))
    values: dict[str, Any] = {}
    if "subject" in raw:
        values["subject"] = kit.text(
            raw["subject"], "a subject", max_len=200, required=True
        )
    if "assignee" in raw:
        values["assignee"] = kit.text(raw["assignee"], "the assignee", max_len=120)
    if "tags" in raw:
        values["tags"] = kit.dumps(kit.tags(raw["tags"]))
    if "customer_name" in raw:
        values["customer_name"] = kit.text(
            raw["customer_name"], "the customer's name", max_len=120
        )
    if "customer_email" in raw:
        values["customer_email"] = kit.email(
            raw["customer_email"], "the customer's email"
        )
    if "customer_phone" in raw:
        values["customer_phone"] = kit.phone(
            raw["customer_phone"], "the customer's phone"
        )
    if "priority" in raw:
        priority = kit.choice(raw["priority"], "the priority", PRIORITIES)
        if priority != case["priority"]:
            first_due, resolve_due = due_times(
                connection, kit.parse_time(case["created_at"]), priority
            )
            values.update(
                {
                    "priority": priority,
                    "first_response_due": first_due,
                    "resolution_due": resolve_due,
                }
            )
    if raw.get("status"):
        values.update(_set_status(case, str(raw["status"]), stamp))
    if not values:
        return get_case(connection, case_id, now)
    values["updated_at"] = stamp
    kit.update_row(connection, "sv_cases", case_id, values)
    if values.get("status"):
        _message(
            connection,
            case_id,
            "system",
            f"Status changed to {values['status']}.",
            name=actor,
            internal=True,
            at=stamp,
        )
        if values["status"] == "solved":
            kit.log(
                connection,
                APP,
                "solved",
                f"Case {case['reference']} solved",
                ref_type="case",
                ref_id=case_id,
                actor=actor,
            )
            _after_solved(connection, store.get_case(connection, case_id), origin)
    connection.commit()
    return get_case(connection, case_id, now)


def reply(
    connection: sqlite3.Connection,
    case_id: str,
    raw: Mapping[str, Any],
    *,
    author: str = "",
    origin: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """An agent's reply (sent to the customer through the outbox) or an internal note (never shown to them)."""
    case = store.get_case(connection, case_id)
    if case["merged_into"]:
        raise ValueError("This case was merged into another one; reply there.")
    if case["status"] == "closed":
        raise ValueError("This case is closed.")
    body = kit.text(
        raw.get("body"), "a reply", max_len=10000, required=True, multiline=True
    )
    internal = kit.flag(raw.get("internal"))
    attachments = _attachments(raw.get("attachments"))
    stamp = kit.iso(_now(now))
    message = _message(
        connection,
        case_id,
        "agent",
        body,
        name=author,
        internal=internal,
        attachments=attachments,
        at=stamp,
    )
    values: dict[str, Any] = {"updated_at": stamp}
    if not internal:
        values["last_agent_at"] = stamp
        if not case["first_responded_at"]:
            values["first_responded_at"] = stamp
        wanted = str(raw.get("status_after") or "waiting")
        values.update(
            _set_status(case, wanted, stamp) if wanted != case["status"] else {}
        )
        link = case_link(connection, case)
        if case["customer_email"]:
            files = "".join(f"\n{item}" for item in attachments)
            kit.queue_email(
                connection,
                APP,
                "case_reply",
                to=case["customer_email"],
                subject=f"Re: {case['subject']} [{case['reference']}]",
                body=(
                    f"{body}{files}\n\nTo reply or see the whole conversation: {kit.public_url(link['path'], origin)}\n"
                    f"Reference {case['reference']}."
                ),
                ref_type="case",
                ref_id=case_id,
            )
    elif case["status"] == "new":
        values.update(_set_status(case, "open", stamp))
    kit.update_row(connection, "sv_cases", case_id, values)
    if values.get("status") == "solved":
        _after_solved(connection, store.get_case(connection, case_id), origin)
    kit.log(
        connection,
        APP,
        "note" if internal else "reply",
        f"{'Note on' if internal else 'Replied to'} {case['reference']}",
        ref_type="case",
        ref_id=case_id,
        actor=author,
    )
    connection.commit()
    return {"message": message, "case": get_case(connection, case_id, now)}


def customer_reply(
    connection: sqlite3.Connection,
    case_id: str,
    raw: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    case = store.get_case(connection, case_id)
    if case["merged_into"]:
        case = store.get_case(connection, case["merged_into"])
    if case["status"] == "closed":
        raise ValueError(
            "This request is closed. Please start a new one from our help centre."
        )
    body = kit.text(
        raw.get("body"), "your message", max_len=5000, required=True, multiline=True
    )
    stamp = kit.iso(_now(now))
    _message(
        connection,
        case["id"],
        "customer",
        body,
        name=case["customer_name"] or case["customer_email"],
        attachments=_attachments(raw.get("attachments")),
        at=stamp,
    )
    values: dict[str, Any] = {"last_customer_at": stamp, "updated_at": stamp}
    if case["status"] in ("waiting", "solved"):
        values.update(_set_status(case, "open", stamp))
    kit.update_row(connection, "sv_cases", case["id"], values)
    kit.log(
        connection,
        APP,
        "customer_reply",
        f"{case['customer_name'] or 'The customer'} replied on {case['reference']}",
        ref_type="case",
        ref_id=case["id"],
        public=True,
    )
    connection.commit()
    return get_case(connection, case["id"], now)


def merge(
    connection: sqlite3.Connection,
    source_id: str,
    target_id: str,
    *,
    actor: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Merge a duplicate (``source``) into ``target``: its messages move, it closes and points at the target."""
    if source_id == target_id:
        raise ValueError("Choose a different case to merge into.")
    source = store.get_case(connection, source_id)
    target = store.get_case(connection, target_id)
    if source["merged_into"]:
        raise ValueError("This case was already merged.")
    if target["merged_into"] or target["status"] == "closed":
        raise ValueError("Merge into a case that is still open.")
    stamp = kit.iso(_now(now))
    connection.execute(
        "UPDATE sv_messages SET case_id = ? WHERE case_id = ?", (target_id, source_id)
    )
    _message(
        connection,
        target_id,
        "system",
        f"Case {source['reference']} ({source['subject']}) was merged into this case.",
        name=actor,
        internal=True,
        at=stamp,
    )
    kit.update_row(
        connection,
        "sv_cases",
        source_id,
        {
            "status": "closed",
            "closed_at": stamp,
            "merged_into": target_id,
            "updated_at": stamp,
        },
    )
    kit.update_row(connection, "sv_cases", target_id, {"updated_at": stamp})
    connection.commit()
    return get_case(connection, target_id, now)


def rate(
    connection: sqlite3.Connection, case_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    case = store.get_case(connection, case_id)
    if case["status"] not in ("solved", "closed") or case["merged_into"]:
        raise ValueError("You can rate a request once it is solved.")
    if case["csat_score"] is not None:
        raise ValueError("Thank you, you already rated this request.")
    score = kit.integer(raw.get("score"), "a rating", minimum=1, maximum=5)
    comment = kit.text(raw.get("comment"), "a comment", max_len=1000, multiline=True)
    kit.update_row(
        connection,
        "sv_cases",
        case_id,
        {
            "csat_score": score,
            "csat_comment": comment,
            "csat_at": kit.now(),
            "updated_at": kit.now(),
        },
    )
    kit.log(
        connection,
        APP,
        "csat",
        f"{case['reference']} rated {score} out of 5",
        detail=comment[:200],
        ref_type="case",
        ref_id=case_id,
        public=True,
    )
    connection.commit()
    return store.get_case(connection, case_id) or {}


VIEWS = (
    "all",
    "mine",
    "unassigned",
    "due_soon",
    "overdue",
    "waiting",
    "open",
    "solved",
    "closed",
)


def list_cases(
    connection: sqlite3.Connection,
    *,
    view: str = "open",
    me: str = "",
    q: str = "",
    status: str = "",
    priority: str = "",
    channel: str = "",
    tag: str = "",
    now: datetime | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    view = kit.choice(view, "the view", VIEWS, default="open")
    clauses, params = ["merged_into = ''"], []
    if view in ("open", "mine", "unassigned", "due_soon", "overdue"):
        clauses.append("status IN ('new', 'open', 'waiting')")
    elif view in ("waiting", "solved", "closed"):
        clauses.append("status = ?")
        params.append(view)
    if view == "mine":
        clauses.append("lower(assignee) = lower(?)")
        params.append(me)
    if view == "unassigned":
        clauses.append("assignee = ''")
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", STATUSES))
    if priority:
        clauses.append("priority = ?")
        params.append(kit.choice(priority, "the priority", PRIORITIES))
    if channel:
        clauses.append("channel = ?")
        params.append(kit.choice(channel, "the channel", CHANNELS))
    if q:
        like = f"%{kit.text(q, 'a search', max_len=100).lower()}%"
        clauses.append(
            "(lower(subject) LIKE ? OR lower(customer_name) LIKE ? OR lower(customer_email) LIKE ? OR CAST(number AS TEXT) LIKE ?)"
        )
        params += [like, like, like, like.strip("%#")]
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM sv_cases WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 2000))),
    )
    cases = [_view(store.case_row(row) or {}, now) for row in rows]
    if tag:
        cases = [case for case in cases if tag.lower() in case["tags"]]
    if view == "overdue":
        cases = [case for case in cases if case["overdue"]]
    if view == "due_soon":
        cases = [case for case in cases if case["due_soon"]]
    if view in ("open", "mine", "unassigned", "overdue", "due_soon"):
        order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        cases.sort(
            key=lambda c: (
                not c["overdue"],
                order[c["priority"]],
                c["resolution_due"] or "",
            )
        )
    return cases


def view_counts(
    connection: sqlite3.Connection, *, me: str = "", now: datetime | None = None
) -> dict[str, int]:
    open_cases = list_cases(connection, view="open", now=now)
    return {
        "open": len(open_cases),
        "mine": sum(
            1 for c in open_cases if me and c["assignee"].lower() == me.lower()
        ),
        "unassigned": sum(1 for c in open_cases if not c["assignee"]),
        "due_soon": sum(1 for c in open_cases if c["due_soon"]),
        "overdue": sum(1 for c in open_cases if c["overdue"]),
        "waiting": sum(1 for c in open_cases if c["status"] == "waiting"),
    }


CSV_COLUMNS = (
    "reference",
    "subject",
    "status",
    "priority",
    "channel",
    "customer_name",
    "customer_email",
    "assignee",
    "tags",
    "created_at",
    "first_responded_at",
    "solved_at",
    "csat_score",
)


def suggest_reply(connection: sqlite3.Connection, case_id: str) -> dict[str, Any]:
    case = store.get_case(connection, case_id)
    thread = store.messages(connection, case_id, public_only=True)[-8:]
    profile = kit.business(connection)
    first = (case["customer_name"] or "there").split(" ")[0]
    last_customer = next(
        (m["body"] for m in reversed(thread) if m["author_kind"] == "customer"),
        case["description"],
    )
    template = (
        f'Hello {first},\n\nThank you for getting in touch about "{case["subject"]}". '
        "I am looking into this now and will update you as soon as I have an answer. If you have any more details, "
        "such as a screenshot or an order number, please reply here.\n\n"
        f"Best wishes,\n{profile.get('name') or 'The team'}"
    )
    transcript = "\n".join(f"{m['author_kind']}: {m['body'][:600]}" for m in thread)
    prompt = (
        "You are a friendly, concise customer support agent for "
        f"{profile.get('name') or 'a small business'}. Write the next reply to the customer. Be specific to their "
        "message, polite and short (under 120 words). Do not promise refunds or dates you do not know. Return JSON "
        f'{{"reply": "..."}}.\n\nCase: {case["subject"]}\nLatest message: {last_customer[:1500]}\nConversation:\n{transcript}'
    )
    return kit.draft(prompt, {"reply": template})


# ---------------------------------------------------------------------------
# saved replies
# ---------------------------------------------------------------------------


def list_macros(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection, "SELECT * FROM sv_macros ORDER BY uses DESC, title"
    )


def save_macro(
    connection: sqlite3.Connection, raw: Mapping[str, Any], macro_id: str = ""
) -> dict[str, Any]:
    title = kit.text(raw.get("title"), "a name", max_len=120, required=True)
    body = kit.text(
        raw.get("body"), "the reply text", max_len=5000, required=True, multiline=True
    )
    stamp = kit.now()
    if macro_id:
        store.get_macro(connection, macro_id)
        kit.update_row(
            connection,
            "sv_macros",
            macro_id,
            {"title": title, "body": body, "updated_at": stamp},
        )
    else:
        macro_id = kit.new_id()
        connection.execute(
            "INSERT INTO sv_macros (id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (macro_id, title, body, stamp, stamp),
        )
    connection.commit()
    return store.get_macro(connection, macro_id)


def fill_macro(body: str, case: Mapping[str, Any]) -> str:
    first = (case.get("customer_name") or "there").split(" ")[0]
    return body.replace("{customer}", first).replace(
        "{case}", str(case.get("reference") or "")
    )


def use_macro(connection: sqlite3.Connection, macro_id: str, case_id: str) -> str:
    macro = store.get_macro(connection, macro_id)
    case = store.get_case(connection, case_id)
    connection.execute("UPDATE sv_macros SET uses = uses + 1 WHERE id = ?", (macro_id,))
    connection.commit()
    return fill_macro(macro["body"], case)


# ---------------------------------------------------------------------------
# help centre
# ---------------------------------------------------------------------------


def list_categories(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection, "SELECT * FROM sv_categories ORDER BY position, name"
    )


def save_category(
    connection: sqlite3.Connection, raw: Mapping[str, Any], category_id: str = ""
) -> dict[str, Any]:
    name = kit.text(raw.get("name"), "a category name", max_len=80, required=True)
    position = kit.integer(
        raw.get("position"), "the position", default=0, minimum=0, maximum=999
    )
    if category_id:
        if not kit.fetch_one(
            connection, "SELECT 1 FROM sv_categories WHERE id = ?", (category_id,)
        ):
            raise LookupError("That category was not found.")
        kit.update_row(
            connection,
            "sv_categories",
            category_id,
            {"name": name, "position": position},
        )
    else:
        category_id = kit.new_id()
        connection.execute(
            "INSERT INTO sv_categories (id, name, position, created_at) VALUES (?, ?, ?, ?)",
            (category_id, name, position, kit.now()),
        )
    connection.commit()
    return (
        kit.fetch_one(
            connection, "SELECT * FROM sv_categories WHERE id = ?", (category_id,)
        )
        or {}
    )


def delete_category(connection: sqlite3.Connection, category_id: str) -> None:
    if not kit.fetch_one(
        connection, "SELECT 1 FROM sv_categories WHERE id = ?", (category_id,)
    ):
        raise LookupError("That category was not found.")
    connection.execute(
        "UPDATE sv_articles SET category_id = '' WHERE category_id = ?", (category_id,)
    )
    connection.execute("DELETE FROM sv_categories WHERE id = ?", (category_id,))
    connection.commit()


def save_article(
    connection: sqlite3.Connection, raw: Mapping[str, Any], article_id: str = ""
) -> dict[str, Any]:
    current = store.get_article(connection, article_id) if article_id else {}
    merged = {**current, **raw}
    title = kit.text(merged.get("title"), "a title", max_len=200, required=True)
    body = kit.text(merged.get("body"), "the article", max_len=50000, multiline=True)
    status = kit.choice(
        merged.get("status"), "the status", ("draft", "published"), default="draft"
    )
    category_id = str(merged.get("category_id") or "")
    if category_id and not kit.fetch_one(
        connection, "SELECT 1 FROM sv_categories WHERE id = ?", (category_id,)
    ):
        raise ValueError("Choose a category from the list.")
    if status == "published" and not body:
        raise ValueError("Write the article before publishing it.")
    stamp = kit.now()
    values = {
        "title": title,
        "body": body,
        "status": status,
        "category_id": category_id,
        "updated_at": stamp,
    }
    if article_id:
        kit.update_row(connection, "sv_articles", article_id, values)
    else:
        article_id = kit.new_id()
        connection.execute(
            "INSERT INTO sv_articles (id, category_id, title, body, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (article_id, category_id, title, body, status, stamp, stamp),
        )
    connection.commit()
    return store.get_article(connection, article_id)


def delete_article(connection: sqlite3.Connection, article_id: str) -> None:
    store.get_article(connection, article_id)
    connection.execute("DELETE FROM sv_articles WHERE id = ?", (article_id,))
    connection.execute(
        "DELETE FROM sv_article_votes WHERE article_id = ?", (article_id,)
    )
    connection.commit()


def list_articles(
    connection: sqlite3.Connection, *, published_only: bool = False, q: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if published_only:
        clauses.append("status = 'published'")
    if q:
        like = f"%{kit.text(q, 'a search', max_len=100).lower()}%"
        clauses.append("(lower(title) LIKE ? OR lower(body) LIKE ?)")
        params += [like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM sv_articles {where} ORDER BY updated_at DESC",
        params,
    )  # noqa: S608
    if q:
        needle = q.strip().lower()
        rows.sort(
            key=lambda r: (needle not in r["title"].lower(), -int(r["views"] or 0))
        )
    return rows


def public_article(row: Mapping[str, Any], *, full: bool = False) -> dict[str, Any]:
    body = str(row.get("body") or "")
    out = {
        "id": row["id"],
        "title": row["title"],
        "category_id": row.get("category_id") or "",
        "excerpt": (body[:180] + "…") if len(body) > 180 else body,
        "updated_at": row["updated_at"],
    }
    if full:
        out["body"] = body
        out["helpful_yes"] = int(row.get("helpful_yes") or 0)
    return out


def read_article(connection: sqlite3.Connection, article_id: str) -> dict[str, Any]:
    row = store.get_article(connection, article_id)
    if row["status"] != "published":
        raise LookupError("That article was not found.")
    connection.execute(
        "UPDATE sv_articles SET views = views + 1 WHERE id = ?", (article_id,)
    )
    connection.commit()
    return public_article(row, full=True)


def vote(
    connection: sqlite3.Connection, article_id: str, client: str, helpful: bool
) -> dict[str, Any]:
    row = store.get_article(connection, article_id)
    if row["status"] != "published":
        raise LookupError("That article was not found.")
    key = hashlib.sha256(str(client or "?").encode()).hexdigest()[:32]
    previous = connection.execute(
        "SELECT helpful FROM sv_article_votes WHERE article_id = ? AND client = ?",
        (article_id, key),
    ).fetchone()
    if previous is not None:
        if bool(previous[0]) == helpful:
            return {"recorded": False}
        column = "helpful_yes" if previous[0] else "helpful_no"
        connection.execute(
            f"UPDATE sv_articles SET {column} = MAX(0, {column} - 1) WHERE id = ?",
            (article_id,),
        )  # noqa: S608
    connection.execute(
        "INSERT OR REPLACE INTO sv_article_votes (article_id, client, helpful, at) VALUES (?, ?, ?, ?)",
        (article_id, key, 1 if helpful else 0, kit.now()),
    )
    column = "helpful_yes" if helpful else "helpful_no"
    connection.execute(
        f"UPDATE sv_articles SET {column} = {column} + 1 WHERE id = ?", (article_id,)
    )  # noqa: S608
    connection.commit()
    return {"recorded": True}


# ---------------------------------------------------------------------------
# live chat
# ---------------------------------------------------------------------------


def chat_online(connection: sqlite3.Connection, now: datetime | None = None) -> bool:
    rules = store.settings(connection)
    if not rules["chat_enabled"]:
        return False
    if not rules["chat_in_hours_only"]:
        return True
    local = _now(now).astimezone(_zone(connection))
    window = _window(local, rules)
    return window is not None and window[0] <= local < window[1]


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode()).hexdigest()


def _chat_message(
    connection: sqlite3.Connection, chat_id: str, kind: str, name: str, body: str
) -> dict[str, Any]:
    row = {
        "id": kit.new_id(),
        "chat_id": chat_id,
        "author_kind": kind,
        "author_name": name[:120],
        "body": body,
        "created_at": kit.now(),
    }
    connection.execute(
        "INSERT INTO sv_chat_messages (id, chat_id, author_kind, author_name, body, created_at) VALUES (:id, :chat_id, :author_kind, :author_name, :body, :created_at)",
        row,
    )
    stamp_column = "last_visitor_at" if kind == "visitor" else "last_agent_at"
    connection.execute(
        f"UPDATE sv_chats SET {stamp_column} = ?, updated_at = ? WHERE id = ?",  # noqa: S608
        (row["created_at"], row["created_at"], chat_id),
    )
    return row


def start_chat(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not chat_online(connection, now):
        raise ValueError(
            "Live chat is closed right now. Please leave a message instead."
        )
    name = kit.text(raw.get("name"), "your name", max_len=80, required=True)
    email = kit.email(raw.get("email"), "your email")
    body = kit.text(
        raw.get("message"), "a message", max_len=2000, required=True, multiline=True
    )
    chat_id = kit.new_id()
    secret = secrets.token_urlsafe(24)
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sv_chats (id, secret_hash, visitor_name, visitor_email, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'waiting', ?, ?)""",
        (chat_id, _secret_hash(secret), name, email, stamp, stamp),
    )
    _chat_message(connection, chat_id, "visitor", name, body)
    kit.log(
        connection,
        APP,
        "chat",
        f"{name} started a live chat",
        ref_type="chat",
        ref_id=chat_id,
        public=True,
    )
    connection.commit()
    return {
        "chat_id": chat_id,
        "secret": secret,
        "messages": chat_messages(connection, chat_id),
    }


def _visitor_chat(
    connection: sqlite3.Connection, chat_id: Any, secret: Any
) -> dict[str, Any]:
    chat = kit.fetch_one(
        connection, "SELECT * FROM sv_chats WHERE id = ?", (str(chat_id or ""),)
    )
    if not chat or not hmac.compare_digest(chat["secret_hash"], _secret_hash(secret)):
        raise LookupError("That chat was not found.")
    return chat


def chat_messages(
    connection: sqlite3.Connection, chat_id: str, after: int = 0
) -> list[dict[str, Any]]:
    """The chat's messages, oldest first; ``after`` is a cursor (the last ``seq`` seen) for live updates."""
    return kit.fetch_all(
        connection,
        "SELECT rowid AS seq, * FROM sv_chat_messages WHERE chat_id = ? AND rowid > ? ORDER BY rowid",
        (chat_id, int(after or 0)),
    )


def _cursor(rows: list[dict[str, Any]], after: int) -> int:
    return max([int(after or 0), *[int(r["seq"]) for r in rows]])


def _public_chat_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": r["id"],
            "from": "you" if r["author_kind"] == "visitor" else "agent",
            "name": r["author_name"] if r["author_kind"] != "visitor" else "",
            "body": r["body"],
            "at": r["created_at"],
        }
        for r in rows
    ]


def visitor_send(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    chat = _visitor_chat(connection, raw.get("chat_id"), raw.get("secret"))
    if chat["status"] == "ended":
        raise ValueError("This chat has ended. Start a new one if you need more help.")
    body = kit.text(
        raw.get("body"), "a message", max_len=2000, required=True, multiline=True
    )
    _chat_message(connection, chat["id"], "visitor", chat["visitor_name"], body)
    connection.commit()
    return visitor_poll(connection, raw)


def visitor_poll(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    chat = _visitor_chat(connection, raw.get("chat_id"), raw.get("secret"))
    after = kit.integer(raw.get("after"), "the cursor", default=0, minimum=0)
    rows = chat_messages(connection, chat["id"], after)
    return {
        "status": chat["status"],
        "messages": _public_chat_messages(rows),
        "cursor": _cursor(rows, after),
    }


def list_chats(
    connection: sqlite3.Connection, *, include_ended: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_ended else "WHERE status != 'ended'"
    rows = kit.fetch_all(
        connection, f"SELECT * FROM sv_chats {where} ORDER BY updated_at DESC LIMIT 200"
    )  # noqa: S608
    for row in rows:
        row.pop("secret_hash", None)
        last = kit.fetch_one(
            connection,
            "SELECT body, author_kind FROM sv_chat_messages WHERE chat_id = ? ORDER BY created_at DESC, rowid DESC",
            (row["id"],),
        )
        row["last_message"] = (last or {}).get("body", "")[:140]
        row["waiting_for_us"] = bool(last and last["author_kind"] == "visitor")
    return rows


def chat_detail(
    connection: sqlite3.Connection, chat_id: str, after: int = 0
) -> dict[str, Any]:
    chat = store.get_chat(connection, chat_id)
    chat.pop("secret_hash", None)
    rows = chat_messages(connection, chat_id, after)
    return {"chat": chat, "messages": rows, "cursor": _cursor(rows, after)}


def agent_chat_reply(
    connection: sqlite3.Connection, chat_id: str, body: Any, *, author: str
) -> dict[str, Any]:
    chat = store.get_chat(connection, chat_id)
    if chat["status"] == "ended":
        raise ValueError("This chat has ended.")
    text = kit.text(body, "a message", max_len=2000, required=True, multiline=True)
    _chat_message(connection, chat_id, "agent", author or "Support", text)
    connection.execute(
        "UPDATE sv_chats SET status = 'active', assignee = CASE WHEN assignee = '' THEN ? ELSE assignee END WHERE id = ?",
        (author, chat_id),
    )
    connection.commit()
    return chat_detail(connection, chat_id)


def end_chat(connection: sqlite3.Connection, chat_id: str) -> dict[str, Any]:
    store.get_chat(connection, chat_id)
    connection.execute(
        "UPDATE sv_chats SET status = 'ended', updated_at = ? WHERE id = ?",
        (kit.now(), chat_id),
    )
    connection.commit()
    return chat_detail(connection, chat_id)


def chat_to_case(
    connection: sqlite3.Connection, chat_id: str, *, origin: Any = None
) -> dict[str, Any]:
    chat = store.get_chat(connection, chat_id)
    if chat["case_id"]:
        return get_case(connection, chat["case_id"])
    rows = chat_messages(connection, chat_id)
    transcript = "\n".join(
        f"{'Visitor' if r['author_kind'] == 'visitor' else r['author_name'] or 'Agent'}: {r['body']}"
        for r in rows
    )
    first = next(
        (r["body"] for r in rows if r["author_kind"] == "visitor"), "Live chat"
    )
    case = create_case(
        connection,
        {
            "subject": f"Live chat: {first[:80]}",
            "description": f"Live chat transcript:\n{transcript}",
            "name": chat["visitor_name"],
            "email": chat["visitor_email"],
        },
        channel="chat" if chat["visitor_email"] else "manual",
        origin=origin,
        acknowledge=False,
    )
    connection.execute(
        "UPDATE sv_chats SET case_id = ?, updated_at = ? WHERE id = ?",
        (case["id"], kit.now(), chat_id),
    )
    connection.commit()
    return case


# ---------------------------------------------------------------------------
# numbers, background, hub
# ---------------------------------------------------------------------------


def _minutes(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (kit.parse_time(end) - kit.parse_time(start)).total_seconds() / 60


def stats(
    connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    store.ensure(connection)
    rows = [
        store.case_row(r) or {}
        for r in kit.fetch_all(
            connection, "SELECT * FROM sv_cases WHERE merged_into = ''"
        )
    ]
    open_cases = [r for r in rows if r["status"] in OPEN_STATUSES]
    firsts = [
        m
        for m in (_minutes(r["created_at"], r["first_responded_at"]) for r in rows)
        if m is not None
    ]
    resolves = [
        m
        for m in (_minutes(r["created_at"], r["solved_at"]) for r in rows)
        if m is not None
    ]
    rated = [int(r["csat_score"]) for r in rows if r["csat_score"] is not None]
    moment = _now(now)
    week = kit.iso(moment - timedelta(days=7))
    by_channel: dict[str, int] = {}
    for row in rows:
        by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + 1
    return {
        "open": len(open_cases),
        "overdue": sum(1 for r in open_cases if is_overdue(r, now)),
        "unassigned": sum(1 for r in open_cases if not r["assignee"]),
        "new_this_week": sum(1 for r in rows if r["created_at"] >= week),
        "solved_this_week": sum(
            1 for r in rows if r["solved_at"] and r["solved_at"] >= week
        ),
        "first_response_minutes": round(sum(firsts) / len(firsts)) if firsts else None,
        "resolution_minutes": round(sum(resolves) / len(resolves))
        if resolves
        else None,
        "csat_average": round(sum(rated) / len(rated), 1) if rated else None,
        "csat_count": len(rated),
        "by_channel": by_channel,
        "waiting_chats": kit.count(
            connection, "SELECT COUNT(*) FROM sv_chats WHERE status = 'waiting'"
        ),
        "total": len(rows),
    }


def tick(connection: sqlite3.Connection, now: datetime | None = None) -> dict[str, int]:
    """The background pass: overdue flags, satisfaction requests for solved cases, and auto-closing."""
    store.ensure(connection)
    moment = _now(now)
    rules = store.settings(connection)
    flagged = closed = asked = 0
    for row in kit.fetch_all(
        connection, "SELECT * FROM sv_cases WHERE status IN ('new', 'open', 'waiting')"
    ):
        overdue = is_overdue(row, moment)
        if overdue != bool(row["overdue"]):
            connection.execute(
                "UPDATE sv_cases SET overdue = ? WHERE id = ?",
                (1 if overdue else 0, row["id"]),
            )
            if overdue:
                flagged += 1
                kit.log(
                    connection,
                    APP,
                    "overdue",
                    f"Case #{row['number']} is overdue",
                    ref_type="case",
                    ref_id=row["id"],
                )
    cutoff = kit.iso(moment - timedelta(days=int(rules["auto_close_days"])))
    for row in kit.fetch_all(
        connection,
        "SELECT * FROM sv_cases WHERE status = 'solved' AND merged_into = ''",
    ):
        case = store.case_row(row) or {}
        if not case["csat_requested"] and case["customer_email"]:
            _after_solved(connection, case, None)
            asked += 1
        last_customer = case["last_customer_at"] or ""
        if (
            case["solved_at"]
            and case["solved_at"] <= cutoff
            and last_customer <= case["solved_at"]
        ):
            stamp = kit.iso(moment)
            kit.update_row(
                connection,
                "sv_cases",
                case["id"],
                {"status": "closed", "closed_at": stamp, "updated_at": stamp},
            )
            _message(
                connection,
                case["id"],
                "system",
                "Closed automatically after no reply.",
                internal=True,
                at=stamp,
            )
            closed += 1
    connection.commit()
    return {"overdue": flagged, "closed": closed, "csat_requested": asked}


def checklist(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    store.ensure(connection)
    profile = kit.business(connection)
    shared = bool(
        public.link_for(connection, APP, "help_center", "help")
        or public.link_for(connection, APP, "chat", "chat")
    )
    return [
        kit.step(
            "profile",
            "Add your business details",
            bool(profile["name"]),
            "Business hub > Business profile",
        ),
        kit.step(
            "article",
            "Write a help article",
            kit.count(
                connection,
                "SELECT COUNT(*) FROM sv_articles WHERE status = 'published'",
            )
            > 0,
            "Answer the question customers ask most",
        ),
        kit.step(
            "help_center",
            "Share your help centre",
            bool(public.link_for(connection, APP, "help_center", "help")),
            "Customers find answers and contact you",
        ),
        kit.step(
            "contact",
            "Share a contact form or live chat",
            shared,
            "Put the link on your website",
        ),
        kit.step(
            "solved",
            "Solve your first case",
            kit.count(
                connection, "SELECT COUNT(*) FROM sv_cases WHERE solved_at IS NOT NULL"
            )
            > 0,
            "Reply and mark it solved",
        ),
    ]


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    numbers = stats(connection)
    return {
        "cards": [
            {"label": "Open cases", "value": numbers["open"]},
            {
                "label": "Overdue",
                "value": numbers["overdue"],
                "tone": "bad" if numbers["overdue"] else "",
            },
            {
                "label": "Satisfaction",
                "value": f"{numbers['csat_average']} / 5"
                if numbers["csat_average"] is not None
                else "-",
                "tone": "good" if (numbers["csat_average"] or 0) >= 4 else "",
            },
        ]
    }
