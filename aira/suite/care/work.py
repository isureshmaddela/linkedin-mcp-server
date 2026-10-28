"""Care work: earning money by looking after people (a home carer, a nurse who visits, a neighbour who helps an older
person every day).

The carer keeps their **clients** (who pays, at what rate an hour), records each **visit** (the day, how long, what
was done) and turns the visits not yet billed into a **statement**. The client opens the statement as a link
(``care_statement`` public page): the visits, the total and how to pay (the payment link and instructions from the
Business hub profile; Aira does not move money). The client can say "I have paid"; the carer marks it **paid**,
which records the money in the revenue ledger (Business hub > Revenue), once. Statements can be emailed through
the outbox (kind ``care_statement``; it waits for approval like every message to a customer). A statement counts
as **sent** once that email has really gone out; until then it shows the email is waiting for approval, and
pressing "Email it" again does not queue a second copy.

Everything belongs to the carer (``owner_id``): other people in the household or organization never see it.
Children cannot use it (no money for children).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from aira.suite import kit, life
from aira.suite.care import links, service, store
from aira.suite.care.store import APP

STATUSES = ("open", "sent", "paid")
KIND = "care_statement"
SOURCE = "care_statement"
MAX_CLIENTS = 100
MAX_MINUTES = 24 * 60


def require_adult(me: dict[str, Any]) -> None:
    if not life.is_adult(me):
        raise PermissionError("Care work is for adults: it is about being paid.")


def visit_cents(minutes: int, rate_cents: int) -> int:
    """The pay for a visit, rounded to the nearest cent."""
    return int(round(minutes * rate_cents / 60))


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------


def _clean_client(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    rate = merged.get("rate_cents")
    return {
        "name": kit.text(
            merged.get("name"), "the client's name", max_len=80, required=True
        ),
        "email": kit.email(merged.get("email"), "the client's email"),
        "phone": kit.phone(merged.get("phone"), "the client's phone"),
        "rate_cents": kit.cents_value(rate, "the rate an hour", default=0),
        "notes": kit.text(merged.get("notes"), "notes", max_len=500, multiline=True),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }


def get_client(
    connection: sqlite3.Connection, owner_id: str, client_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM cr_clients WHERE id = ? AND owner_id = ?",
        (client_id, owner_id),
    )
    if not row:
        raise LookupError("That client was not found.")
    row["active"] = bool(row["active"])
    return row


def clients(connection: sqlite3.Connection, owner_id: str) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT c.*,
            (SELECT COALESCE(SUM(v.minutes), 0) FROM cr_visits v WHERE v.client_id = c.id AND v.statement_id IS NULL)
                AS unbilled_minutes,
            (SELECT COALESCE(SUM(ROUND(v.minutes * v.rate_cents / 60.0)), 0) FROM cr_visits v
                WHERE v.client_id = c.id AND v.statement_id IS NULL) AS unbilled_cents
        FROM cr_clients c WHERE c.owner_id = ? ORDER BY c.active DESC, c.name COLLATE NOCASE""",
        (owner_id,),
    )
    for row in rows:
        row["active"] = bool(row["active"])
        row["unbilled_cents"] = int(row["unbilled_cents"] or 0)
    return rows


