"""Planner selling: earn money from things the household no longer needs (a pram, a bike, the old sofa), and
from side jobs done for neighbours (babysitting, dog walking, ironing, gardening, tutoring).

Adults list a thing with a price; one public link shows everything for sale, and a neighbour or a friend taps
**I want this** with their name and a phone number or email (and an offer if they like). The adult says yes
(the thing is kept for that buyer) or no, and marks it **Sold** when the money is in: the sale is added to the
budget as money in, and to the earnings Aira adds up across apps. Aira does not take payments: buyer and seller
settle in person or however they agree.

A side job is offered on the same page, priced per job or per hour. Neighbours ask for it the same way; saying
yes books them (the job stays on offer for others), and each job done is recorded with what it earned: money in
for the budget and the earnings, once per job. Money from a sale or a job can go straight toward a savings goal.

Money is for adults (children cannot list things or see offers), and a visitor sees only what is for sale:
never offers, other buyers' details or what something sold for.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from aira.suite import kit, life, public
from aira.suite.planner import goals, service, store

APP = store.APP
KIND = "for_sale"
REF = "household"  # one sale page per household
CONDITIONS = ("new", "like_new", "good", "fair", "for_parts")
CONDITION_NAMES = {
    "new": "New",
    "like_new": "Like new",
    "good": "Good",
    "fair": "Fair",
    "for_parts": "For parts or repair",
}
INCOME_CATEGORY = "Selling"
JOB_CATEGORY = "Side jobs"
KINDS = ("thing", "service")
UNITS = ("job", "hour")
UNIT_NAMES = {"job": "a job", "hour": "an hour"}
MAX_NEW_REQUESTS = 30  # per thing: enough for a popular item, not a flood
MAX_HOURS = 744.0  # a month of hours on one job record


def _adult(me: dict[str, Any]) -> None:
    if not life.is_adult(me):
        raise PermissionError("Selling is for adults.")


def settings(connection: sqlite3.Connection) -> dict[str, str]:
    stored = kit.get_setting(connection, APP, "sale", {}) or {}
    return {
        "title": str(stored.get("title") or "Things for sale"),
        "note": str(stored.get("note") or ""),
    }


def _page_title(connection: sqlite3.Connection, things: int, jobs: int) -> str:
    """The page's title: the household's own, or one that says what is on it."""
    stored = kit.get_setting(connection, APP, "sale", {}) or {}
    own = str(stored.get("title") or "")
    if own and own != "Things for sale":
        return own
    if jobs and not things:
        return "Jobs we can do"
    if jobs and things:
        return "Things for sale and jobs we can do"
    return "Things for sale"


def save_settings(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, str]:
    _adult(me)
    values = {
        "title": kit.text(raw.get("title"), "the page title", max_len=80)
        or "Things for sale",
        "note": kit.text(
            raw.get("note"), "the note for buyers", max_len=500, multiline=True
        ),
    }
    kit.set_setting(connection, APP, "sale", values)
    link = public.link_for(connection, APP, KIND, REF)
    if link:
        public.create_link(connection, APP, KIND, REF, title=values["title"])
    connection.commit()
    return values


# ---------------------------------------------------------------------------
# things for sale
# ---------------------------------------------------------------------------


def _item(connection: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    return store.one(connection, "pn_sale_items", item_id, "thing")


def _request_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "contact": row["contact"],
        "message": row["message"],
        "offer_cents": row["offer_cents"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _job_view(
    row: dict[str, Any], everyone: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "item_id": row["item_id"],
        "customer": row["customer"],
        "hours": row["hours"],
        "amount_cents": row["amount_cents"],
        "day": row["day"],
        "note": row["note"],
        "done_by": str(everyone.get(row["done_by"], {}).get("name") or ""),
    }


def _item_view(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    everyone: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    service_item = row.get("kind") == "service"
    # A side job's finished requests (status done) are in its jobs, not waiting for an answer.
    requests = kit.fetch_all(
        connection,
        "SELECT * FROM pn_sale_requests WHERE item_id = ? AND status IN ('new', 'accepted') ORDER BY created_at",
        (row["id"],),
    )
    jobs = (
        kit.fetch_all(
            connection,
            "SELECT * FROM pn_jobs WHERE item_id = ? ORDER BY day DESC, created_at DESC, rowid DESC",
            (row["id"],),
        )
        if service_item
        else []
    )
    month = service.today(connection).strftime("%Y-%m")
    return {
        "id": row["id"],
        "kind": "service" if service_item else "thing",
        "price_unit": (row.get("price_unit") or "job") if service_item else "",
        "jobs": [_job_view(j, everyone) for j in jobs[:10]],
        "jobs_count": len(jobs),
        "earned_cents": sum(int(j["amount_cents"]) for j in jobs),
        "earned_this_month_cents": sum(
            int(j["amount_cents"]) for j in jobs if j["day"][:7] == month
        ),
        "title": row["title"],
        "description": row["description"],
        "condition": row["condition"],
        "condition_name": CONDITION_NAMES.get(row["condition"], row["condition"]),
        "price_cents": row["price_cents"],
        "status": row["status"],
        "buyer": row["buyer"],
        "sold_cents": row["sold_cents"],
        "sold_at": row["sold_at"],
        "listed_by": str(everyone.get(row["created_by"], {}).get("name") or ""),
        "created_at": row["created_at"],
        "requests": [_request_view(r) for r in requests],
        "new_requests": sum(1 for r in requests if r["status"] == "new"),
    }


def overview(
    connection: sqlite3.Connection, me: dict[str, Any], *, status: str = "", q: str = ""
) -> dict[str, Any]:
    _adult(me)
    status = kit.choice(
        status,
        "the status",
        ("available", "reserved", "sold", "services", "all"),
        default="all",
    )
    q = kit.text(q, "the search", max_len=80)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_sale_items WHERE deleted_at IS NULL ORDER BY CASE status WHEN 'reserved' THEN 0 WHEN 'available' THEN 1 ELSE 2 END, created_at DESC, rowid DESC",
    )
    everyone = service.people(connection)
    month = service.today(connection).strftime("%Y-%m")
    # A sold thing taken off the list still earned its money: it counts (as it does in the budget).
    sold = kit.fetch_all(
        connection,
        "SELECT sold_cents, sold_at FROM pn_sale_items WHERE status = 'sold'",
    )
    things = [r for r in rows if r.get("kind") != "service"]
    offered = [r for r in rows if r.get("kind") == "service"]
    if status == "services":
        shown = offered
    elif status == "all":
        shown = rows
    else:
        shown = [r for r in things if r["status"] == status]
    if q:
        needle = q.lower()
        shown = [
            r
            for r in shown
            if needle in r["title"].lower() or needle in r["description"].lower()
        ]
    items = [_item_view(connection, r, everyone) for r in shown]
    # Jobs of side jobs since removed still count: the money was earned.
    jobs = kit.fetch_all(connection, "SELECT amount_cents, day FROM pn_jobs")
    sold_month = sum(
        int(r["sold_cents"]) for r in sold if (r["sold_at"] or "")[:7] == month
    )
    jobs_month = sum(int(j["amount_cents"]) for j in jobs if j["day"][:7] == month)
    return {
        "currency": kit.business(connection)["currency"],
        "items": items,
        "earned_this_month_cents": sold_month + jobs_month,
        "earned_total_cents": sum(int(r["sold_cents"]) for r in sold)
        + sum(int(j["amount_cents"]) for j in jobs),
        "jobs_this_month_cents": jobs_month,
        "jobs_done": len(jobs),
        "for_sale": sum(1 for r in things if r["status"] == "available"),
        "reserved": sum(1 for r in things if r["status"] == "reserved"),
        "sold": len(sold),
        "services": sum(1 for r in offered if r["status"] == "available"),
        "new_requests": new_request_count(connection),
        "asking_cents": sum(
            int(r["price_cents"]) for r in things if r["status"] != "sold"
        ),
        "settings": settings(connection),
        "link": public.link_for(connection, APP, KIND, REF),
        "conditions": [[c, CONDITION_NAMES[c]] for c in CONDITIONS],
        "units": [[u, UNIT_NAMES[u]] for u in UNITS],
        "goals": goals.choices(connection),
    }


def earned_this_month(connection: sqlite3.Connection) -> int:
    """Money in this month from things sold and side jobs done (for My day)."""
    month = service.today(connection).strftime("%Y-%m")
    return kit.count(
        connection,
        "SELECT COALESCE(SUM(sold_cents), 0) FROM pn_sale_items WHERE status = 'sold' AND substr(sold_at, 1, 7) = ?",
        (month,),
    ) + kit.count(
        connection,
        "SELECT COALESCE(SUM(amount_cents), 0) FROM pn_jobs WHERE substr(day, 1, 7) = ?",
        (month,),
    )


def new_request_count(connection: sqlite3.Connection) -> int:
    return kit.count(
        connection,
        """SELECT COUNT(*) FROM pn_sale_requests r JOIN pn_sale_items i ON i.id = r.item_id
        WHERE r.status = 'new' AND i.deleted_at IS NULL AND i.status = 'available'""",
    )


def new_requests(
    connection: sqlite3.Connection, limit: int = 10
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        """SELECT r.*, i.title AS item_title, i.kind AS item_kind FROM pn_sale_requests r JOIN pn_sale_items i ON i.id = r.item_id
        WHERE r.status = 'new' AND i.deleted_at IS NULL AND i.status = 'available' ORDER BY r.created_at LIMIT ?""",
        (limit,),
    )


def save_item(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    item_id: str = "",
) -> dict[str, Any]:
    _adult(me)
    current = _item(connection, item_id) if item_id else None
    merged = {**(current or {}), **raw}
    kind = kit.choice(merged.get("kind"), "what it is", KINDS, default="thing")
    if current is not None and kind != (current.get("kind") or "thing"):
        raise ValueError(
            "A thing for sale cannot become a side job (or the other way). Add a new one instead."
        )
    if current is not None and current["status"] == "sold":
        raise ValueError("That is sold. Use Not sold after all first to change it.")
    values = {
        "kind": kind,
        "price_unit": kit.choice(
            merged.get("price_unit") or "job", "the price per", UNITS, default="job"
        )
        if kind == "service"
        else "",
        "title": kit.text(
            merged.get("title"),
            "the job you offer" if kind == "service" else "what you are selling",
            max_len=100,
            required=True,
        ),
        "description": kit.text(
            merged.get("description"), "the description", max_len=1000, multiline=True
        ),
        "condition": kit.choice(
            merged.get("condition"), "the condition", CONDITIONS, default="good"
        ),
        "price_cents": kit.cents_value(
            merged.get("price_cents"), "the price", default=0
        ),
    }
    stamp = kit.now()
    if item_id:
        kit.update_row(
            connection, "pn_sale_items", item_id, {**values, "updated_at": stamp}
        )
    else:
        item_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_sale_items (id, kind, price_unit, title, description, condition, price_cents, status, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'available', ?, ?, ?)",
            (
                item_id,
                values["kind"],
                values["price_unit"],
                values["title"],
                values["description"],
                values["condition"],
                values["price_cents"],
                me["id"],
                stamp,
                stamp,
            ),
        )
        kit.log(
            connection,
            APP,
            "for_sale",
            f"{me['name']} offered a side job: {values['title']}"
            if kind == "service"
            else f"{me['name']} put up for sale: {values['title']}",
            ref_type="sale_item",
            ref_id=item_id,
            amount_cents=values["price_cents"],
            actor=me["name"],
        )
    connection.commit()
    return _item_view(
        connection, _item(connection, item_id), service.people(connection)
    )


def delete_item(
    connection: sqlite3.Connection, me: dict[str, Any], item_id: str
) -> None:
    """Take a thing off the list. A sold thing's money stays in the budget and in the earnings."""
    _adult(me)
    _item(connection, item_id)
    stamp = kit.now()
    kit.update_row(
        connection, "pn_sale_items", item_id, {"deleted_at": stamp, "updated_at": stamp}
    )
    connection.commit()


