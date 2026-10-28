"""Life rules: goals and their progress, the timeline, the document vault and the overview.

Every function takes the person (``me``, from :func:`aira.suite.life.current`) and only ever reads or changes
their own rows; the one exception is a timeline event its owner shared with the household, which others may read.
Document numbers and scans are encrypted with the installation's secret store (``seal``) before they are written.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira import apps as app_catalog
from aira import suite
from aira.suite import kit, life, public

GOAL_CATEGORIES = (
    "career",
    "health",
    "money",
    "learning",
    "family",
    "relationships",
    "home",
    "travel",
    "creative",
    "personal",
)
GOAL_STATUSES = ("active", "paused", "completed", "dropped")
# How a goal is measured: a percentage (or its steps), a number with a unit (100 km, 12 books) or money (save or
# earn an amount). Numbers and money are kept in hundredths (cents for money).
MEASURES = ("percent", "number", "money")
SOURCES = (
    "manual",
    "earnings",
)  # earnings: counted from what the account's apps earned (the revenue ledger)
EARNING_ROLES = {
    "owner",
    "admin",
    "coach",
    "auditor",
}  # hosted roles that may see the organization's earnings
EVENT_KINDS = (
    "life",
    "birth",
    "education",
    "career",
    "family",
    "relationship",
    "home",
    "health",
    "travel",
    "achievement",
)
DOCUMENT_KINDS = (
    "passport",
    "id_card",
    "driving_licence",
    "birth_certificate",
    "visa",
    "diploma",
    "insurance",
    "medical",
    "tax",
    "property",
    "vehicle",
    "bank",
    "will",
    "other",
)
MAX_GOALS = 200
MAX_STEPS = 50
MAX_EVENTS = 2000
MAX_DOCUMENTS = 300
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_VAULT_BYTES = 200 * 1024 * 1024  # all of one person's scans together
MAX_HABITS = 30
MAX_CHEERS = 500  # on one goal's page
CHEERS_PER_VISITOR = 5  # a day, on one goal's page
HABIT_BACKFILL_DAYS = (
    30  # a habit can be ticked for a day this far back (forgot yesterday)
)
SOON_DAYS = 90  # a document "expires soon" this many days ahead
REMIND_DAYS = 30  # the pulse reminds this many days ahead (and Today keeps an expired one this long after)
MAX_PLEDGES = 500  # on one goal's page
PLEDGES_PER_VISITOR = 5  # a day, on one goal's page
MAX_PLEDGE_CENTS = 10_000_000  # one supporter's pledge (100,000 in the goal's currency)
PLEDGE_STATUSES = ("promised", "received", "cancelled")
# A day's mood in the journal, from 1 (a hard day) to 5 (a great day); 0 means not said.
MOODS = {5: "Great", 4: "Good", 3: "Okay", 2: "Low", 1: "Hard"}
MAX_JOURNAL_DAYS = 20000  # about 55 years of daily entries
MAX_SUGGESTED_STEPS = 8

# What a scan may be, by its first bytes (the name a browser sends is not trusted).
FILE_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "text/plain": ".txt",
}


class Seal(Protocol):
    """Encrypts text for storage (the installation's secret store)."""

    def encrypt(self, plaintext: str) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> str: ...


# ---------------------------------------------------------------------------
# amounts
# ---------------------------------------------------------------------------

_SYMBOLS: list[str] = sorted(
    {str(symbol).strip() for symbol in kit.CURRENCIES.values()},
    key=lambda symbol: len(symbol),
    reverse=True,
)
_NUMBER = re.compile(r"[+-]?(?:\d{1,15}(?:\.\d{0,6})?|\.\d{1,6})")


def money_in(
    value: Any,
    name: str = "an amount",
    *,
    currency: str = "",
    default: int | None = None,
    minimum: int = 0,
) -> int:
    """An amount typed by a person (or a visitor on a public page) -> cents, stricter than ``kit.cents``:
    a plain number like 12.50 (commas allowed) with at most one currency mark, and when ``currency`` is given
    the mark must be that currency's. "Infinity", "NaN" and "1e999" are refused with a clear message."""
    if value is None or value == "":
        return kit.cents(value, name, default=default, minimum=minimum)
    label = f"{name[:1].upper()}{name[1:]}"
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number like 12.50.")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be a number like 12.50.")
        value = f"{value:.6f}"
    text = str(value).strip().replace(",", "")
    upper = text.upper()
    mark = ""
    for code in kit.CURRENCIES:  # "USD 50", "50 EUR"
        if upper.startswith(code) or upper.endswith(code):
            mark = code
            text = (
                text[len(code) :] if upper.startswith(code) else text[: -len(code)]
            ).strip()
            break
    else:
        for symbol in _SYMBOLS:
            if text.startswith(symbol) or text.endswith(symbol):
                mark = symbol
                text = (
                    text[len(symbol) :]
                    if text.startswith(symbol)
                    else text[: -len(symbol)]
                ).strip()
                break
    if not _NUMBER.fullmatch(text):
        if re.fullmatch(r"[+-]?\d{16,}(?:\.\d*)?", text):
            raise ValueError(f"{label} is too large.")
        raise ValueError(f"{label} must be a number like 12.50.")
    if mark and currency:
        own = str(kit.CURRENCIES.get(currency, currency)).strip()
        # "$" is fine for any dollar (CA$, A$, S$); anything else must be this currency's own mark.
        if mark not in (currency, own) and not (mark == "$" and own.endswith("$")):
            raise ValueError(
                f"{label} is in {currency} here. Enter it without {mark}, for example 12.50."
            )
    return kit.cents(text, name, minimum=minimum)


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def today(connection: sqlite3.Connection) -> date:
    """Today in the account's time zone."""
    zone = kit.business(connection).get("timezone") or "UTC"
    try:
        return datetime.now(ZoneInfo(zone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo("UTC")).date()


def days_until(value: str, now: date) -> int | None:
    if not value:
        return None
    try:
        return (date.fromisoformat(value) - now).days
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# goals
# ---------------------------------------------------------------------------


def _goal_row(
    connection: sqlite3.Connection, me: dict[str, Any], goal_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM jr_goals WHERE id = ? AND owner_id = ?",
        (goal_id, me["id"]),
    )
    if not found:
        raise LookupError("That goal was not found.")
    return found


def _steps(connection: sqlite3.Connection, goal_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "done": bool(row["done"]),
            "done_at": row["done_at"],
        }
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM jr_steps WHERE goal_id = ? ORDER BY position, created_at",
            (goal_id,),
        )
    ]


def goal_state(goal: dict[str, Any], now: date) -> str:
    """How a goal is doing, in words a person reads: completed, on track, behind, overdue, paused or dropped."""
    if goal["status"] in {"completed", "paused", "dropped"}:
        return goal["status"]
    left = days_until(goal.get("target_date") or "", now)
    if left is not None and left < 0:
        return "overdue"
    begun = goal.get("start_date") or goal.get("created_at")
    if left is not None and begun:
        try:
            start = datetime.fromisoformat(str(begun).replace("Z", "+00:00")).date()
        except ValueError:
            start = now
        total = (date.fromisoformat(goal["target_date"]) - start).days
        if total > 0:
            expected = round(100 * (now - start).days / total)
            if goal["progress"] + 15 < expected:
                return "behind"
    return "on_track"


def measure_of(goal: dict[str, Any]) -> str:
    return goal.get("measure") or "percent"


def amount_progress(current: int, target: int) -> int:
    """Progress of a goal measured in a number or money: 100 only once the target is reached."""
    if target <= 0:
        return 0
    if current >= target:
        return 100
    return max(0, min(99, (100 * current) // target))


def may_count_earnings(me: dict[str, Any]) -> bool:
    """Whether this person may see what the account's apps earned (an earning goal counts it).

    Hosted, the earnings are the organization's: owners, admins, coaches and auditors see them (as in the
    Business hub). Locally they are the household's account: adults see them, children and teens do not."""
    member = suite.actor()
    if member is not None:
        return str(member.get("role") or "") in EARNING_ROLES
    return life.is_adult(me)


def _app_name(app_id: str) -> str:
    owner = app_catalog.owner_of(app_id)
    return owner.name if owner else app_id.title()


def earnings(
    connection: sqlite3.Connection, currency: str, start: str, end: str = ""
) -> dict[str, Any]:
    """What the account's apps earned in ``currency`` from ``start`` (a date) to ``end`` (a date, inclusive)."""
    kit.ensure_core(connection)
    sql = "SELECT app, SUM(amount_cents) AS total, COUNT(*) AS n FROM suite_revenue WHERE voided_at IS NULL AND currency = ?"
    params: list[Any] = [currency]
    if start:
        sql += " AND at >= ?"
        params.append(start)
    if end:
        sql += " AND at < ?"
        params.append((date.fromisoformat(end) + timedelta(days=1)).isoformat())
    rows = kit.fetch_all(connection, sql + " GROUP BY app ORDER BY total DESC", params)
    by_app = [
        {
            "app": row["app"],
            "name": _app_name(row["app"]),
            "cents": int(row["total"] or 0),
        }
        for row in rows
        if int(row["total"] or 0)
    ]
    return {
        "currency": currency,
        "since": start,
        "until": end,
        "cents": sum(item["cents"] for item in by_app),
        "payments": sum(int(row["n"] or 0) for row in rows),
        "by_app": by_app,
    }


def _sync_earnings(connection: sqlite3.Connection, me: dict[str, Any]) -> None:
    """Bring this person's active earning goals up to date with the revenue ledger (and finish reached ones)."""
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM jr_goals WHERE owner_id = ? AND source = 'earnings' AND status IN ('active', 'paused')",
        (me["id"],),
    )
    if not rows or not may_count_earnings(me):
        return
    changed = False
    for goal in rows:
        counted = earnings(
            connection, goal["currency"], goal["start_date"], goal["target_date"]
        )["cents"]
        if counted == int(goal["current_amount"]):
            continue
        progress = amount_progress(counted, int(goal["target_amount"]))
        kit.update_row(
            connection,
            "jr_goals",
            goal["id"],
            {"current_amount": counted, "progress": progress, "updated_at": kit.now()},
        )
        if progress >= 100 and goal["status"] == "active":
            kit.update_row(connection, "jr_goals", goal["id"], {"status": "completed"})
            _complete(connection, me, goal["id"])
        changed = True
    if changed:
        connection.commit()


def _live_links(connection: sqlite3.Connection) -> set[str]:
    kit.ensure_core(connection)
    return {
        row["ref_id"]
        for row in kit.fetch_all(
            connection,
            "SELECT ref_id FROM suite_public_links WHERE app = 'journey' AND kind = 'goal_page' AND revoked_at IS NULL",
        )
    }


def goal_view(
    connection: sqlite3.Connection,
    goal: dict[str, Any],
    now: date,
    *,
    shared: bool | None = None,
) -> dict[str, Any]:
    steps = _steps(connection, goal["id"])
    checkins = kit.fetch_all(
        connection,
        "SELECT progress, amount, note, at FROM jr_checkins WHERE goal_id = ? ORDER BY at DESC LIMIT 20",
        (goal["id"],),
    )
    measure = measure_of(goal)
    view = {
        "id": goal["id"],
        "title": goal["title"],
        "description": goal["description"],
        "category": goal["category"],
        "status": goal["status"],
        "progress": int(goal["progress"]),
        "target_date": goal["target_date"],
        "days_left": days_until(goal["target_date"], now),
        "state": goal_state(goal, now),
        "completed_at": goal["completed_at"],
        "created_at": goal["created_at"],
        "updated_at": goal["updated_at"],
        "measure": measure,
        "unit": goal.get("unit") or "",
        "currency": goal.get("currency") or "",
        "target_amount": int(goal.get("target_amount") or 0),
        "current_amount": int(goal.get("current_amount") or 0),
        "source": goal.get("source") or "manual",
        "start_date": goal.get("start_date") or "",
        "steps": steps,
        "steps_done": sum(1 for step in steps if step["done"]),
        "checkins": checkins,
        "shared": bool(shared)
        if shared is not None
        else goal["id"] in _live_links(connection),
        "cheers_unread": kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_cheers WHERE goal_id = ? AND read_at IS NULL",
            (goal["id"],),
        ),
        "support": bool(goal.get("support")),
        "pay_link": goal.get("pay_link") or "",
        "pay_note": goal.get("pay_note") or "",
        "pledges": pledge_totals(connection, goal["id"]),
    }
    return view