def save_client(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    client_id: str = "",
) -> dict[str, Any]:
    require_adult(me)
    stamp = kit.now()
    if client_id:
        values = _clean_client(raw, get_client(connection, me["id"], client_id))
        kit.update_row(
            connection, "cr_clients", client_id, {**values, "updated_at": stamp}
        )
    else:
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM cr_clients WHERE owner_id = ?",
                (me["id"],),
            )
            >= MAX_CLIENTS
        ):
            raise ValueError(f"You can keep at most {MAX_CLIENTS} clients.")
        values = _clean_client(raw)
        client_id = kit.new_id()
        connection.execute(
            """INSERT INTO cr_clients (id, owner_id, name, email, phone, rate_cents, notes, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                client_id,
                me["id"],
                values["name"],
                values["email"],
                values["phone"],
                values["rate_cents"],
                values["notes"],
                values["active"],
                stamp,
                stamp,
            ),
        )
        kit.log(
            connection,
            APP,
            "client",
            f"New care client: {values['name']}",
            actor=me["name"],
        )
    connection.commit()
    return get_client(connection, me["id"], client_id)


def delete_client(
    connection: sqlite3.Connection, me: dict[str, Any], client_id: str
) -> str:
    """A client with visits or statements is kept for the record (made a past client); one never visited is
    deleted. Deleting a past client removes it for good with its visits and statements (money already marked paid
    stays in the revenue ledger), unless a statement still waits to be paid."""
    client = get_client(connection, me["id"], client_id)
    if not client["active"]:
        if kit.count(
            connection,
            "SELECT COUNT(*) FROM cr_statements WHERE client_id = ? AND status != 'paid'",
            (client_id,),
        ):
            raise ValueError(
                f"{client['name']} has a statement waiting to be paid. Mark it paid or delete it first."
            )
        for row in kit.fetch_all(
            connection, "SELECT id FROM cr_statements WHERE client_id = ?", (client_id,)
        ):
            links.revoke(connection, KIND, row["id"])
        connection.execute(
            "DELETE FROM cr_statements WHERE client_id = ?", (client_id,)
        )
        connection.execute("DELETE FROM cr_visits WHERE client_id = ?", (client_id,))
        connection.execute("DELETE FROM cr_clients WHERE id = ?", (client_id,))
        kit.log(
            connection,
            APP,
            "client",
            f"Deleted past client {client['name']}",
            actor=me["name"],
        )
        connection.commit()
        return "deleted"
    if kit.count(
        connection, "SELECT COUNT(*) FROM cr_visits WHERE client_id = ?", (client_id,)
    ):
        kit.update_row(
            connection, "cr_clients", client_id, {"active": 0, "updated_at": kit.now()}
        )
        outcome = "archived"
    else:
        connection.execute("DELETE FROM cr_clients WHERE id = ?", (client_id,))
        outcome = "deleted"
    connection.commit()
    return outcome


# ---------------------------------------------------------------------------
# visits
# ---------------------------------------------------------------------------


def _visit_row(row: dict[str, Any]) -> dict[str, Any]:
    row["tasks"] = kit.loads(row.get("tasks"), []) or []
    row["amount_cents"] = visit_cents(int(row["minutes"]), int(row["rate_cents"]))
    return row


def get_visit(
    connection: sqlite3.Connection, owner_id: str, visit_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        """SELECT v.*, c.name AS client FROM cr_visits v JOIN cr_clients c ON c.id = v.client_id
        WHERE v.id = ? AND v.owner_id = ?""",
        (visit_id, owner_id),
    )
    if not row:
        raise LookupError("That visit was not found.")
    return _visit_row(row)


def visits(
    connection: sqlite3.Connection,
    owner_id: str,
    *,
    client_id: str = "",
    unbilled: bool = False,
    since: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses, params = ["v.owner_id = ?"], [owner_id]
    if client_id:
        clauses.append("v.client_id = ?")
        params.append(client_id)
    if unbilled:
        clauses.append("v.statement_id IS NULL")
    if since:
        clauses.append("v.day >= ?")
        params.append(since)
    rows = kit.fetch_all(
        connection,
        f"""SELECT v.*, c.name AS client FROM cr_visits v JOIN cr_clients c ON c.id = v.client_id
        WHERE {" AND ".join(clauses)} ORDER BY v.day DESC, v.start DESC LIMIT ?""",  # noqa: S608
        (*params, max(1, min(limit, 2000))),
    )
    return [_visit_row(row) for row in rows]


def _clean_visit(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    client = get_client(connection, me["id"], str(merged.get("client_id") or ""))
    day = (
        kit.day(merged.get("day"), "the day")
        or service.local_today(connection).isoformat()
    )
    if day > service.local_today(connection).isoformat():
        raise ValueError(
            "A visit cannot be in the future. Record it after it happened."
        )
    start = str(merged.get("start") or "").strip()
    minutes = kit.integer(
        merged.get("minutes"),
        "how long the visit was (in minutes)",
        minimum=5,
        maximum=MAX_MINUTES,
    )
    rate = merged.get("rate_cents")
    return {
        "client_id": client["id"],
        "day": day,
        "start": store.clock(start, "the start time") if start else "",
        "minutes": minutes,
        "rate_cents": client["rate_cents"]
        if rate in (None, "") and not current
        else kit.cents_value(rate, "the rate an hour", default=client["rate_cents"]),
        "tasks": kit.tags(merged.get("tasks"), limit=15),
        "notes": kit.text(merged.get("notes"), "notes", max_len=1000, multiline=True),
    }


def save_visit(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    visit_id: str = "",
) -> dict[str, Any]:
    require_adult(me)
    stamp = kit.now()
    if visit_id:
        current = get_visit(connection, me["id"], visit_id)
        if current["statement_id"]:
            raise ValueError(
                "This visit is on a statement. Delete the statement first to change it."
            )
        values = _clean_visit(connection, me, raw, current)
        kit.update_row(
            connection,
            "cr_visits",
            visit_id,
            {**values, "tasks": kit.dumps(values["tasks"]), "updated_at": stamp},
        )
    else:
        values = _clean_visit(connection, me, raw)
        visit_id = kit.new_id()
        connection.execute(
            """INSERT INTO cr_visits (id, owner_id, client_id, day, start, minutes, rate_cents, tasks, notes, statement_id,
            created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                visit_id,
                me["id"],
                values["client_id"],
                values["day"],
                values["start"],
                values["minutes"],
                values["rate_cents"],
                kit.dumps(values["tasks"]),
                values["notes"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    return get_visit(connection, me["id"], visit_id)


def delete_visit(
    connection: sqlite3.Connection, me: dict[str, Any], visit_id: str
) -> None:
    visit = get_visit(connection, me["id"], visit_id)
    if visit["statement_id"]:
        raise ValueError("This visit is on a statement. Delete the statement first.")
    connection.execute("DELETE FROM cr_visits WHERE id = ?", (visit_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# statements
# ---------------------------------------------------------------------------


def get_statement(
    connection: sqlite3.Connection, owner_id: str, statement_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        """SELECT s.*, c.name AS client, c.email AS client_email FROM cr_statements s
        JOIN cr_clients c ON c.id = s.client_id WHERE s.id = ? AND s.owner_id = ?""",
        (statement_id, owner_id),
    )
    if not row:
        raise LookupError("That statement was not found.")
    row["visits"] = [
        _visit_row(visit)
        for visit in kit.fetch_all(
            connection,
            "SELECT * FROM cr_visits WHERE statement_id = ? ORDER BY day, start",
            (statement_id,),
        )
    ]
    _with_email(connection, [row])
    row["link"] = links.current(connection, KIND, statement_id)
    return row


def statements(
    connection: sqlite3.Connection, owner_id: str, *, status: str = ""
) -> list[dict[str, Any]]:
    params: list[Any] = [owner_id]
    where = ""
    if status:
        kit.choice(status, "the status", STATUSES)
        where = "AND s.status = ?"
        params.append(status)
    rows = kit.fetch_all(
        connection,
        f"""SELECT s.*, c.name AS client, c.email AS client_email FROM cr_statements s
        JOIN cr_clients c ON c.id = s.client_id
        WHERE s.owner_id = ? {where} ORDER BY s.created_at DESC LIMIT 200""",  # noqa: S608
        params,
    )
    _with_email(connection, rows)
    for row in rows:
        row["link"] = links.current(connection, KIND, row["id"])
    return rows


def _with_email(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    """Add each statement's latest email (``email``: its outbox status and when), and mark a statement sent once
    its email has really gone out."""
    if not rows:
        return
    kit.ensure_core(connection)
    marks = ",".join("?" for _ in rows)
    latest: dict[str, dict[str, Any]] = {}
    for message in kit.fetch_all(
        connection,
        f"""SELECT ref_id, status, created_at, sent_at FROM suite_outbox WHERE app = ? AND kind = ?
        AND ref_type = 'statement' AND ref_id IN ({marks}) ORDER BY created_at""",  # noqa: S608
        (APP, KIND, *[row["id"] for row in rows]),
    ):
        latest[message["ref_id"]] = message
    changed = False
    for row in rows:
        message = latest.get(row["id"])
        row["email"] = (
            {
                "status": message["status"],
                "queued_at": message["created_at"],
                "sent_at": message["sent_at"],
            }
            if message
            else None
        )
        if message and message["status"] == "sent" and row["status"] == "open":
            stamp = message["sent_at"] or kit.now()
            connection.execute(
                "UPDATE cr_statements SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ?",
                (stamp, kit.now(), row["id"]),
            )
            row["status"], row["sent_at"] = "sent", stamp
            changed = True
    if changed:
        connection.commit()


def _next_number(connection: sqlite3.Connection, owner_id: str) -> str:
    """The next number after the highest one used (a deleted statement's number is never given again)."""
    highest = 0
    for row in kit.fetch_all(
        connection, "SELECT number FROM cr_statements WHERE owner_id = ?", (owner_id,)
    ):
        digits = str(row["number"]).rsplit("-", 1)[-1]
        if digits.isdigit():
            highest = max(highest, int(digits))
    used = kit.get_setting(connection, APP, f"statement_seq:{owner_id}", 0) or 0
    number = max(highest, int(used)) + 1
    kit.set_setting(connection, APP, f"statement_seq:{owner_id}", number)
    return f"CARE-{number:04d}"


def create_statement(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Bill a client for every visit not yet billed, up to ``to`` (default today)."""
    require_adult(me)
    client = get_client(connection, me["id"], str(raw.get("client_id") or ""))
    until = (
        kit.day(raw.get("to"), "the last day")
        or service.local_today(connection).isoformat()
    )
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM cr_visits WHERE owner_id = ? AND client_id = ? AND statement_id IS NULL AND day <= ? ORDER BY day",
        (me["id"], client["id"], until),
    )
    if not rows:
        raise ValueError(
            f"There are no visits to bill for {client['name']}. Record a visit first."
        )
    minutes = sum(int(row["minutes"]) for row in rows)
    total = sum(
        visit_cents(int(row["minutes"]), int(row["rate_cents"])) for row in rows
    )
    statement_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO cr_statements (id, owner_id, client_id, number, period_from, period_to, minutes, total_cents, currency,
        status, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
        (
            statement_id,
            me["id"],
            client["id"],
            _next_number(connection, me["id"]),
            rows[0]["day"],
            rows[-1]["day"],
            minutes,
            total,
            kit.business(connection)["currency"],
            kit.text(raw.get("note"), "a note", max_len=500, multiline=True),
            stamp,
            stamp,
        ),
    )
    connection.executemany(
        "UPDATE cr_visits SET statement_id = ?, updated_at = ? WHERE id = ?",
        [(statement_id, stamp, row["id"]) for row in rows],
    )
    kit.log(
        connection,
        APP,
        "statement",
        f"Statement for {client['name']}: {kit.fmt_money(total, kit.business(connection)['currency'])}",
        actor=me["name"],
        ref_type="statement",
        ref_id=statement_id,
        amount_cents=total,
    )
    connection.commit()
    return get_statement(connection, me["id"], statement_id)


def delete_statement(
    connection: sqlite3.Connection, me: dict[str, Any], statement_id: str
) -> None:
    """Undo a statement that is not paid: its visits can be billed again."""
    statement = get_statement(connection, me["id"], statement_id)
    if statement["status"] == "paid":
        raise ValueError("This statement is paid. Mark it not paid first.")
    connection.execute(
        "UPDATE cr_visits SET statement_id = NULL, updated_at = ? WHERE statement_id = ?",
        (kit.now(), statement_id),
    )
    links.revoke(connection, KIND, statement_id)
    connection.execute("DELETE FROM cr_statements WHERE id = ?", (statement_id,))
    connection.commit()


def link(
    connection: sqlite3.Connection, me: dict[str, Any], statement_id: str
) -> dict[str, Any]:
    statement = get_statement(connection, me["id"], statement_id)
    return links.create(
        connection, KIND, statement_id, title=f"Statement {statement['number']}"
    )


def _waiting_email(
    connection: sqlite3.Connection, statement_id: str
) -> dict[str, Any] | None:
    """The statement's email that has not gone out yet (waiting for approval, or approved and about to go)."""
    kit.ensure_core(connection)
    return kit.fetch_one(
        connection,
        """SELECT * FROM suite_outbox WHERE app = ? AND kind = ? AND ref_type = 'statement' AND ref_id = ?
        AND status IN ('pending', 'approved') ORDER BY created_at DESC""",
        (APP, KIND, statement_id),
    )


def send(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    statement_id: str,
    origin: Any = None,
) -> dict[str, Any]:
    """Email the statement's link to the client (through the outbox). It counts as sent once the email has gone
    out; while one waits, pressing again queues nothing more (``already`` is true)."""
    statement = get_statement(connection, me["id"], statement_id)
    if not statement["client_email"]:
        raise ValueError(
            f"Add {statement['client']}'s email address first, or copy the link and send it yourself."
        )
    waiting = _waiting_email(connection, statement_id)
    if waiting:
        return {"statement": statement, "message": waiting, "already": True}
    made = link(connection, me, statement_id)
    currency = statement["currency"]
    count = len(statement["visits"])
    message = kit.queue_email(
        connection,
        APP,
        KIND,
        to=statement["client_email"],
        subject=f"Statement {statement['number']} from {me['name']}: {kit.fmt_money(statement['total_cents'], currency)}",
        body=(
            f"Hello {statement['client']},\n\n"
            f"Here is the statement for the care visits from {statement['period_from']} to {statement['period_to']}: "
            f"{count} visit{'' if count == 1 else 's'}, {_hours_words(statement['minutes'])}, "
            f"{kit.fmt_money(statement['total_cents'], currency)}.\n\n"
            f"See the visits and how to pay: {kit.public_url(made['path'], origin)}\n\n"
            f"Thank you,\n{me['name']}"
        ),
        ref_type="statement",
        ref_id=statement_id,
    )
    connection.commit()
    return {
        "statement": get_statement(connection, me["id"], statement_id),
        "message": message,
        "already": False,
    }


def _hours_words(minutes: int) -> str:
    """110 -> "1 hour 50 minutes", 120 -> "2 hours", 45 -> "45 minutes"."""
    hours, rest = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'' if hours == 1 else 's'}")
    if rest or not hours:
        parts.append(f"{rest} minute{'' if rest == 1 else 's'}")
    return " ".join(parts)


def set_paid(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    statement_id: str,
    paid: bool,
) -> dict[str, Any]:
    """Mark the money received (recorded once in the revenue ledger) or not received after all."""
    statement = get_statement(connection, me["id"], statement_id)
    stamp = kit.now()
    if paid:
        kit.record_revenue(
            connection,
            APP,
            source_type=SOURCE,
            source_id=statement_id,
            amount_cents=statement["total_cents"],
            label=f"Care visits {statement['number']}",
            customer=statement["client"],
            currency_code=statement["currency"],
        )
        kit.update_row(
            connection,
            "cr_statements",
            statement_id,
            {
                "status": "paid",
                "paid_at": statement["paid_at"] or stamp,
                "updated_at": stamp,
            },
        )
        if statement["status"] != "paid":
            kit.log(
                connection,
                APP,
                "paid",
                f"{statement['client']} paid statement {statement['number']}",
                actor=me["name"],
                ref_type="statement",
                ref_id=statement_id,
                amount_cents=statement["total_cents"],
            )
    else:
        kit.void_revenue(connection, APP, SOURCE, statement_id)
        kit.update_row(
            connection,
            "cr_statements",
            statement_id,
            {
                "status": "sent" if statement["sent_at"] else "open",
                "paid_at": None,
                "updated_at": stamp,
            },
        )
    connection.commit()
    return get_statement(connection, me["id"], statement_id)


# ---------------------------------------------------------------------------
# headline numbers, the public page and reminders
# ---------------------------------------------------------------------------


def _to_check(connection: sqlite3.Connection, owner_id: str) -> list[dict[str, Any]]:
    """Statements a client says they paid that the carer has not marked paid yet."""
    return kit.fetch_all(
        connection,
        """SELECT s.id, s.number, s.total_cents, s.client_says_paid_at, s.client_paid_note, c.name AS client
        FROM cr_statements s
        JOIN cr_clients c ON c.id = s.client_id
        WHERE s.owner_id = ? AND s.status != 'paid' AND s.client_says_paid_at IS NOT NULL""",
        (owner_id,),
    )


def summary(
    connection: sqlite3.Connection, owner_id: str, now: datetime | None = None
) -> dict[str, Any]:
    today = service.local_today(connection, now)
    month = today.replace(day=1).isoformat()
    month_minutes = kit.count(
        connection,
        "SELECT COALESCE(SUM(minutes), 0) FROM cr_visits WHERE owner_id = ? AND day >= ?",
        (owner_id, month),
    )
    paid_month = kit.count(
        connection,
        "SELECT COALESCE(SUM(total_cents), 0) FROM cr_statements WHERE owner_id = ? AND status = 'paid' AND paid_at >= ?",
        (owner_id, month),
    )
    waiting = kit.count(
        connection,
        "SELECT COALESCE(SUM(total_cents), 0) FROM cr_statements WHERE owner_id = ? AND status != 'paid'",
        (owner_id,),
    )
    unbilled = kit.count(
        connection,
        "SELECT COALESCE(SUM(ROUND(minutes * rate_cents / 60.0)), 0) FROM cr_visits WHERE owner_id = ? AND statement_id IS NULL",
        (owner_id,),
    )
    paid_all = kit.count(
        connection,
        "SELECT COALESCE(SUM(total_cents), 0) FROM cr_statements WHERE owner_id = ? AND status = 'paid'",
        (owner_id,),
    )
    to_check = _to_check(connection, owner_id)
    return {
        "currency": kit.business(connection)["currency"],
        "hours_this_month": round(month_minutes / 60, 2),
        "paid_this_month_cents": paid_month,
        "waiting_cents": waiting,
        "unbilled_cents": unbilled,
        "paid_all_time_cents": paid_all,
        "clients": kit.count(
            connection,
            "SELECT COUNT(*) FROM cr_clients WHERE owner_id = ? AND active = 1",
            (owner_id,),
        ),
        "to_check": to_check,
        "payment_ready": bool(
            kit.business(connection).get("payment_link")
            or kit.business(connection).get("payment_instructions")
        ),
    }


def public_page(
    connection: sqlite3.Connection, link_row: dict[str, Any]
) -> dict[str, Any]:
    """What the client sees: the carer's first name, the visits (day, time, length, what was done), the total."""
    row = kit.fetch_one(
        connection,
        """SELECT s.*, c.name AS client FROM cr_statements s JOIN cr_clients c ON c.id = s.client_id
        WHERE s.id = ?""",
        (link_row["ref_id"],),
    )
    if not row:
        raise LookupError("This link is no longer available.")
    carer = life.name_of(connection, row["owner_id"])
    items = kit.fetch_all(
        connection,
        "SELECT day, start, minutes, rate_cents, tasks FROM cr_visits WHERE statement_id = ? ORDER BY day, start",
        (row["id"],),
    )
    return {
        "statement": {
            "number": row["number"],
            "client": row["client"],
            "carer": carer,
            "period_from": row["period_from"],
            "period_to": row["period_to"],
            "minutes": row["minutes"],
            "total_cents": row["total_cents"],
            "currency": row["currency"],
            "status": row["status"],
            "note": row["note"],
            "client_says_paid": bool(row["client_says_paid_at"]),
            "visits": [
                {
                    "day": item["day"],
                    "start": item["start"],
                    "minutes": item["minutes"],
                    "amount_cents": visit_cents(
                        int(item["minutes"]), int(item["rate_cents"])
                    ),
                    "tasks": kit.loads(item["tasks"], []) or [],
                }
                for item in items
            ],
        }
    }


def client_says_paid(
    connection: sqlite3.Connection, link_row: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """The client says they paid: the carer is told and checks before marking it paid (nothing is recorded as money
    received from what a visitor says)."""
    row = kit.fetch_one(
        connection, "SELECT * FROM cr_statements WHERE id = ?", (link_row["ref_id"],)
    )
    if not row:
        raise LookupError("This link is no longer available.")
    if row["status"] == "paid":
        return {"status": "paid"}
    note = kit.text(payload.get("note"), "a note", max_len=200)
    if row["client_says_paid_at"]:
        if note and note != row["client_paid_note"]:  # a reference sent afterwards
            connection.execute(
                "UPDATE cr_statements SET client_paid_note = ?, updated_at = ? WHERE id = ?",
                (note, kit.now(), row["id"]),
            )
    else:
        connection.execute(
            "UPDATE cr_statements SET client_says_paid_at = ?, client_paid_note = ?, updated_at = ? WHERE id = ?",
            (kit.now(), note, kit.now(), row["id"]),
        )
        kit.log(
            connection,
            APP,
            "client_paid",
            f"The client says statement {row['number']} is paid"
            + (f": {note}" if note else ""),
            public=True,
            ref_type="statement",
            ref_id=row["id"],
            amount_cents=row["total_cents"],
        )
    connection.commit()
    return {"status": "told"}


def alerts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in _to_check(connection, me["id"]):
        out.append(
            {
                "id": f"carepaid:{row['id']}",
                "title": "A client says they paid",
                "body": f"{row['client']}: statement {row['number']}."
                + (
                    f' Their note: "{row["client_paid_note"]}".'
                    if row["client_paid_note"]
                    else ""
                )
                + " Check your account, then mark it paid in Health > Care work.",
                "tone": "good",
                "speak": False,
            }
        )
    return out


def visits_csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "day": row["day"],
            "start": row["start"],
            "client": row["client"],
            "minutes": row["minutes"],
            "rate": f"{row['rate_cents'] / 100:.2f}",
            "amount": f"{row['amount_cents'] / 100:.2f}",
            "tasks": ", ".join(row["tasks"]),
            "billed": "yes" if row["statement_id"] else "no",
            "notes": row["notes"],
        }
        for row in rows
    ]