def decide(
    connection: sqlite3.Connection, me: dict[str, Any], request_id: str, accept: bool
) -> dict[str, Any]:
    """Yes: the thing is kept for this buyer (others who asked stay waiting, in case it falls through).
    No: the request is closed."""
    _adult(me)
    row = kit.fetch_one(
        connection, "SELECT * FROM pn_sale_requests WHERE id = ?", (request_id,)
    )
    if not row:
        raise LookupError("That request was not found.")
    item = _item(connection, row["item_id"])
    if item["status"] == "sold":
        raise ValueError("That is already sold.")
    stamp = kit.now()
    if item.get("kind") == "service":
        if row["status"] == "done":
            raise ValueError("That job is done and recorded.")
        connection.execute(
            "UPDATE pn_sale_requests SET status = ?, decided_at = ? WHERE id = ?",
            ("accepted" if accept else "declined", stamp, request_id),
        )
        connection.commit()
        return _item_view(
            connection, _item(connection, item["id"]), service.people(connection)
        )
    if accept:
        connection.execute(
            "UPDATE pn_sale_requests SET status = 'new', decided_at = NULL WHERE item_id = ? AND status = 'accepted' AND id != ?",
            (item["id"], request_id),
        )
        connection.execute(
            "UPDATE pn_sale_requests SET status = 'accepted', decided_at = ? WHERE id = ?",
            (stamp, request_id),
        )
        kit.update_row(
            connection,
            "pn_sale_items",
            item["id"],
            {"status": "reserved", "buyer": row["name"], "updated_at": stamp},
        )
    else:
        connection.execute(
            "UPDATE pn_sale_requests SET status = 'declined', decided_at = ? WHERE id = ?",
            (stamp, request_id),
        )
        if row["status"] == "accepted":
            kit.update_row(
                connection,
                "pn_sale_items",
                item["id"],
                {"status": "available", "buyer": "", "updated_at": stamp},
            )
    connection.commit()
    return _item_view(
        connection, _item(connection, item["id"]), service.people(connection)
    )