def pledge_totals(connection: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
    """What supporters promised and what reached the owner, for one goal (cancelled pledges do not count).

    Amounts are only added up in one currency (the one most supporters used); ``other`` counts the
    pledges made in another currency (the account's currency changed), which are not in the sums."""
    rows = kit.fetch_all(
        connection,
        "SELECT status, currency, COUNT(*) AS n, SUM(amount) AS total, SUM(read_at IS NULL) AS unread "
        "FROM jr_pledges WHERE goal_id = ? AND status != 'cancelled' GROUP BY status, currency",
        (goal_id,),
    )
    by_currency: dict[str, int] = {}
    for row in rows:
        code = str(row["currency"])
        by_currency[code] = by_currency.get(code, 0) + int(row["n"] or 0)
    currency = max(sorted(by_currency), key=lambda code: by_currency[code], default="")
    counts = {"supporters": 0, "promised": 0, "received": 0, "unread": 0, "other": 0}
    for row in rows:
        counts["supporters"] += int(row["n"] or 0)
        counts["unread"] += int(row["unread"] or 0)
        if str(row["currency"]) == currency:
            counts[str(row["status"])] += int(row["total"] or 0)
        else:
            counts["other"] += int(row["n"] or 0)
    return {**counts, "currency": currency}


def list_goals(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    status: str = "",
    category: str = "",
    search: str = "",
) -> list[dict[str, Any]]:
    _sync_earnings(connection, me)
    sql = "SELECT * FROM jr_goals WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    if status:
        sql += " AND status = ?"
        params.append(kit.choice(status, "the status", GOAL_STATUSES))
    if category:
        sql += " AND category = ?"
        params.append(kit.choice(category, "the category", GOAL_CATEGORIES))
    if search:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        like = f"%{kit.text(search, 'the search', max_len=80)}%"
        params += [like, like]
    sql += " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'completed' THEN 2 ELSE 3 END, CASE WHEN target_date = '' THEN 1 ELSE 0 END, target_date, created_at DESC"
    now = today(connection)
    links = _live_links(connection)
    return [
        goal_view(connection, row, now, shared=row["id"] in links)
        for row in kit.fetch_all(connection, sql, params)
    ]


def get_goal(
    connection: sqlite3.Connection, me: dict[str, Any], goal_id: str
) -> dict[str, Any]:
    _sync_earnings(connection, me)
    return goal_view(connection, _goal_row(connection, me, goal_id), today(connection))


def goal_detail(
    connection: sqlite3.Connection, me: dict[str, Any], goal_id: str
) -> dict[str, Any]:
    """A goal with what only its owner's detail view shows: its public link, the cheers friends left (now read)
    and, for an earning goal, what each app earned towards it."""
    goal = get_goal(connection, me, goal_id)
    goal["can_share"] = may_share(me)
    goal["link"] = public.link_for(connection, "journey", "goal_page", goal_id)
    goal["cheers"] = kit.fetch_all(
        connection,
        "SELECT id, name, message, at, read_at FROM jr_cheers WHERE goal_id = ? ORDER BY at DESC LIMIT 50",
        (goal_id,),
    )
    if goal["cheers_unread"]:
        connection.execute(
            "UPDATE jr_cheers SET read_at = ? WHERE goal_id = ? AND read_at IS NULL",
            (kit.now(), goal_id),
        )
        connection.commit()
    if goal["source"] == "earnings" and may_count_earnings(me):
        goal["earnings"] = earnings(
            connection, goal["currency"], goal["start_date"], goal["target_date"]
        )
    goal["can_support"] = may_take_support(me, goal)
    goal["pledge_list"] = kit.fetch_all(
        connection,
        "SELECT id, name, message, amount, currency, status, at, received_at FROM jr_pledges WHERE goal_id = ? "
        "ORDER BY CASE status WHEN 'promised' THEN 0 WHEN 'received' THEN 1 ELSE 2 END, at DESC LIMIT 200",
        (goal_id,),
    )
    if goal["pledges"]["unread"]:
        connection.execute(
            "UPDATE jr_pledges SET read_at = ? WHERE goal_id = ? AND read_at IS NULL",
            (kit.now(), goal_id),
        )
        connection.commit()
    return goal


def _clean_goal(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    values: dict[str, Any] = {
        "title": kit.text(merged.get("title"), "a goal", max_len=120, required=True),
        "description": kit.text(
            merged.get("description"), "the description", max_len=2000, multiline=True
        ),
        "category": kit.choice(
            merged.get("category"), "the category", GOAL_CATEGORIES, default="personal"
        ),
        "target_date": kit.day(merged.get("target_date"), "the target date"),
    }
    measure = kit.choice(
        merged.get("measure") or "percent", "how the goal is measured", MEASURES
    )
    source = kit.choice(
        merged.get("source") or "manual", "where the amount comes from", SOURCES
    )
    if source == "earnings" and measure != "money":
        raise ValueError("An earning goal is measured in money.")
    if (
        source == "earnings"
        and (current is None or current.get("source") != "earnings")
        and not may_count_earnings(me)
    ):
        raise PermissionError(
            "Only adults who can see the account's earnings can make an earning goal."
        )
    if measure == "percent":
        return {
            **values,
            "measure": "",
            "unit": "",
            "currency": "",
            "target_amount": 0,
            "source": "manual",
        }
    if "target" in raw:
        target = money_in(raw.get("target"), "the target", minimum=1)
    elif current is not None and int(current.get("target_amount") or 0) > 0:
        target = int(current["target_amount"])
    else:
        raise ValueError(
            "Enter the target, for example 100 (km) or 50000 (money to save)."
        )
    if target < 1:
        raise ValueError("The target must be more than zero.")
    values.update(measure=measure, target_amount=target, source=source)
    if measure == "money":
        values["unit"] = ""
        values["currency"] = kit.currency(
            merged.get("currency"), default=kit.business(connection)["currency"]
        )
    else:
        values["unit"] = kit.text(merged.get("unit"), "the unit", max_len=20)
        values["currency"] = ""
    if source == "earnings":
        values["start_date"] = kit.day(
            merged.get("start_date") or today(connection).isoformat(),
            "the date to count from",
        )
        if values["target_date"] and values["target_date"] < values["start_date"]:
            raise ValueError("The target date is before the date to count from.")
    return values


def create_goal(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    values = _clean_goal(connection, me, raw, None)
    if (
        kit.count(
            connection, "SELECT COUNT(*) FROM jr_goals WHERE owner_id = ?", (me["id"],)
        )
        >= MAX_GOALS
    ):
        raise ValueError(f"You can keep {MAX_GOALS} goals. Remove old ones first.")
    current_amount = 0
    if values["measure"] and values["source"] == "manual":
        current_amount = money_in(
            raw.get("current"),
            "what you have so far",
            currency=values["currency"],
            default=0,
        )
    progress = (
        amount_progress(current_amount, values["target_amount"])
        if values["measure"]
        else kit.integer(
            raw.get("progress"), "the progress", default=0, minimum=0, maximum=100
        )
    )
    goal_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO jr_goals (id, owner_id, title, description, category, status, progress, target_date,
        measure, unit, target_amount, current_amount, currency, source, start_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            goal_id,
            me["id"],
            values["title"],
            values["description"],
            values["category"],
            progress,
            values["target_date"],
            values["measure"],
            values["unit"],
            values["target_amount"],
            current_amount,
            values["currency"],
            values["source"],
            values.get("start_date", ""),
            stamp,
            stamp,
        ),
    )
    steps = raw.get("steps")
    if isinstance(steps, list) and not values["measure"]:
        for title in steps[:MAX_STEPS]:
            if str(title or "").strip():
                _insert_step(connection, goal_id, title)
        _recount(connection, goal_id)
    connection.commit()
    return get_goal(connection, me, goal_id)


def update_goal(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    current = _goal_row(connection, me, goal_id)
    values = _clean_goal(connection, me, raw, current)
    if values["currency"] and values["currency"] != current.get("currency"):
        # Pledges keep the currency they were made in: a money goal in another currency could not count them.
        other = kit.fetch_one(
            connection,
            "SELECT currency FROM jr_pledges WHERE goal_id = ? AND currency != ? AND status != 'cancelled' LIMIT 1",
            (goal_id, values["currency"]),
        )
        if other:
            raise ValueError(
                f"Supporters backed this goal in {other['currency']}. Keep {other['currency']}, "
                "or cancel or remove those pledges first."
            )
    if values["measure"] and _steps(connection, goal_id):
        raise ValueError(
            "This goal's progress follows its steps. Remove the steps to measure it in a number or money."
        )
    changes: dict[str, Any] = {**values, "updated_at": kit.now()}
    if values["measure"]:
        changes["progress"] = amount_progress(
            int(current.get("current_amount") or 0), values["target_amount"]
        )
    elif current.get("measure"):
        changes["current_amount"] = (
            0  # back to a percentage: the progress stays as it was
        )
    if "status" in raw:
        status = kit.choice(raw.get("status"), "the status", GOAL_STATUSES)
        changes["status"] = status
        if status == "completed" and current["status"] != "completed":
            changes["progress"] = 100
            # Marking a goal with steps done ticks its steps, so the steps and the progress agree.
            connection.execute(
                "UPDATE jr_steps SET done = 1, done_at = ? WHERE goal_id = ? AND done = 0",
                (kit.now(), goal_id),
            )
        elif status != "completed" and current["status"] == "completed":
            changes["completed_at"] = None
            if int(current["progress"]) >= 100 and not values["measure"]:
                changes["progress"] = (
                    90 if not _steps(connection, goal_id) else current["progress"]
                )
    elif current["status"] == "active" and changes.get("progress", 0) >= 100:
        changes["status"] = "completed"  # a lower target the amount already reaches
    kit.update_row(connection, "jr_goals", goal_id, changes)
    if changes.get("status") == "completed" and current["status"] != "completed":
        _complete(connection, me, goal_id)
    connection.commit()
    return get_goal(connection, me, goal_id)


def delete_goal(
    connection: sqlite3.Connection, me: dict[str, Any], goal_id: str
) -> None:
    _goal_row(connection, me, goal_id)
    connection.execute("DELETE FROM jr_steps WHERE goal_id = ?", (goal_id,))
    connection.execute("DELETE FROM jr_checkins WHERE goal_id = ?", (goal_id,))
    connection.execute("DELETE FROM jr_cheers WHERE goal_id = ?", (goal_id,))
    connection.execute("DELETE FROM jr_pledges WHERE goal_id = ?", (goal_id,))
    connection.execute(
        "UPDATE jr_habits SET goal_id = '', amount = 0 WHERE goal_id = ?", (goal_id,)
    )
    connection.execute("DELETE FROM jr_goals WHERE id = ?", (goal_id,))
    connection.commit()
    public.revoke(connection, "journey", "goal_page", goal_id)


def _set_progress(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    """Apply new progress (and amount) to a goal, finishing or reopening it as the progress says."""
    progress = int(changes["progress"])
    if progress >= 100 and goal["status"] != "completed":
        changes["status"] = "completed"
    elif progress < 100 and goal["status"] == "completed":
        changes["status"] = "active"
        changes["completed_at"] = None
    kit.update_row(connection, "jr_goals", goal["id"], changes)
    if changes.get("status") == "completed":
        _complete(connection, me, goal["id"])


def check_in(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Record how a goal is going, with a note; reaching 100% completes the goal.

    * A goal measured in percent takes ``progress`` (0 to 100).
    * A goal with steps follows its steps: a check-in is a note (a different ``progress`` is refused).
    * A goal measured in a number or money takes ``amount`` (added, may be negative), ``value`` (the new total)
      or ``progress`` (a percentage of the target). An earning goal counts the account's earnings by itself.
    """
    goal = _goal_row(connection, me, goal_id)
    if goal["status"] == "dropped":
        raise ValueError("This goal was dropped. Make it active again first.")
    note = kit.text(raw.get("note"), "the note", max_len=500, multiline=True)
    measure = measure_of(goal)
    stamp = kit.now()
    changes: dict[str, Any] = {"updated_at": stamp}
    amount: int | None = None
    if measure == "percent" and _steps(connection, goal_id):
        progress = int(goal["progress"])
        given = raw.get("progress")
        if (
            given not in (None, "")
            and kit.integer(given, "the progress", minimum=0, maximum=100) != progress
        ):
            raise ValueError(
                f'"{goal["title"]}" follows its steps ({progress}% done). Tick a step instead.'
            )
        if not note:
            raise ValueError("Write a note about how it is going.")
    elif measure == "percent":
        progress = kit.integer(
            raw.get("progress"), "the progress", minimum=0, maximum=100
        )
    elif (goal.get("source") or "manual") == "earnings":
        if any(
            raw.get(key) not in (None, "") for key in ("amount", "value", "progress")
        ):
            raise ValueError(
                "An earning goal counts what your apps earn by itself. Add a note instead."
            )
        if not note:
            raise ValueError("Write a note about how it is going.")
        progress = int(goal["progress"])
    else:
        target = int(goal["target_amount"])
        before = int(goal["current_amount"])
        if raw.get("amount") not in (None, ""):
            total = before + money_in(
                raw.get("amount"),
                "the amount",
                currency=goal.get("currency") or "",
                minimum=-10_000_000_00,
            )
        elif raw.get("value") not in (None, ""):
            total = money_in(
                raw.get("value"),
                "the total so far",
                currency=goal.get("currency") or "",
            )
        elif raw.get("progress") not in (None, ""):
            total = (
                target
                * kit.integer(
                    raw.get("progress"), "the progress", minimum=0, maximum=100
                )
                // 100
            )
        else:
            raise ValueError("Enter how much to add.")
        total = max(0, total)
        amount = total - before
        changes["current_amount"] = total
        progress = amount_progress(total, target)
    connection.execute(
        "INSERT INTO jr_checkins (id, goal_id, progress, amount, note, at) VALUES (?, ?, ?, ?, ?, ?)",
        (kit.new_id(), goal_id, progress, amount, note, stamp),
    )
    changes["progress"] = progress
    _set_progress(connection, me, goal, changes)
    connection.commit()
    return get_goal(connection, me, goal_id)


def _insert_step(connection: sqlite3.Connection, goal_id: str, title: Any) -> str:
    if (
        kit.count(
            connection, "SELECT COUNT(*) FROM jr_steps WHERE goal_id = ?", (goal_id,)
        )
        >= MAX_STEPS
    ):
        raise ValueError(f"A goal can have {MAX_STEPS} steps.")
    step_id = kit.new_id()
    position = kit.count(
        connection,
        "SELECT COALESCE(MAX(position), 0) + 1 FROM jr_steps WHERE goal_id = ?",
        (goal_id,),
    )
    connection.execute(
        "INSERT INTO jr_steps (id, goal_id, title, position, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            step_id,
            goal_id,
            kit.text(title, "a step", max_len=160, required=True),
            position,
            kit.now(),
        ),
    )
    return step_id


def _recount(connection: sqlite3.Connection, goal_id: str) -> int | None:
    """Progress from the steps (None when the goal has none, so the hand-set progress stays)."""
    steps = _steps(connection, goal_id)
    if not steps:
        return None
    progress = round(100 * sum(1 for s in steps if s["done"]) / len(steps))
    if progress == 100 and not all(s["done"] for s in steps):
        progress = 99  # 199 of 200 steps rounds to 100: the goal is not finished yet
    kit.update_row(
        connection, "jr_goals", goal_id, {"progress": progress, "updated_at": kit.now()}
    )
    return progress


def _after_steps(
    connection: sqlite3.Connection, me: dict[str, Any], goal_id: str
) -> dict[str, Any]:
    goal = _goal_row(connection, me, goal_id)
    progress = _recount(connection, goal_id)
    if progress is not None and progress >= 100 and goal["status"] == "active":
        kit.update_row(connection, "jr_goals", goal_id, {"status": "completed"})
        _complete(connection, me, goal_id)
    elif progress is not None and progress < 100 and goal["status"] == "completed":
        kit.update_row(
            connection, "jr_goals", goal_id, {"status": "active", "completed_at": None}
        )
    connection.commit()
    return get_goal(connection, me, goal_id)


def add_step(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    goal = _goal_row(connection, me, goal_id)
    if goal.get("measure"):
        raise ValueError(
            "This goal is measured in a number or money, so it has no steps."
        )
    _insert_step(connection, goal_id, raw.get("title"))
    return _after_steps(connection, me, goal_id)


def _step_row(
    connection: sqlite3.Connection, me: dict[str, Any], step_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT s.* FROM jr_steps s JOIN jr_goals g ON g.id = s.goal_id WHERE s.id = ? AND g.owner_id = ?",
        (step_id, me["id"]),
    )
    if not found:
        raise LookupError("That step was not found.")
    return found


def set_step(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    step_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    step = _step_row(connection, me, step_id)
    changes: dict[str, Any] = {}
    if "title" in raw:
        changes["title"] = kit.text(
            raw.get("title"), "a step", max_len=160, required=True
        )
    if "done" in raw:
        done = kit.flag(raw.get("done"))
        changes["done"] = 1 if done else 0
        changes["done_at"] = kit.now() if done else None
    kit.update_row(connection, "jr_steps", step_id, changes)
    return _after_steps(connection, me, step["goal_id"])


def delete_step(
    connection: sqlite3.Connection, me: dict[str, Any], step_id: str
) -> dict[str, Any]:
    step = _step_row(connection, me, step_id)
    connection.execute("DELETE FROM jr_steps WHERE id = ?", (step_id,))
    return _after_steps(connection, me, step["goal_id"])


def _complete(connection: sqlite3.Connection, me: dict[str, Any], goal_id: str) -> None:
    """A finished goal: stamp it and put it on the timeline (once)."""
    goal = kit.require_row(connection, "jr_goals", goal_id, "goal")
    stamp = kit.now()
    kit.update_row(
        connection,
        "jr_goals",
        goal_id,
        {"completed_at": goal["completed_at"] or stamp, "progress": 100},
    )
    if kit.count(
        connection, "SELECT COUNT(*) FROM jr_events WHERE goal_id = ?", (goal_id,)
    ):
        return
    connection.execute(
        """INSERT INTO jr_events (id, owner_id, title, description, kind, event_date, goal_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'achievement', ?, ?, ?, ?)""",
        (
            kit.new_id(),
            me["id"],
            f"Reached a goal: {goal['title']}"[:160],
            goal["description"][:2000],
            today(connection).isoformat(),
            goal_id,
            stamp,
            stamp,
        ),
    )


def format_amount(goal: dict[str, Any], cents: int) -> str:
    """An amount of a measured goal as a person reads it: "$1,250.00", "42.5 km"."""
    if measure_of(goal) == "money":
        return kit.fmt_money(cents, goal.get("currency") or "USD")
    whole, part = divmod(abs(int(cents)), 100)
    number = f"{whole:,}" + (f".{part:02d}".rstrip("0") if part else "")
    unit = goal.get("unit") or ""
    return f"{'-' if cents < 0 else ''}{number}{' ' + unit if unit else ''}"


def goals_csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    rows = [
        {
            "title": g["title"],
            "category": g["category"],
            "status": g["status"],
            "progress": g["progress"],
            "target_date": g["target_date"],
            "steps": f"{g['steps_done']}/{len(g['steps'])}" if g["steps"] else "",
            "so_far": format_amount(g, g["current_amount"])
            if g["measure"] != "percent"
            else "",
            "target": format_amount(g, g["target_amount"])
            if g["measure"] != "percent"
            else "",
            "completed_at": g["completed_at"] or "",
            "description": g["description"],
        }
        for g in list_goals(connection, me)
    ]
    return kit.csv_bytes(
        rows,
        [
            "title",
            "category",
            "status",
            "progress",
            "target_date",
            "steps",
            "so_far",
            "target",
            "completed_at",
            "description",
        ],
    )


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


def _event_view(
    event: dict[str, Any], me: dict[str, Any], names: dict[str, str]
) -> dict[str, Any]:
    return {
        "id": event["id"],
        "title": event["title"],
        "description": event["description"],
        "kind": event["kind"],
        "event_date": event["event_date"],
        "location": event["location"],
        "shared": bool(event["shared"]),
        "goal_id": event["goal_id"],
        "mine": event["owner_id"] == me["id"],
        "owner_name": names.get(event["owner_id"], ""),
        "created_at": event["created_at"],
    }


def list_events(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    kind: str = "",
    year: str = "",
    family: bool = False,
    search: str = "",
) -> list[dict[str, Any]]:
    """The person's events, newest first; ``family`` adds what others in the household shared."""
    if family:
        sql = "SELECT * FROM jr_events WHERE (owner_id = ? OR shared = 1)"
    else:
        sql = "SELECT * FROM jr_events WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    if kind:
        sql += " AND kind = ?"
        params.append(kit.choice(kind, "the kind", EVENT_KINDS))
    if year:
        if not re.fullmatch(r"\d{4}", year):
            raise ValueError("The year must look like 2024.")
        sql += " AND substr(event_date, 1, 4) = ?"
        params.append(year)
    if search:
        like = f"%{kit.text(search, 'the search', max_len=80)}%"
        sql += " AND (title LIKE ? OR description LIKE ? OR location LIKE ?)"
        params += [like, like, like]
    sql += " ORDER BY event_date DESC, created_at DESC LIMIT 1000"
    names = {p["id"]: p["name"] for p in life.profiles(connection)}
    return [
        _event_view(row, me, names) for row in kit.fetch_all(connection, sql, params)
    ]


def _clean_event(raw: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "title": kit.text(
            merged.get("title"), "what happened", max_len=160, required=True
        ),
        "description": kit.text(
            merged.get("description"), "the description", max_len=2000, multiline=True
        ),
        "kind": kit.choice(merged.get("kind"), "the kind", EVENT_KINDS, default="life"),
        "event_date": kit.day(merged.get("event_date"), "the date", required=True),
        "location": kit.text(merged.get("location"), "the place", max_len=120),
        "shared": 1 if kit.flag(merged.get("shared")) else 0,
    }


def _event_row(
    connection: sqlite3.Connection, me: dict[str, Any], event_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection, "SELECT * FROM jr_events WHERE id = ?", (event_id,)
    )
    if not found or (found["owner_id"] != me["id"] and not found["shared"]):
        raise LookupError("That event was not found.")
    if found["owner_id"] != me["id"]:
        raise PermissionError("Only the person who added this event can change it.")
    return found


def save_event(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    event_id: str = "",
) -> dict[str, Any]:
    current = _event_row(connection, me, event_id) if event_id else None
    values = _clean_event(raw, current)
    stamp = kit.now()
    if current:
        kit.update_row(
            connection, "jr_events", event_id, {**values, "updated_at": stamp}
        )
    else:
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM jr_events WHERE owner_id = ?",
                (me["id"],),
            )
            >= MAX_EVENTS
        ):
            raise ValueError(f"Your timeline can hold {MAX_EVENTS} events.")
        event_id = kit.new_id()
        connection.execute(
            """INSERT INTO jr_events (id, owner_id, title, description, kind, event_date, location, shared,
            created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                me["id"],
                values["title"],
                values["description"],
                values["kind"],
                values["event_date"],
                values["location"],
                values["shared"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    names = {p["id"]: p["name"] for p in life.profiles(connection)}
    return _event_view(
        kit.require_row(connection, "jr_events", event_id, "event"), me, names
    )


def delete_event(
    connection: sqlite3.Connection, me: dict[str, Any], event_id: str
) -> None:
    _event_row(connection, me, event_id)
    connection.execute("DELETE FROM jr_events WHERE id = ?", (event_id,))
    connection.commit()


def event_years(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    """The years the person's timeline (and what the household shared) has events in, newest first."""
    return [
        row["year"]
        for row in kit.fetch_all(
            connection,
            "SELECT DISTINCT substr(event_date, 1, 4) AS year FROM jr_events WHERE owner_id = ? OR shared = 1 ORDER BY year DESC",
            (me["id"],),
        )
    ]


def events_csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    """The person's own timeline, oldest first (the story of their life as a spreadsheet)."""
    rows = [
        {
            "date": e["event_date"],
            "title": e["title"],
            "kind": e["kind"],
            "location": e["location"],
            "shared": "yes" if e["shared"] else "no",
            "story": e["description"],
        }
        for e in reversed(list_events(connection, me))
    ]
    return kit.csv_bytes(rows, ["date", "title", "kind", "location", "shared", "story"])


# ---------------------------------------------------------------------------
# document vault
# ---------------------------------------------------------------------------


def sniff(data: bytes, name: str) -> str:
    """The file's real type from its first bytes; refuses anything that is not a scan, a photo or text."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if name.lower().endswith(".txt"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return "text/plain"
    raise ValueError("Add a PDF, a photo (JPEG, PNG, WebP or GIF) or a text file.")


def expiry_state(expires_on: str, now: date) -> str:
    left = days_until(expires_on, now)
    if left is None:
        return "no_expiry"
    if left < 0:
        return "expired"
    if left <= SOON_DAYS:
        return "expires_soon"
    return "valid"


def _document_view(
    row: dict[str, Any], now: date, *, number: str | None = None
) -> dict[str, Any]:
    view = {
        "id": row["id"],
        "title": row["title"],
        "kind": row["kind"],
        "issuer": row["issuer"],
        "number_hint": row["number_hint"],
        "issued_on": row["issued_on"],
        "expires_on": row["expires_on"],
        "days_left": days_until(row["expires_on"], now),
        "state": expiry_state(row["expires_on"], now),
        "tags": kit.loads(row["tags"], []),
        "notes": row["notes"],
        "file": {
            "name": row["file_name"],
            "type": row["file_type"],
            "size": int(row["file_size"]),
        }
        if row["file_size"]
        else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if number is not None:
        view["number"] = number
    return view


def _document_row(
    connection: sqlite3.Connection, me: dict[str, Any], document_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM jr_documents WHERE id = ? AND owner_id = ?",
        (document_id, me["id"]),
    )
    if not found:
        raise LookupError("That document was not found.")
    return found


# What a list reads: never the encrypted number or scan (a scan is up to 10 MB, and a vault holds hundreds).
_LIST_COLUMNS = (
    "id, owner_id, title, kind, issuer, number_hint, issued_on, expires_on, tags, notes, file_name, file_type, "
    "file_size, created_at, updated_at"
)


def list_documents(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    kind: str = "",
    state: str = "",
    search: str = "",
) -> list[dict[str, Any]]:
    sql = f"SELECT {_LIST_COLUMNS} FROM jr_documents WHERE owner_id = ?"  # noqa: S608 - fixed names
    params: list[Any] = [me["id"]]
    if kind:
        sql += " AND kind = ?"
        params.append(kit.choice(kind, "the kind", DOCUMENT_KINDS))
    if search:
        like = f"%{kit.text(search, 'the search', max_len=80)}%"
        sql += " AND (title LIKE ? OR issuer LIKE ? OR tags LIKE ? OR notes LIKE ?)"
        params += [like, like, like, like]
    sql += " ORDER BY CASE WHEN expires_on = '' THEN 1 ELSE 0 END, expires_on, title"
    now = today(connection)
    rows = [_document_view(row, now) for row in kit.fetch_all(connection, sql, params)]
    if state:
        kit.choice(
            state, "the state", ("expired", "expires_soon", "valid", "no_expiry")
        )
        rows = [row for row in rows if row["state"] == state]
    return rows


def get_document(
    connection: sqlite3.Connection, me: dict[str, Any], document_id: str, seal: Seal
) -> dict[str, Any]:
    """One document with its number decrypted (only for its owner)."""
    row = _document_row(connection, me, document_id)
    number = seal.decrypt(row["number_cipher"]) if row["number_cipher"] else ""
    return _document_view(row, today(connection), number=number)


def _decode_file(raw: dict[str, Any]) -> tuple[bytes, str, str] | None:
    upload = raw.get("file")
    if not upload:
        return None
    if not isinstance(upload, dict):
        raise ValueError("Send the file as {name, data}.")
    name = kit.text(upload.get("name"), "the file name", max_len=160) or "document"
    encoded = str(upload.get("data") or "")
    if len(encoded) > (MAX_FILE_BYTES * 4) // 3 + 8:
        raise ValueError("A document can be at most 10 MB.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The file could not be read. Try adding it again.") from None
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("A document can be at most 10 MB.")
    kind = sniff(data, name)
    stem = (
        re.sub(r"[^A-Za-z0-9 ._-]+", "", name.rsplit(".", 1)[0]).strip() or "document"
    )
    return data, kind, f"{stem[:80]}{FILE_TYPES[kind]}"


def save_document(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    seal: Seal,
    document_id: str = "",
) -> dict[str, Any]:
    current = _document_row(connection, me, document_id) if document_id else None
    merged = {**(current or {}), **raw}
    values: dict[str, Any] = {
        "title": kit.text(
            merged.get("title"), "a name for the document", max_len=120, required=True
        ),
        "kind": kit.choice(
            merged.get("kind"), "the kind", DOCUMENT_KINDS, default="other"
        ),
        "issuer": kit.text(merged.get("issuer"), "who issued it", max_len=120),
        "issued_on": kit.day(merged.get("issued_on"), "the issue date"),
        "expires_on": kit.day(merged.get("expires_on"), "the expiry date"),
        "notes": kit.text(
            merged.get("notes"), "the notes", max_len=2000, multiline=True
        ),
        "tags": kit.dumps(
            kit.tags(
                raw["tags"] if "tags" in raw else kit.loads(merged.get("tags"), []),
                limit=10,
            )
        ),
    }
    if (
        values["issued_on"]
        and values["expires_on"]
        and values["expires_on"] < values["issued_on"]
    ):
        raise ValueError("The expiry date is before the issue date.")
    if "number" in raw:
        number = kit.text(raw.get("number"), "the document number", max_len=80)
        values["number_cipher"] = seal.encrypt(number) if number else None
        values["number_hint"] = (
            f"•••• {number[-4:]}" if len(number) > 4 else ("••••" if number else "")
        )
    upload = _decode_file(raw)
    if upload is not None:
        data, file_type, file_name = upload
        replaced = int(current["file_size"]) if current else 0
        if vault_usage(connection, me) - replaced + len(data) > MAX_VAULT_BYTES:
            raise ValueError(
                f"Your vault is full ({MAX_VAULT_BYTES // (1024 * 1024)} MB of scans). Remove a scan you no longer need first."
            )
        values.update(
            file_cipher=seal.encrypt(base64.b64encode(data).decode("ascii")),
            file_type=file_type,
            file_name=file_name,
            file_size=len(data),
        )
    elif raw.get("remove_file"):
        values.update(file_cipher=None, file_type="", file_name="", file_size=0)
    stamp = kit.now()
    if current:
        kit.update_row(
            connection, "jr_documents", document_id, {**values, "updated_at": stamp}
        )
    else:
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM jr_documents WHERE owner_id = ?",
                (me["id"],),
            )
            >= MAX_DOCUMENTS
        ):
            raise ValueError(f"The vault can hold {MAX_DOCUMENTS} documents.")
        document_id = kit.new_id()
        values.setdefault("number_cipher", None)
        values.setdefault("number_hint", "")
        values.setdefault("file_cipher", None)
        values.setdefault("file_type", "")
        values.setdefault("file_name", "")
        values.setdefault("file_size", 0)
        columns = ["id", "owner_id", *values.keys(), "created_at", "updated_at"]
        connection.execute(
            f"INSERT INTO jr_documents ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",  # noqa: S608 - fixed names
            (document_id, me["id"], *values.values(), stamp, stamp),
        )
    connection.commit()
    return get_document(connection, me, document_id, seal)


def document_file(
    connection: sqlite3.Connection, me: dict[str, Any], document_id: str, seal: Seal
) -> tuple[bytes, str, str]:
    row = _document_row(connection, me, document_id)
    if not row["file_cipher"]:
        raise LookupError("This document has no file.")
    data = base64.b64decode(seal.decrypt(row["file_cipher"]))
    return (
        data,
        row["file_type"] or "application/octet-stream",
        row["file_name"] or "document",
    )


def delete_document(
    connection: sqlite3.Connection, me: dict[str, Any], document_id: str
) -> None:
    _document_row(connection, me, document_id)
    connection.execute("DELETE FROM jr_documents WHERE id = ?", (document_id,))
    connection.commit()


def documents_csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    """A list of the vault (never the numbers or the scans): for insurance, a move or the family's records."""
    labels = {
        "valid": "valid",
        "expires_soon": "expires soon",
        "expired": "expired",
        "no_expiry": "no expiry",
    }
    rows = [
        {
            "name": d["title"],
            "kind": d["kind"],
            "issued_by": d["issuer"],
            "issued_on": d["issued_on"],
            "expires_on": d["expires_on"],
            "state": labels[d["state"]],
            "number": "saved" if d["number_hint"] else "",
            "scan": d["file"]["name"] if d["file"] else "",
            "tags": ", ".join(d["tags"]),
            "notes": d["notes"],
        }
        for d in list_documents(connection, me)
    ]
    return kit.csv_bytes(
        rows,
        [
            "name",
            "kind",
            "issued_by",
            "issued_on",
            "expires_on",
            "state",
            "number",
            "scan",
            "tags",
            "notes",
        ],
    )


def vault_usage(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    return kit.count(
        connection,
        "SELECT COALESCE(SUM(file_size), 0) FROM jr_documents WHERE owner_id = ?",
        (me["id"],),
    )


# ---------------------------------------------------------------------------
# habits
# ---------------------------------------------------------------------------


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def streaks(days: set[date], per_week: int, now: date) -> dict[str, int]:
    """The current and best streak: days in a row for an every-day habit, weeks in a row (each with at least
    ``per_week`` days done) for the others. Today (or this week) counts once it is done; until then the streak
    runs to yesterday (or last week), so it is not broken in the morning."""
    if per_week >= 7:
        cursor = now if now in days else now - timedelta(days=1)
        current = 0
        while cursor in days:
            current += 1
            cursor -= timedelta(days=1)
        best = run = 0
        previous: date | None = None
        for day in sorted(days):
            run = run + 1 if previous and (day - previous).days == 1 else 1
            best = max(best, run)
            previous = day
        return {"current": current, "best": max(best, current)}
    weeks: dict[date, int] = {}
    for day in days:
        weeks[_week_start(day)] = weeks.get(_week_start(day), 0) + 1
    met = {week for week, n in weeks.items() if n >= per_week}
    cursor = _week_start(now)
    if cursor not in met:
        cursor -= timedelta(days=7)
    current = 0
    while cursor in met:
        current += 1
        cursor -= timedelta(days=7)
    best = run = 0
    previous_week: date | None = None
    for week in sorted(met):
        run = run + 1 if previous_week and (week - previous_week).days == 7 else 1
        best = max(best, run)
        previous_week = week
    return {"current": current, "best": max(best, current)}


def _habit_row(
    connection: sqlite3.Connection, me: dict[str, Any], habit_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM jr_habits WHERE id = ? AND owner_id = ?",
        (habit_id, me["id"]),
    )
    if not found:
        raise LookupError("That habit was not found.")
    return found


def habit_view(
    connection: sqlite3.Connection, habit: dict[str, Any], now: date
) -> dict[str, Any]:
    since = (now - timedelta(days=400)).isoformat()
    done = {
        date.fromisoformat(row["day"])
        for row in kit.fetch_all(
            connection,
            "SELECT day FROM jr_habit_days WHERE habit_id = ? AND day >= ?",
            (habit["id"], since),
        )
    }
    per_week = int(habit["per_week"])
    streak = streaks(done, per_week, now)
    week = _week_start(now)
    last = [now - timedelta(days=offset) for offset in range(27, -1, -1)]
    # The last 30 days, counted from the day the habit started (or the earliest day ticked, if earlier): a habit
    # started yesterday and done both days is at 100%, not 7%.
    started = date.fromisoformat(str(habit["created_at"])[:10])
    month = [now - timedelta(days=offset) for offset in range(30)]
    first = min([started, *[day for day in done if day in month]])
    month = [day for day in month if day >= first] or [now]
    expected = max(1, round(len(month) * per_week / 7))
    goal = (
        kit.fetch_one(
            connection,
            "SELECT id, title, measure, unit, currency FROM jr_goals WHERE id = ?",
            (habit["goal_id"],),
        )
        if habit["goal_id"]
        else None
    )
    return {
        "id": habit["id"],
        "title": habit["title"],
        "per_week": per_week,
        "every_day": per_week >= 7,
        "archived": bool(habit["archived"]),
        "goal_id": habit["goal_id"] if goal else "",
        "goal_title": goal["title"] if goal else "",
        "amount": int(habit["amount"]) if goal else 0,
        "amount_label": format_amount(goal, int(habit["amount"]))
        if goal and int(habit["amount"])
        else "",
        "done_today": now in done,
        "this_week": sum(1 for day in done if day >= week),
        "streak": streak["current"],
        "best_streak": streak["best"],
        "rate": min(
            100, round(100 * sum(1 for day in month if day in done) / expected)
        ),
        "total": kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_habit_days WHERE habit_id = ?",
            (habit["id"],),
        ),
        "days": [{"day": day.isoformat(), "done": day in done} for day in last],
        "started_on": started.isoformat(),
        "created_at": habit["created_at"],
    }


def list_habits(
    connection: sqlite3.Connection, me: dict[str, Any], *, archived: bool = False
) -> list[dict[str, Any]]:
    now = today(connection)
    return [
        habit_view(connection, row, now)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM jr_habits WHERE owner_id = ? AND archived = ? ORDER BY created_at",
            (me["id"], 1 if archived else 0),
        )
    ]


def _clean_habit(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    values: dict[str, Any] = {
        "title": kit.text(merged.get("title"), "a habit", max_len=80, required=True),
        "per_week": kit.integer(
            merged.get("per_week"),
            "how many days a week",
            default=7,
            minimum=1,
            maximum=7,
        ),
        "archived": 1 if kit.flag(merged.get("archived")) else 0,
        "goal_id": "",
        "amount": 0,
    }
    goal_id = str(merged.get("goal_id") or "").strip()
    if goal_id:
        goal = _goal_row(connection, me, goal_id)
        values["goal_id"] = goal_id
        if "amount" in raw:
            amount = money_in(
                raw.get("amount"),
                "the amount each time",
                currency=goal.get("currency") or "",
                default=0,
            )
        else:
            amount = int((current or {}).get("amount") or 0)
        if amount and (not goal.get("measure") or goal.get("source") == "earnings"):
            raise ValueError(
                "Only a goal you measure in a number or money (and add to yourself) can grow with a habit."
            )
        values["amount"] = amount
    return values


def save_habit(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    habit_id: str = "",
) -> dict[str, Any]:
    current = _habit_row(connection, me, habit_id) if habit_id else None
    values = _clean_habit(connection, me, raw, current)
    stamp = kit.now()
    if current:
        kit.update_row(
            connection, "jr_habits", habit_id, {**values, "updated_at": stamp}
        )
    else:
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM jr_habits WHERE owner_id = ? AND archived = 0",
                (me["id"],),
            )
            >= MAX_HABITS
        ):
            raise ValueError(
                f"You can keep {MAX_HABITS} habits going. Stop one you no longer do first."
            )
        habit_id = kit.new_id()
        connection.execute(
            """INSERT INTO jr_habits (id, owner_id, title, per_week, goal_id, amount, archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                habit_id,
                me["id"],
                values["title"],
                values["per_week"],
                values["goal_id"],
                values["amount"],
                values["archived"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    return habit_view(
        connection, _habit_row(connection, me, habit_id), today(connection)
    )


def delete_habit(
    connection: sqlite3.Connection, me: dict[str, Any], habit_id: str
) -> None:
    """Remove a habit and its history (what it added to a goal stays: that money was saved)."""
    _habit_row(connection, me, habit_id)
    connection.execute("DELETE FROM jr_habit_days WHERE habit_id = ?", (habit_id,))
    connection.execute("DELETE FROM jr_habits WHERE id = ?", (habit_id,))
    connection.commit()


def _habit_day(connection: sqlite3.Connection, raw: dict[str, Any]) -> str:
    now = today(connection)
    day = kit.day(raw.get("day"), "the day") or now.isoformat()
    when = date.fromisoformat(day)
    if when > now:
        raise ValueError("A habit can be ticked for today or a day before, not later.")
    if (now - when).days > HABIT_BACKFILL_DAYS:
        raise ValueError(
            f"A habit can be ticked for the last {HABIT_BACKFILL_DAYS} days only."
        )
    return day


def mark_habit(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    habit_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Tick a habit for a day (today unless ``day`` says otherwise). Ticking twice changes nothing; a habit
    linked to a goal adds its amount to the goal once per day."""
    habit = _habit_row(connection, me, habit_id)
    if habit["archived"]:
        raise ValueError("This habit is stopped. Start it again first.")
    day = _habit_day(connection, raw)
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM jr_habit_days WHERE habit_id = ? AND day = ?",
        (habit_id, day),
    ):
        return habit_view(connection, habit, today(connection))
    checkin_id = ""
    amount = int(habit["amount"])
    goal = (
        kit.fetch_one(
            connection,
            "SELECT * FROM jr_goals WHERE id = ? AND owner_id = ?",
            (habit["goal_id"], me["id"]),
        )
        if habit["goal_id"]
        else None
    )
    if amount and goal is not None and _adds_up(goal):
        checkin_id = _add_amount(
            connection, me, goal, amount, f"Habit: {habit['title']}"
        )
    connection.execute(
        "INSERT INTO jr_habit_days (habit_id, day, checkin_id, at) VALUES (?, ?, ?, ?)",
        (habit_id, day, checkin_id, kit.now()),
    )
    connection.commit()
    return habit_view(connection, habit, today(connection))


def unmark_habit(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    habit_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Untick a day (a mistake): what the tick added to a goal is taken back."""
    habit = _habit_row(connection, me, habit_id)
    day = kit.day(raw.get("day"), "the day") or today(connection).isoformat()
    row = kit.fetch_one(
        connection,
        "SELECT * FROM jr_habit_days WHERE habit_id = ? AND day = ?",
        (habit_id, day),
    )
    if row:
        connection.execute(
            "DELETE FROM jr_habit_days WHERE habit_id = ? AND day = ?", (habit_id, day)
        )
        _take_back(connection, me, row["checkin_id"])
        connection.commit()
    return habit_view(connection, habit, today(connection))


# ---------------------------------------------------------------------------
# a goal's public page and the cheers friends leave there
# ---------------------------------------------------------------------------


def may_share(me: dict[str, Any]) -> bool:
    """Children do not make public links (life-sdk.md)."""
    return me.get("age_group") != "child"


def public_goal(connection: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
    """What a goal's public page shows: the goal and its progress, the steps, the dates and the cheers.

    Never shown: check-in notes, the other goals, documents, and the amounts of a money goal (only its
    percentage: what someone saves or earns is theirs to tell)."""
    goal = kit.fetch_one(connection, "SELECT * FROM jr_goals WHERE id = ?", (goal_id,))
    if not goal:
        raise LookupError("This goal is no longer shared.")
    now = today(connection)
    measure = measure_of(goal)
    owner = kit.fetch_one(
        connection, "SELECT name FROM life_profiles WHERE id = ?", (goal["owner_id"],)
    )
    history = kit.fetch_all(
        connection,
        "SELECT progress, at FROM jr_checkins WHERE goal_id = ? ORDER BY at DESC LIMIT 12",
        (goal_id,),
    )
    return {
        "title": goal["title"],
        "why": goal["description"],
        "name": (str(owner["name"]) if owner else "").split(" ")[0],
        "category": goal["category"],
        "status": goal["status"],
        "state": goal_state(goal, now),
        "progress": int(goal["progress"]),
        "measure": measure,
        "so_far": format_amount(goal, int(goal["current_amount"]))
        if measure == "number"
        else "",
        "target": format_amount(goal, int(goal["target_amount"]))
        if measure == "number"
        else "",
        "target_date": goal["target_date"],
        "days_left": days_until(goal["target_date"], now),
        "completed_at": goal["completed_at"],
        "steps": [
            {"title": step["title"], "done": step["done"]}
            for step in _steps(connection, goal_id)
        ],
        "history": [
            {"progress": int(h["progress"]), "at": h["at"]} for h in reversed(history)
        ],
        "cheers": kit.fetch_all(
            connection,
            "SELECT name, message, at FROM jr_cheers WHERE goal_id = ? ORDER BY at DESC LIMIT 30",
            (goal_id,),
        ),
        "support": _public_support(connection, goal),
    }


def _public_support(
    connection: sqlite3.Connection, goal: dict[str, Any]
) -> dict[str, Any] | None:
    """The supporters part of a goal's page: how to pay the owner and who backed the goal (names and messages,
    never the amounts: what each person gives is between them and the owner)."""
    if not goal.get("support") or goal["status"] == "dropped":
        return None
    backers = kit.fetch_all(
        connection,
        "SELECT name, message, at FROM jr_pledges WHERE goal_id = ? AND status != 'cancelled' ORDER BY at DESC LIMIT 30",
        (goal["id"],),
    )
    return {
        "currency": pledge_currency(connection, goal),
        "pay_link": goal.get("pay_link") or "",
        "pay_note": goal.get("pay_note") or "",
        "supporters": kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_pledges WHERE goal_id = ? AND status != 'cancelled'",
            (goal["id"],),
        ),
        "backers": backers,
    }


def add_cheer(
    connection: sqlite3.Connection,
    goal_id: str,
    raw: dict[str, Any],
    client: str,
) -> dict[str, Any]:
    """A visitor's cheer on a goal's public page (name optional, a short message)."""
    goal = kit.fetch_one(
        connection, "SELECT id, owner_id FROM jr_goals WHERE id = ?", (goal_id,)
    )
    if not goal:
        raise LookupError("This goal is no longer shared.")
    name = kit.text(raw.get("name"), "your name", max_len=40) or "A friend"
    message = kit.text(raw.get("message"), "a message", max_len=280, required=True)
    if (
        kit.count(
            connection, "SELECT COUNT(*) FROM jr_cheers WHERE goal_id = ?", (goal_id,)
        )
        >= MAX_CHEERS
    ):
        raise ValueError("This page has all the cheers it can hold. Thank you!")
    key = kit.text(client, "the visitor", max_len=120)
    since = kit.iso(kit.utcnow() - timedelta(days=1))
    if (
        key
        and kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_cheers WHERE goal_id = ? AND client = ? AND at >= ?",
            (goal_id, key, since),
        )
        >= CHEERS_PER_VISITOR
    ):
        raise ValueError("You have cheered a lot today. Come back tomorrow!")
    cheer = {
        "id": kit.new_id(),
        "name": name,
        "message": message,
        "at": kit.now(),
    }
    connection.execute(
        "INSERT INTO jr_cheers (id, goal_id, owner_id, name, message, client, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cheer["id"], goal_id, goal["owner_id"], name, message, key, cheer["at"]),
    )
    connection.commit()
    return {"name": name, "message": message, "at": cheer["at"]}


def delete_cheer(
    connection: sqlite3.Connection, me: dict[str, Any], cheer_id: str
) -> None:
    if not kit.count(
        connection,
        "SELECT COUNT(*) FROM jr_cheers WHERE id = ? AND owner_id = ?",
        (cheer_id, me["id"]),
    ):
        raise LookupError("That cheer was not found.")
    connection.execute("DELETE FROM jr_cheers WHERE id = ?", (cheer_id,))
    connection.commit()


def unread_cheers(
    connection: sqlite3.Connection, me: dict[str, Any], *, days: int = 3
) -> list[dict[str, Any]]:
    since = kit.iso(kit.utcnow() - timedelta(days=days))
    return kit.fetch_all(
        connection,
        """SELECT c.id, c.name, c.message, c.at, g.title AS goal_title FROM jr_cheers c
        JOIN jr_goals g ON g.id = c.goal_id
        WHERE c.owner_id = ? AND c.read_at IS NULL AND c.at >= ? ORDER BY c.at DESC LIMIT 20""",
        (me["id"], since),
    )


# ---------------------------------------------------------------------------
# supporters: people who back a goal from its public page (and pay the owner through the owner's own link)
# ---------------------------------------------------------------------------


def may_take_support(me: dict[str, Any], goal: dict[str, Any] | None = None) -> bool:
    """Money is for adults (life-sdk.md): only an adult asks for support, and not on a dropped goal."""
    return life.is_adult(me) and (goal is None or goal["status"] != "dropped")


def pledge_currency(connection: sqlite3.Connection, goal: dict[str, Any]) -> str:
    """A money goal's own currency; any other goal uses the account's."""
    if measure_of(goal) == "money" and goal.get("currency"):
        return str(goal["currency"])
    return kit.business(connection)["currency"]


def set_support(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Turn supporters on or off for a goal, with how they pay: a link (PayPal.me, a UPI or Stripe link) and a
    short note (bank details, "cash when we meet"). Aira never takes the money itself."""
    goal = _goal_row(connection, me, goal_id)
    merged = {**goal, **raw}
    support = kit.flag(merged.get("support"))
    if support and not may_take_support(me, goal):
        raise PermissionError(
            "Only adults can ask for support, and not on a dropped goal."
        )
    changes = {
        "support": 1 if support else 0,
        "pay_link": kit.url(merged.get("pay_link"), "the payment link"),
        "pay_note": kit.text(
            merged.get("pay_note"), "how to pay you", max_len=300, multiline=True
        ),
        "updated_at": kit.now(),
    }
    kit.update_row(connection, "jr_goals", goal_id, changes)
    connection.commit()
    return goal_detail(connection, me, goal_id)


def add_pledge(
    connection: sqlite3.Connection,
    goal_id: str,
    raw: dict[str, Any],
    client: str,
) -> dict[str, Any]:
    """A visitor backs a goal on its public page: an amount they promise, a name and a message."""
    goal = kit.fetch_one(connection, "SELECT * FROM jr_goals WHERE id = ?", (goal_id,))
    if not goal:
        raise LookupError("This goal is no longer shared.")
    if not goal.get("support") or goal["status"] == "dropped":
        raise PermissionError("This goal is not taking support right now.")
    currency = pledge_currency(connection, goal)
    amount = money_in(raw.get("amount"), "the amount", currency=currency, minimum=1)
    if amount > MAX_PLEDGE_CENTS:
        raise ValueError(
            f"The most one person can pledge here is {kit.fmt_money(MAX_PLEDGE_CENTS, currency)}."
        )
    name = kit.text(raw.get("name"), "your name", max_len=40) or "A friend"
    message = kit.text(raw.get("message"), "a message", max_len=280)
    if (
        kit.count(
            connection, "SELECT COUNT(*) FROM jr_pledges WHERE goal_id = ?", (goal_id,)
        )
        >= MAX_PLEDGES
    ):
        raise ValueError("This goal has all the supporters it can hold. Thank you!")
    key = kit.text(client, "the visitor", max_len=120)
    since = kit.iso(kit.utcnow() - timedelta(days=1))
    if (
        key
        and kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_pledges WHERE goal_id = ? AND client = ? AND at >= ?",
            (goal_id, key, since),
        )
        >= PLEDGES_PER_VISITOR
    ):
        raise ValueError("You have pledged a lot today. Come back tomorrow!")
    stamp = kit.now()
    connection.execute(
        """INSERT INTO jr_pledges (id, goal_id, owner_id, name, message, amount, currency, status, client, at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'promised', ?, ?)""",
        (
            kit.new_id(),
            goal_id,
            goal["owner_id"],
            name,
            message,
            amount,
            currency,
            key,
            stamp,
        ),
    )
    connection.commit()
    return {
        "name": name,
        "message": message,
        "at": stamp,
        "amount_label": kit.fmt_money(amount, currency),
    }


def _pledge_row(
    connection: sqlite3.Connection, me: dict[str, Any], pledge_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM jr_pledges WHERE id = ? AND owner_id = ?",
        (pledge_id, me["id"]),
    )
    if not found:
        raise LookupError("That pledge was not found.")
    return found


def _adds_up(goal: dict[str, Any] | None, currency: str = "") -> bool:
    """Whether an amount may be added to this goal by hand: a number or money goal the person fills in
    themselves (not one counted from the apps' earnings), and for money only in the goal's own currency."""
    if not goal or not goal.get("measure") or goal["status"] == "dropped":
        return False
    if (goal.get("source") or "manual") == "earnings":
        return False
    if measure_of(goal) == "money" and currency:
        return goal.get("currency") == currency
    return measure_of(goal) == "money" or not currency


def _add_amount(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal: dict[str, Any],
    amount: int,
    note: str,
) -> str:
    """Add an amount to a measured goal as a check-in (finishing it at its target); returns the check-in's id."""
    total = max(0, int(goal["current_amount"]) + amount)
    progress = amount_progress(total, int(goal["target_amount"]))
    checkin_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        "INSERT INTO jr_checkins (id, goal_id, progress, amount, note, at) VALUES (?, ?, ?, ?, ?, ?)",
        (checkin_id, goal["id"], progress, amount, note[:500], stamp),
    )
    _set_progress(
        connection,
        me,
        goal,
        {"current_amount": total, "progress": progress, "updated_at": stamp},
    )
    return checkin_id