def put_back(
    connection: sqlite3.Connection, me: dict[str, Any], item_id: str
) -> dict[str, Any]:
    """The buyer did not come: the thing is for sale again."""
    _adult(me)
    item = _item(connection, item_id)
    if item["status"] == "paused":
        kit.update_row(
            connection,
            "pn_sale_items",
            item_id,
            {"status": "available", "updated_at": kit.now()},
        )
        connection.commit()
    if item["status"] == "reserved":
        connection.execute(
            "UPDATE pn_sale_requests SET status = 'new', decided_at = NULL WHERE item_id = ? AND status = 'accepted'",
            (item_id,),
        )
        kit.update_row(
            connection,
            "pn_sale_items",
            item_id,
            {"status": "available", "buyer": "", "updated_at": kit.now()},
        )
        connection.commit()
    return _item_view(
        connection, _item(connection, item_id), service.people(connection)
    )


def pause(
    connection: sqlite3.Connection, me: dict[str, Any], item_id: str
) -> dict[str, Any]:
    """Not taking side jobs for now: it leaves the public page (its jobs and requests stay)."""
    _adult(me)
    item = _item(connection, item_id)
    if item.get("kind") != "service":
        raise ValueError("Only a side job can be paused. Remove a thing instead.")
    if item["status"] == "available":
        kit.update_row(
            connection,
            "pn_sale_items",
            item_id,
            {"status": "paused", "updated_at": kit.now()},
        )
        connection.commit()
    return _item_view(
        connection, _item(connection, item_id), service.people(connection)
    )


def _goal_choice(connection: sqlite3.Connection, raw: dict[str, Any]) -> str:
    goal_id = kit.text(raw.get("goal_id"), "the goal", max_len=32)
    if goal_id:
        store.one(connection, "pn_goals", goal_id, "goal")
    return goal_id


def mark_sold(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """The money is in: one income entry in the budget and one earnings line, however often it is pressed."""
    _adult(me)
    item = _item(connection, item_id)
    if item.get("kind") == "service":
        raise ValueError("A side job is not sold: record each job when it is done.")
    if item["status"] == "sold":
        return _item_view(connection, item, service.people(connection))
    goal_id = _goal_choice(connection, raw)
    accepted = kit.fetch_one(
        connection,
        "SELECT * FROM pn_sale_requests WHERE item_id = ? AND status = 'accepted' ORDER BY decided_at DESC",
        (item_id,),
    )
    default_amount = int(item["price_cents"])
    if accepted and int(accepted["offer_cents"] or 0) > 0:
        default_amount = int(accepted["offer_cents"])
    amount = kit.cents_value(
        raw.get("sold_cents"), "what it sold for", default=default_amount
    )
    buyer = (
        kit.text(raw.get("buyer"), "the buyer", max_len=60)
        or item["buyer"]
        or (accepted["name"] if accepted else "")
    )
    on, stamp = service.today(connection), kit.now()
    entry_id = ""
    if amount > 0:
        entry_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_entries (id, kind, amount_cents, day, category, note, profile_id, created_at) VALUES (?, 'income', ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                amount,
                on.isoformat(),
                INCOME_CATEGORY,
                f"Sold: {item['title']}"[:200],
                me["id"],
                stamp,
            ),
        )
        kit.record_revenue(
            connection,
            APP,
            source_type="sale_item",
            source_id=item_id,
            amount_cents=amount,
            label=f"Sold: {item['title']}",
            customer=buyer,
        )
        if goal_id:
            goals.add_from(
                connection,
                me,
                goal_id,
                amount,
                "sale_item",
                item_id,
                f"Sold: {item['title']}",
            )
    kit.update_row(
        connection,
        "pn_sale_items",
        item_id,
        {
            "status": "sold",
            "buyer": buyer,
            "sold_cents": amount,
            "sold_at": stamp,
            "entry_id": entry_id,
            "updated_at": stamp,
        },
    )
    connection.execute(
        "UPDATE pn_sale_requests SET status = 'declined', decided_at = ? WHERE item_id = ? AND status = 'new'",
        (stamp, item_id),
    )
    kit.log(
        connection,
        APP,
        "sold",
        f"Sold {item['title']}" + (f" to {buyer}" if buyer else ""),
        ref_type="sale_item",
        ref_id=item_id,
        amount_cents=amount,
        actor=me["name"],
    )
    connection.commit()
    return _item_view(
        connection, _item(connection, item_id), service.people(connection)
    )