def _take_back(
    connection: sqlite3.Connection, me: dict[str, Any], checkin_id: str
) -> None:
    """Undo a check-in that added an amount (a habit unticked, a pledge marked not received)."""
    checkin = (
        kit.fetch_one(
            connection, "SELECT * FROM jr_checkins WHERE id = ?", (checkin_id,)
        )
        if checkin_id
        else None
    )
    goal = (
        kit.fetch_one(
            connection,
            "SELECT * FROM jr_goals WHERE id = ? AND owner_id = ?",
            (checkin["goal_id"], me["id"]),
        )
        if checkin
        else None
    )
    if not checkin or not goal:
        return
    connection.execute("DELETE FROM jr_checkins WHERE id = ?", (checkin["id"],))
    if not goal.get("measure") or (goal.get("source") or "manual") == "earnings":
        # The goal is measured in percent (or counts earnings) now: the amount it once held is gone already,
        # and the progress the person set by hand stays as it is.
        return
    total = max(0, int(goal["current_amount"]) - int(checkin["amount"] or 0))
    _set_progress(
        connection,
        me,
        goal,
        {
            "current_amount": total,
            "progress": amount_progress(total, int(goal["target_amount"])),
            "updated_at": kit.now(),
        },
    )


def set_pledge(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    pledge_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """The owner marks a pledge received (it is added to a money goal), back to promised, or cancelled."""
    pledge = _pledge_row(connection, me, pledge_id)
    status = kit.choice(raw.get("status"), "the status", PLEDGE_STATUSES)
    if status == pledge["status"]:
        return goal_detail(connection, me, pledge["goal_id"])
    changes: dict[str, Any] = {
        "status": status,
        "read_at": pledge["read_at"] or kit.now(),
    }
    if pledge["status"] == "received":
        _take_back(connection, me, pledge["checkin_id"])
        changes.update(checkin_id="", received_at=None)
    if status == "received":
        goal = _goal_row(connection, me, pledge["goal_id"])
        changes["received_at"] = kit.now()
        if _adds_up(goal, pledge["currency"]):
            changes["checkin_id"] = _add_amount(
                connection,
                me,
                goal,
                int(pledge["amount"]),
                f"Support from {pledge['name']}",
            )
    kit.update_row(connection, "jr_pledges", pledge_id, changes)
    connection.commit()
    return goal_detail(connection, me, pledge["goal_id"])


def delete_pledge(
    connection: sqlite3.Connection, me: dict[str, Any], pledge_id: str
) -> None:
    pledge = _pledge_row(connection, me, pledge_id)
    if pledge["status"] == "received":
        _take_back(connection, me, pledge["checkin_id"])
    connection.execute("DELETE FROM jr_pledges WHERE id = ?", (pledge_id,))
    connection.commit()


def unread_pledges(
    connection: sqlite3.Connection, me: dict[str, Any], *, days: int = 3
) -> list[dict[str, Any]]:
    since = kit.iso(kit.utcnow() - timedelta(days=days))
    return kit.fetch_all(
        connection,
        """SELECT p.id, p.name, p.message, p.amount, p.currency, p.at, g.title AS goal_title FROM jr_pledges p
        JOIN jr_goals g ON g.id = p.goal_id
        WHERE p.owner_id = ? AND p.read_at IS NULL AND p.status = 'promised' AND p.at >= ?
        ORDER BY p.at DESC LIMIT 20""",
        (me["id"], since),
    )


def support_summary(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> dict[str, Any]:
    """What supporters gave or promised this person, per currency (for the overview)."""
    by_currency: dict[str, dict[str, Any]] = {}
    for row in kit.fetch_all(
        connection,
        "SELECT currency, status, COUNT(*) AS n, SUM(amount) AS total FROM jr_pledges "
        "WHERE owner_id = ? AND status != 'cancelled' GROUP BY currency, status",
        (me["id"],),
    ):
        entry = by_currency.setdefault(
            row["currency"],
            {
                "currency": row["currency"],
                "promised": 0,
                "received": 0,
                "supporters": 0,
            },
        )
        entry[row["status"]] += int(row["total"] or 0)
        entry["supporters"] += int(row["n"] or 0)
    return {
        "supporters": sum(entry["supporters"] for entry in by_currency.values()),
        "totals": [
            {
                **entry,
                "promised_label": kit.fmt_money(entry["promised"], entry["currency"]),
                "received_label": kit.fmt_money(entry["received"], entry["currency"]),
            }
            for entry in by_currency.values()
        ],
        "goals": kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_goals WHERE owner_id = ? AND support = 1 AND status != 'dropped'",
            (me["id"],),
        ),
    }


# ---------------------------------------------------------------------------
# journal: a few lines and a mood a day, always private
# ---------------------------------------------------------------------------


def _entry_view(row: dict[str, Any]) -> dict[str, Any]:
    mood = int(row["mood"] or 0)
    return {
        "id": row["id"],
        "day": row["day"],
        "mood": mood,
        "mood_label": MOODS.get(mood, ""),
        "body": row["body"],
        "grateful": row["grateful"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _journal_day(connection: sqlite3.Connection, value: Any) -> str:
    now = today(connection)
    day = kit.day(value, "the day") or now.isoformat()
    if date.fromisoformat(day) > now:
        raise ValueError(
            "You can write about today or a day before, not a day to come."
        )
    return day


def save_entry(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Write (or rewrite) the journal for one day: a mood, what happened and one thing to be grateful for."""
    day = _journal_day(connection, raw.get("day"))
    current = kit.fetch_one(
        connection,
        "SELECT * FROM jr_journal WHERE owner_id = ? AND day = ?",
        (me["id"], day),
    )
    if current and kit.flag(raw.get("new")):
        # "Write about another day" on a day already written: never wipe what is there with empty boxes.
        raise ValueError(
            f"You already wrote about {day}. Open that day in your journal to change it."
        )
    merged = {**(current or {}), **raw}
    values = {
        "mood": kit.integer(
            merged.get("mood"), "the mood", default=0, minimum=0, maximum=5
        ),
        "body": kit.text(
            merged.get("body"), "what happened", max_len=5000, multiline=True
        ),
        "grateful": kit.text(
            merged.get("grateful"), "what you are grateful for", max_len=300
        ),
    }
    if not (values["mood"] or values["body"] or values["grateful"]):
        raise ValueError("Pick how the day was or write a few words.")
    stamp = kit.now()
    if current:
        kit.update_row(
            connection, "jr_journal", current["id"], {**values, "updated_at": stamp}
        )
        entry_id = current["id"]
    else:
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM jr_journal WHERE owner_id = ?",
                (me["id"],),
            )
            >= MAX_JOURNAL_DAYS
        ):
            raise ValueError(
                "Your journal is full. Export it and remove old days first."
            )
        entry_id = kit.new_id()
        connection.execute(
            """INSERT INTO jr_journal (id, owner_id, day, mood, body, grateful, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                me["id"],
                day,
                values["mood"],
                values["body"],
                values["grateful"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    return _entry_view(
        kit.require_row(connection, "jr_journal", entry_id, "journal day")
    )


def set_mood(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Today's mood in one tap (Today's buttons): what was written that day stays; pressing again changes nothing."""
    mood = kit.integer(raw.get("mood"), "the mood", minimum=1, maximum=5)
    return save_entry(connection, me, {"mood": mood, "day": raw.get("day") or ""})


def delete_entry(
    connection: sqlite3.Connection, me: dict[str, Any], entry_id: str
) -> None:
    if not kit.count(
        connection,
        "SELECT COUNT(*) FROM jr_journal WHERE id = ? AND owner_id = ?",
        (entry_id, me["id"]),
    ):
        raise LookupError("That journal day was not found.")
    connection.execute("DELETE FROM jr_journal WHERE id = ?", (entry_id,))
    connection.commit()


def list_entries(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    month: str = "",
    search: str = "",
    mood: str = "",
    limit: int = 400,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM jr_journal WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    if month:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("The month must look like 2026-10.")
        sql += " AND substr(day, 1, 7) = ?"
        params.append(month)
    if mood:
        sql += " AND mood = ?"
        params.append(kit.integer(mood, "the mood", minimum=1, maximum=5))
    if search:
        like = f"%{kit.text(search, 'the search', max_len=80)}%"
        sql += " AND (body LIKE ? OR grateful LIKE ?)"
        params += [like, like]
    sql += " ORDER BY day DESC LIMIT ?"
    params.append(max(1, min(int(limit), MAX_JOURNAL_DAYS)))
    return [_entry_view(row) for row in kit.fetch_all(connection, sql, params)]


def journal_summary(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> dict[str, Any]:
    """Days in a row, this month's entries, the mood of the last 30 days and today's entry."""
    now = today(connection)
    by_day = {
        row["day"]: row
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM jr_journal WHERE owner_id = ? AND day >= ?",
            (me["id"], (now - timedelta(days=400)).isoformat()),
        )
    }
    days = {date.fromisoformat(day) for day in by_day}
    streak = streaks(days, 7, now)
    last = [now - timedelta(days=offset) for offset in range(29, -1, -1)]
    moods = [
        int(by_day[d.isoformat()]["mood"] or 0) if d.isoformat() in by_day else 0
        for d in last
    ]
    rated = [m for m in moods if m]
    average = round(sum(rated) / len(rated), 1) if rated else 0
    return {
        "today": now.isoformat(),
        "entry_today": _entry_view(by_day[now.isoformat()])
        if now.isoformat() in by_day
        else None,
        "streak": streak["current"],
        "best_streak": streak["best"],
        "this_month": sum(
            1 for day in days if day.year == now.year and day.month == now.month
        ),
        "total": kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_journal WHERE owner_id = ?",
            (me["id"],),
        ),
        "mood_days": [
            {"day": d.isoformat(), "mood": m} for d, m in zip(last, moods, strict=True)
        ],
        "mood_average": average,
        "mood_word": MOODS.get(round(average), "") if average else "",
        "months": [
            row["month"]
            for row in kit.fetch_all(
                connection,
                "SELECT DISTINCT substr(day, 1, 7) AS month FROM jr_journal WHERE owner_id = ? ORDER BY month DESC",
                (me["id"],),
            )
        ],
    }


def uses_life(connection: sqlite3.Connection, me: dict[str, Any]) -> bool:
    """Whether this person keeps anything in Life's journal, goals or habits (Today asks them about their day)."""
    return any(
        kit.count(
            connection,
            f"SELECT EXISTS (SELECT 1 FROM {table} WHERE owner_id = ?)",  # noqa: S608 - fixed names
            (me["id"],),
        )
        for table in ("jr_journal", "jr_goals", "jr_habits")
    )


def journal_csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    rows = [
        {
            "day": e["day"],
            "mood": e["mood_label"],
            "what_happened": e["body"],
            "grateful_for": e["grateful"],
        }
        for e in reversed(list_entries(connection, me, limit=MAX_JOURNAL_DAYS))
    ]
    return kit.csv_bytes(rows, ["day", "mood", "what_happened", "grateful_for"])


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def memories(
    connection: sqlite3.Connection, me: dict[str, Any], *, limit: int = 5
) -> list[dict[str, Any]]:
    """On this day: the person's own timeline events and journal days from this date in earlier years."""
    now = today(connection)
    dates = [now.strftime("%m-%d")]
    if dates[0] == "02-28" and not _leap(now.year):
        dates.append("02-29")  # a leap day is remembered on the 28th in other years
    marks = ", ".join("?" for _ in dates)
    this_year = f"{now.year:04d}"
    out: list[dict[str, Any]] = []
    for event in kit.fetch_all(
        connection,
        "SELECT id, title, event_date, kind FROM jr_events WHERE owner_id = ? "
        f"AND substr(event_date, 6, 5) IN ({marks}) AND substr(event_date, 1, 4) < ? "  # noqa: S608 - placeholders
        "ORDER BY event_date DESC LIMIT 20",
        (me["id"], *dates, this_year),
    ):
        out.append(
            {
                "type": "event",
                "id": event["id"],
                "title": event["title"],
                "date": event["event_date"],
                "kind": event["kind"],
            }
        )
    for entry in kit.fetch_all(
        connection,
        "SELECT * FROM jr_journal WHERE owner_id = ? "
        f"AND substr(day, 6, 5) IN ({marks}) AND substr(day, 1, 4) < ? "  # noqa: S608 - placeholders
        "ORDER BY day DESC LIMIT 20",
        (me["id"], *dates, this_year),
    ):
        lines = (entry["body"] or entry["grateful"] or "").strip().splitlines()
        first = lines[0].strip() if lines else ""
        mood = MOODS.get(int(entry["mood"] or 0), "")
        if first:
            title = first[:100] + ("..." if len(first) > 100 else "")
        else:
            title = f"You felt {mood.lower()}" if mood else "A day in your journal"
        out.append(
            {
                "type": "journal",
                "id": entry["id"],
                "title": title,
                "date": entry["day"],
                "kind": "",
            }
        )
    for item in out:
        item["years_ago"] = now.year - int(item["date"][:4])
    return sorted(out, key=lambda item: item["date"], reverse=True)[:limit]


# ---------------------------------------------------------------------------
# step ideas for a goal (the writing model, or a good template)
# ---------------------------------------------------------------------------

_STEP_TEMPLATES: dict[str, list[str]] = {
    "career": [
        "Write down the job or role you want",
        "Update your CV and online profile",
        "List 10 people or companies to reach out to",
        "Learn one skill the role asks for",
        "Apply or reach out to 3 places a week",
        "Practise for interviews with a friend",
    ],
    "health": [
        "Talk to a doctor if you are not sure where to start",
        "Pick the days and time you will do it",
        "Start small this week",
        "Keep a simple log for 4 weeks",
        "Add a little more each week",
        "Celebrate your first milestone",
    ],
    "money": [
        "Write down the amount and the date you need it by",
        "Look at what you spend in a month",
        "Set an amount to put aside each week",
        "Keep the money in a separate place",
        "Find one way to earn a little extra",
        "Check your progress every month",
    ],
    "learning": [
        "Pick a course, a book or a teacher",
        "Set a time to practise each week",
        "Finish the first lesson",
        "Use what you learnt in a small project",
        "Ask someone to test you",
        "Teach or share what you learnt",
    ],
    "travel": [
        "Choose where and when",
        "Set a budget for the trip",
        "Check passports and visas",
        "Book travel and a place to stay",
        "Plan what you want to see",
        "Pack and enjoy it",
    ],
}
_DEFAULT_STEPS = [
    "Write down why this goal matters to you",
    "Decide what done looks like and pick a date",
    "List what you need: time, money or help",
    "Do the first small step this week",
    "Set a regular time to work on it",
    "Check your progress every week",
    "Celebrate when you reach it",
]


def _clean_steps(value: Any) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(item or "")).strip()
        line = re.sub(r"\s+", " ", line)[:160]
        if line and line.lower() not in {existing.lower() for existing in out}:
            out.append(line)
    return out[:MAX_SUGGESTED_STEPS]


def suggest_steps(raw: dict[str, Any]) -> dict[str, Any]:
    """Small steps for a goal: the writing model's (when it runs and answers well) or a template for its area."""
    title = kit.text(raw.get("title"), "a goal", max_len=120, required=True)
    category = kit.choice(
        raw.get("category"), "the category", GOAL_CATEGORIES, default="personal"
    )
    why = kit.text(
        raw.get("description"), "the description", max_len=500, multiline=True
    )
    template = _STEP_TEMPLATES.get(category, _DEFAULT_STEPS)
    prompt = (
        "Break this personal goal into 4 to 7 small, concrete steps a person can tick off, in order. "
        "Use plain, friendly words; each step under 80 characters. "
        'Reply with JSON only, like {"steps": ["...", "..."]}.\n'
        f"Goal: {title}\nArea of life: {category}\n"
        + (f"Why it matters: {why}\n" if why else "")
    )
    # An empty fallback: kit.draft fills a key the model left out from the fallback and still says "model",
    # so the template is only used below, labelled as a template.
    answer = kit.draft(prompt, {"steps": []})
    steps = _clean_steps(answer.get("steps"))
    if answer.get("source") == "model" and len(steps) >= 2:
        return {"steps": steps, "source": "model"}
    return {"steps": list(template), "source": "template"}


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def milestones(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    days: int = 365,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """What is coming up: future events, goal target dates and document expiries, soonest first."""
    now = today(connection)
    until = (now + timedelta(days=days)).isoformat()
    start = now.isoformat()
    out: list[dict[str, Any]] = []
    for event in kit.fetch_all(
        connection,
        "SELECT id, title, event_date FROM jr_events WHERE owner_id = ? AND event_date >= ? AND event_date <= ?",
        (me["id"], start, until),
    ):
        out.append(
            {
                "type": "event",
                "id": event["id"],
                "title": event["title"],
                "date": event["event_date"],
            }
        )
    for goal in kit.fetch_all(
        connection,
        "SELECT id, title, target_date FROM jr_goals WHERE owner_id = ? AND status = 'active' AND target_date >= ? AND target_date <= ?",
        (me["id"], start, until),
    ):
        out.append(
            {
                "type": "goal",
                "id": goal["id"],
                "title": goal["title"],
                "date": goal["target_date"],
            }
        )
    for document in kit.fetch_all(
        connection,
        "SELECT id, title, expires_on FROM jr_documents WHERE owner_id = ? AND expires_on >= ? AND expires_on <= ?",
        (me["id"], start, until),
    ):
        out.append(
            {
                "type": "document",
                "id": document["id"],
                "title": document["title"],
                "date": document["expires_on"],
            }
        )
    for item in out:
        item["days_left"] = days_until(item["date"], now)
    return sorted(out, key=lambda item: (item["date"], item["title"]))[:limit]


def overview(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    now = today(connection)
    goals = list_goals(connection, me)
    active = [g for g in goals if g["status"] == "active"]
    documents = list_documents(connection, me)
    events = kit.count(
        connection, "SELECT COUNT(*) FROM jr_events WHERE owner_id = ?", (me["id"],)
    )
    habits = list_habits(connection, me)
    journal = journal_summary(connection, me)
    return {
        "me": {"id": me["id"], "name": me["name"]},
        "memories": memories(connection, me),
        "journal": {
            "streak": journal["streak"],
            "this_month": journal["this_month"],
            "mood_today": journal["entry_today"]["mood"]
            if journal["entry_today"]
            else 0,
            "written_today": bool(journal["entry_today"]),
        },
        "support": support_summary(connection, me),
        "may_take_support": may_take_support(me),
        "habits": {
            "total": len(habits),
            "done_today": sum(1 for h in habits if h["done_today"]),
            "best": [
                {
                    "title": h["title"],
                    "streak": h["streak"],
                    "every_day": h["every_day"],
                }
                for h in sorted(habits, key=lambda h: -h["streak"])[:1]
                if h["streak"]
            ],
        },
        "money_goals": [
            {
                "id": g["id"],
                "title": g["title"],
                "progress": g["progress"],
                "source": g["source"],
                "so_far": format_amount(g, g["current_amount"]),
                "target": format_amount(g, g["target_amount"]),
                "state": g["state"],
            }
            for g in active
            if g["measure"] == "money"
        ],
        "may_count_earnings": may_count_earnings(me),
        "cheers_unread": kit.count(
            connection,
            "SELECT COUNT(*) FROM jr_cheers WHERE owner_id = ? AND read_at IS NULL",
            (me["id"],),
        ),
        "today": now.isoformat(),
        "goals": {
            "active": len(active),
            "in_progress": sum(1 for g in active if 0 < g["progress"] < 100),
            "completed": sum(1 for g in goals if g["status"] == "completed"),
            "behind": sum(1 for g in active if g["state"] in {"behind", "overdue"}),
            "total": len(goals),
        },
        "life_score": round(sum(g["progress"] for g in active) / len(active))
        if active
        else 0,
        "documents": {
            "total": len(documents),
            "expires_soon": sum(1 for d in documents if d["state"] == "expires_soon"),
            "expired": sum(1 for d in documents if d["state"] == "expired"),
            "bytes": vault_usage(connection, me),
        },
        "events": events,
        "recent_goals": sorted(active, key=lambda g: g["updated_at"], reverse=True)[:4],
        "milestones": milestones(connection, me),
        "categories": [
            {
                "category": category,
                "goals": sum(1 for g in active if g["category"] == category),
                "progress": round(
                    sum(g["progress"] for g in active if g["category"] == category)
                    / max(1, sum(1 for g in active if g["category"] == category))
                ),
            }
            for category in GOAL_CATEGORIES
            if any(g["category"] == category for g in active)
        ],
    }


def reminders(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """Goals due within a week or overdue, and documents expiring within REMIND_DAYS or expired (for Today and pulse)."""
    now = today(connection)
    week = (now + timedelta(days=7)).isoformat()
    goals = kit.fetch_all(
        connection,
        "SELECT id, title, target_date, progress FROM jr_goals WHERE owner_id = ? AND status = 'active' AND target_date != '' AND target_date <= ? ORDER BY target_date",
        (me["id"], week),
    )
    soon = (now + timedelta(days=REMIND_DAYS)).isoformat()
    # A document that ran out long ago (an old passport kept for the record) stops reminding after REMIND_DAYS.
    lapsed = (now - timedelta(days=REMIND_DAYS)).isoformat()
    documents = kit.fetch_all(
        connection,
        "SELECT id, title, expires_on FROM jr_documents WHERE owner_id = ? AND expires_on != '' AND expires_on >= ? AND expires_on <= ? ORDER BY expires_on",
        (me["id"], lapsed, soon),
    )
    for goal in goals:
        goal["days_left"] = days_until(goal["target_date"], now)
    for document in documents:
        document["days_left"] = days_until(document["expires_on"], now)
    return {"goals": goals, "documents": documents}


# ---------------------------------------------------------------------------
# search across the app (the search box at the top of the Aira frame)
# ---------------------------------------------------------------------------

SEARCH_KINDS = ("goals", "habits", "entries", "events", "documents")


def _snippet(text: str, needle: str, width: int = 90) -> str:
    """A short piece of ``text`` around the first ``needle``, on one line."""
    flat = " ".join(str(text or "").split())
    at = flat.lower().find(needle.lower())
    if len(flat) <= width or at < 0:
        return flat[:width] + ("..." if len(flat) > width else "")
    start = max(0, at - width // 3)
    piece = flat[start : start + width]
    return (
        ("..." if start else "") + piece + ("..." if start + width < len(flat) else "")
    )


def search(
    connection: sqlite3.Connection, me: dict[str, Any], query: Any, *, limit: Any = 6
) -> dict[str, Any]:
    """The person's own goals, habits, journal days, timeline events and documents that match ``query``, up to
    ``limit`` of each, those whose name matches first. A document's number and scan are never searched."""
    needle = kit.text(query, "the search", max_len=80)
    each = kit.integer(limit, "the limit", default=6, minimum=1, maximum=50)
    empty: dict[str, Any] = {kind: [] for kind in SEARCH_KINDS}
    if not needle:
        return {"query": "", **empty, "total": 0}
    # "50%" or "my_file" are words to find, not LIKE patterns: "!" escapes them.
    like = "%" + needle.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    now = today(connection)

    def rows(sql: str, columns: int) -> list[dict[str, Any]]:
        return kit.fetch_all(
            connection, sql, (me["id"], *([like] * columns), like, each)
        )

    goals = rows(
        "SELECT id, title, category, status, progress, target_date FROM jr_goals WHERE owner_id = ? "
        "AND (title LIKE ? ESCAPE '!' OR description LIKE ? ESCAPE '!') "
        "ORDER BY CASE WHEN title LIKE ? ESCAPE '!' THEN 0 ELSE 1 END, "
        "CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'completed' THEN 2 ELSE 3 END, created_at DESC LIMIT ?",
        2,
    )
    habits = kit.fetch_all(
        connection,
        "SELECT id, title, per_week, archived FROM jr_habits WHERE owner_id = ? AND title LIKE ? ESCAPE '!' "
        "ORDER BY archived, created_at LIMIT ?",
        (me["id"], like, each),
    )
    entries = rows(
        "SELECT id, day, mood, body, grateful FROM jr_journal WHERE owner_id = ? "
        "AND (body LIKE ? ESCAPE '!' OR grateful LIKE ? ESCAPE '!') "
        "ORDER BY CASE WHEN body LIKE ? ESCAPE '!' THEN 0 ELSE 1 END, day DESC LIMIT ?",
        2,
    )
    events = rows(
        "SELECT id, title, event_date, kind, location FROM jr_events WHERE owner_id = ? "
        "AND (title LIKE ? ESCAPE '!' OR description LIKE ? ESCAPE '!' OR location LIKE ? ESCAPE '!') "
        "ORDER BY CASE WHEN title LIKE ? ESCAPE '!' THEN 0 ELSE 1 END, event_date DESC LIMIT ?",
        3,
    )
    documents = rows(
        "SELECT id, title, kind, issuer, expires_on FROM jr_documents WHERE owner_id = ? "
        "AND (title LIKE ? ESCAPE '!' OR issuer LIKE ? ESCAPE '!' OR tags LIKE ? ESCAPE '!' OR notes LIKE ? ESCAPE '!') "
        "ORDER BY CASE WHEN title LIKE ? ESCAPE '!' THEN 0 ELSE 1 END, title LIMIT ?",
        4,
    )
    for habit in habits:
        habit["archived"] = bool(habit["archived"])
    for entry in entries:
        mood = int(entry["mood"] or 0)
        entry["mood_label"] = MOODS.get(mood, "")
        body = entry.pop("body") or ""
        grateful = entry.pop("grateful") or ""
        entry["snippet"] = _snippet(
            body if needle.lower() in body.lower() else grateful, needle
        )
    for document in documents:
        document["state"] = expiry_state(document["expires_on"], now)
        document["days_left"] = days_until(document["expires_on"], now)
    found = {
        "goals": goals,
        "habits": habits,
        "entries": entries,
        "events": events,
        "documents": documents,
    }
    return {
        "query": needle,
        **found,
        "total": sum(len(items) for items in found.values()),
    }