def unsell(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    *,
    removed_too: bool = False,
) -> dict[str, Any]:
    """Marked sold by mistake (or the sale fell through): the money leaves the budget, the earnings and any
    savings goal, and the thing is for sale again. ``removed_too``: also for a thing taken off the list (its
    budget entry removed), whose money must stop counting as well."""
    _adult(me)
    item = (
        kit.fetch_one(
            connection, "SELECT * FROM pn_sale_items WHERE id = ?", (item_id,)
        )
        if removed_too
        else None
    ) or _item(connection, item_id)
    if item["status"] != "sold":
        return _item_view(connection, item, service.people(connection))
    goals.remove_from(connection, "sale_item", item_id)  # first: it may refuse
    if item["entry_id"]:
        connection.execute("DELETE FROM pn_entries WHERE id = ?", (item["entry_id"],))
    kit.void_revenue(connection, APP, "sale_item", item_id)
    kit.update_row(
        connection,
        "pn_sale_items",
        item_id,
        {
            "status": "available",
            "buyer": "",
            "sold_cents": 0,
            "sold_at": None,
            "entry_id": "",
            "updated_at": kit.now(),
        },
    )
    connection.execute(
        "UPDATE pn_sale_requests SET status = 'new', decided_at = NULL WHERE item_id = ? AND status = 'accepted'",
        (item_id,),
    )
    connection.commit()
    return _item_view(
        connection,
        kit.fetch_one(
            connection, "SELECT * FROM pn_sale_items WHERE id = ?", (item_id,)
        )
        or item,
        service.people(connection),
    )


def _noon(day: str) -> str:
    return f"{day}T12:00:00Z"


def _revenue_at(connection: sqlite3.Connection, day: str) -> str:
    """When the earnings line is dated: now for today, otherwise the chosen day (so Planner and Aira's
    earnings put it in the same month)."""
    return kit.now() if day == service.today(connection).isoformat() else _noon(day)


def _not_ahead(connection: sqlite3.Connection, day: str) -> None:
    if day > service.today(connection).isoformat():
        raise ValueError("Record a job on the day it was done: today or a day before.")


def entry_changed(
    connection: sqlite3.Connection, entry: dict[str, Any], values: dict[str, Any]
) -> None:
    """A Selling or Side jobs budget entry was changed (its amount or day): the sale or job, Aira's earnings
    line and any savings goal follow, so every place shows the same money. The caller commits."""
    amount, day = int(values["amount_cents"]), str(values["day"])
    job = kit.fetch_one(
        connection, "SELECT * FROM pn_jobs WHERE entry_id = ?", (entry["id"],)
    )
    sale = (
        None
        if job
        else kit.fetch_one(
            connection,
            "SELECT * FROM pn_sale_items WHERE entry_id = ? AND status = 'sold'",
            (entry["id"],),
        )
    )
    if not job and not sale:
        return
    if values["kind"] != "income":
        raise ValueError(
            "This is money in from a side job. To take it back, use Undo on the job in Sell."
            if job
            else "This is money in from something you sold. To take it back, use Not sold after all in Sell."
        )
    moved = day != entry["day"]
    if job:
        if moved:
            _not_ahead(connection, day)
        goals.change_from(connection, "job", job["id"], amount)
        connection.execute(
            "UPDATE pn_jobs SET amount_cents = ?, day = ? WHERE id = ?",
            (amount, day, job["id"]),
        )
        source_type, source_id = "job", job["id"]
    else:
        assert sale is not None
        goals.change_from(connection, "sale_item", sale["id"], amount)
        changes: dict[str, Any] = {"sold_cents": amount, "updated_at": kit.now()}
        if moved:
            changes["sold_at"] = _noon(day)
        kit.update_row(connection, "pn_sale_items", sale["id"], changes)
        source_type, source_id = "sale_item", sale["id"]
    # Aira's earnings line keeps its label and buyer; only the money (and the day, when it moved) change.
    connection.execute(
        "UPDATE suite_revenue SET amount_cents = ? WHERE app = ? AND source_type = ? AND source_id = ? AND voided_at IS NULL",
        (amount, APP, source_type, source_id),
    )
    if moved:
        connection.execute(
            "UPDATE suite_revenue SET at = ? WHERE app = ? AND source_type = ? AND source_id = ?",
            (_noon(day), APP, source_type, source_id),
        )


# ---------------------------------------------------------------------------
# side jobs done
# ---------------------------------------------------------------------------


def record_job(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """A side job done and paid: one income entry (Side jobs), one earnings line and, when chosen, money toward a
    savings goal. With ``request_id`` the neighbour's request is closed as done."""
    _adult(me)
    item = _item(connection, item_id)
    if item.get("kind") != "service":
        raise ValueError("Record jobs for a side job. A thing is marked Sold instead.")
    request = None
    request_id = kit.text(raw.get("request_id"), "the request", max_len=32)
    if request_id:
        request = kit.fetch_one(
            connection,
            "SELECT * FROM pn_sale_requests WHERE id = ? AND item_id = ?",
            (request_id, item_id),
        )
        if not request:
            raise LookupError("That request was not found.")
        if request["status"] == "done":
            raise ValueError("That job is already recorded.")
    try:
        hours = float(raw.get("hours") or 0)
    except (TypeError, ValueError):
        raise ValueError("Write the hours as a number, like 2 or 1.5.") from None
    if not math.isfinite(hours) or hours < 0 or hours > MAX_HOURS:
        raise ValueError("Write the hours as a number, like 2 or 1.5.")
    if (
        item["price_unit"] == "hour"
        and not hours
        and raw.get("amount_cents")
        in (
            None,
            "",
        )
    ):
        raise ValueError("How many hours was it?")
    price = int(item["price_cents"])
    default = round(price * hours) if item["price_unit"] == "hour" else price
    amount = kit.cents_value(
        raw.get("amount_cents"), "what you were paid", default=default
    )
    customer = kit.text(raw.get("customer"), "who it was for", max_len=60) or (
        request["name"] if request else ""
    )
    day = kit.day(raw.get("day"), "the day") or service.today(connection).isoformat()
    _not_ahead(connection, day)
    note = kit.text(raw.get("note"), "a note", max_len=200)
    goal_id = _goal_choice(connection, raw)
    job_id, stamp = kit.new_id(), kit.now()
    entry_id = ""
    label = f"Side job: {item['title']}" + (f" for {customer}" if customer else "")
    if amount > 0:
        entry_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_entries (id, kind, amount_cents, day, category, note, profile_id, created_at) VALUES (?, 'income', ?, ?, ?, ?, ?, ?)",
            (entry_id, amount, day, JOB_CATEGORY, label[:200], me["id"], stamp),
        )
        kit.record_revenue(
            connection,
            APP,
            source_type="job",
            source_id=job_id,
            amount_cents=amount,
            label=label,
            customer=customer,
            at=_revenue_at(connection, day),
        )
        if goal_id:
            goals.add_from(connection, me, goal_id, amount, "job", job_id, label)
    connection.execute(
        "INSERT INTO pn_jobs (id, item_id, request_id, customer, hours, amount_cents, day, note, entry_id, done_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            item_id,
            request_id,
            customer,
            hours,
            amount,
            day,
            note,
            entry_id,
            me["id"],
            stamp,
        ),
    )
    if request:
        connection.execute(
            "UPDATE pn_sale_requests SET status = 'done', decided_at = ? WHERE id = ?",
            (stamp, request_id),
        )
    kit.log(
        connection,
        APP,
        "job",
        label,
        ref_type="job",
        ref_id=job_id,
        amount_cents=amount,
        actor=me["name"],
    )
    connection.commit()
    return _job_view(
        kit.fetch_one(connection, "SELECT * FROM pn_jobs WHERE id = ?", (job_id,))
        or {},
        service.people(connection),
    )


def undo_job(connection: sqlite3.Connection, me: dict[str, Any], job_id: str) -> None:
    """Recorded by mistake: the money leaves the budget, the earnings and any goal, and the neighbour's request
    is booked again."""
    _adult(me)
    job = kit.fetch_one(connection, "SELECT * FROM pn_jobs WHERE id = ?", (job_id,))
    if not job:
        raise LookupError("That job was not found.")
    goals.remove_from(connection, "job", job_id)  # first: it may refuse
    if job["entry_id"]:
        connection.execute("DELETE FROM pn_entries WHERE id = ?", (job["entry_id"],))
    kit.void_revenue(connection, APP, "job", job_id)
    if job["request_id"]:
        connection.execute(
            "UPDATE pn_sale_requests SET status = 'accepted' WHERE id = ? AND status = 'done'",
            (job["request_id"],),
        )
    connection.execute("DELETE FROM pn_jobs WHERE id = ?", (job_id,))
    connection.commit()


def csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    data = overview(connection, me)
    rows = [
        {
            "thing": i["title"],
            "kind": "side job" if i["kind"] == "service" else "thing",
            "condition": "" if i["kind"] == "service" else i["condition_name"],
            "price": service._amount(i["price_cents"])
            + (f" per {i['price_unit']}" if i["kind"] == "service" else ""),
            "status": i["status"],
            "buyer": i["buyer"],
            "sold_for": service._amount(i["sold_cents"])
            if i["status"] == "sold"
            else "",
            "sold_on": (i["sold_at"] or "")[:10],
            "jobs_done": i["jobs_count"] if i["kind"] == "service" else "",
            "earned": service._amount(i["earned_cents"])
            if i["kind"] == "service"
            else "",
            "currency": data["currency"],
        }
        for i in data["items"]
    ]
    return kit.csv_bytes(
        rows,
        (
            "thing",
            "kind",
            "condition",
            "price",
            "status",
            "buyer",
            "sold_for",
            "sold_on",
            "jobs_done",
            "earned",
            "currency",
        ),
    )


# ---------------------------------------------------------------------------
# the public page
# ---------------------------------------------------------------------------


def public_page(connection: sqlite3.Connection) -> dict[str, Any]:
    """Only what a buyer may see: what is for sale (or kept for someone), its price and condition."""
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_sale_items WHERE deleted_at IS NULL AND status IN ('available', 'reserved') ORDER BY created_at DESC, rowid DESC LIMIT 200",
    )
    jobs = sum(1 for r in rows if r.get("kind") == "service")
    return {
        **settings(connection),
        "title": _page_title(connection, len(rows) - jobs, jobs),
        "currency": kit.business(connection)["currency"],
        "items": [
            {
                "id": r["id"],
                "kind": "service" if r.get("kind") == "service" else "thing",
                "price_unit": (r.get("price_unit") or "job")
                if r.get("kind") == "service"
                else "",
                "title": r["title"],
                "description": r["description"],
                "condition": ""
                if r.get("kind") == "service"
                else CONDITION_NAMES.get(r["condition"], r["condition"]),
                "price_cents": r["price_cents"],
                "reserved": r["status"] == "reserved",
            }
            for r in rows
        ],
    }


def _contact(value: Any) -> str:
    text = kit.text(value, "a phone number or email", max_len=120, required=True)
    try:
        if "@" in text:
            return kit.email(text, "your email")
        return kit.phone(text, "your phone number")
    except ValueError:
        raise ValueError(
            "Write a phone number (like 07700 900123) or an email (like sam@example.com) so they can reach you."
        ) from None


def ask(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """A visitor wants to buy something. Asking twice with the same contact changes the first request."""
    item_id = kit.text(payload.get("item_id"), "the thing", max_len=32, required=True)
    item = _item(connection, item_id)
    if item["status"] == "sold":
        raise LookupError("Sorry, that has been sold.")
    if item["status"] == "reserved":
        raise ValueError("Sorry, that is kept for someone else for now.")
    if item["status"] == "paused":
        raise ValueError("Sorry, they are not taking this job right now.")
    name = kit.text(payload.get("name"), "your name", max_len=60, required=True)
    contact = _contact(payload.get("contact"))
    message = kit.text(
        payload.get("message"), "your message", max_len=500, multiline=True
    )
    offer = (
        0
        if item.get("kind") == "service"
        else kit.cents(payload.get("offer"), "your offer", default=0)
    )
    stamp = kit.now()
    mine = kit.fetch_one(
        connection,
        "SELECT id FROM pn_sale_requests WHERE item_id = ? AND contact = ? AND status = 'new'",
        (item_id, contact),
    )
    if mine:
        kit.update_row(
            connection,
            "pn_sale_requests",
            mine["id"],
            {
                "name": name,
                "message": message,
                "offer_cents": offer,
                "created_at": stamp,
            },
        )
        connection.commit()
        return {"id": mine["id"], "status": "new"}
    waiting = kit.count(
        connection,
        "SELECT COUNT(*) FROM pn_sale_requests WHERE item_id = ? AND status = 'new'",
        (item_id,),
    )
    if waiting >= MAX_NEW_REQUESTS:
        raise ValueError("Lots of people have asked for this already. Try again later.")
    request_id = kit.new_id()
    connection.execute(
        "INSERT INTO pn_sale_requests (id, item_id, name, contact, message, offer_cents, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'new', ?)",
        (request_id, item_id, name, contact, message, offer, stamp),
    )
    kit.log(
        connection,
        APP,
        "sale_request",
        f"{name} asked about {item['title']}"
        if item.get("kind") == "service"
        else f"{name} wants to buy {item['title']}",
        ref_type="sale_item",
        ref_id=item_id,
        amount_cents=offer,
        public=True,
    )
    connection.commit()
    return {"id": request_id, "status": "new"}
