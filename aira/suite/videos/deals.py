"""Brand deals: a creator's sponsorships, from the first message to the money received.

A deal is the creator's own record (no one else sees it): the brand, who to talk to, the amount and currency, the
video it is for, a due date and notes. It moves through **proposed**, **agreed**, **delivered** (the video is out)
and **paid**, or **cancelled**. Linking a deal to a video marks that video "Includes paid promotion" for viewers, as
the law and fairness ask (the creator can also tick it under Edit). The label a deal turned on goes off again when
the deal is moved to another video, unlinked, cancelled or deleted, unless another deal still links that video.
Marking a deal paid records the money on the day it arrived (``paid_on``, today when it is not given; on this
computer it joins the Business hub's revenue); moving it back from paid takes it out again. Deals are for adults.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from typing import Any

from aira.suite import kit
from aira.suite.videos import creator, service, store
from aira.suite.videos.store import new_id, row, rows, stamp

STATUSES = ("proposed", "agreed", "delivered", "paid", "cancelled")
OPEN = ("proposed", "agreed", "delivered")
MAX_DEALS = 1000


def _adult(ch: dict[str, Any]) -> None:
    if not service.adult(ch):
        raise PermissionError("Brand deals are for adults.")


def _deal(sc: sqlite3.Connection, ch: dict[str, Any], deal_id: str) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM vi_deals WHERE id = ? AND channel_id = ?",
        (store.check_id(deal_id, "deal"), ch["id"]),
    )
    if not found:
        raise LookupError("That deal was not found.")
    return found


def view(sc: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    video = (
        row(
            sc,
            "SELECT id, title FROM vi_videos WHERE id = ? AND deleted_at IS NULL",
            (item["video_id"],),
        )
        if item["video_id"]
        else None
    )
    today = store.day()
    return {
        "id": item["id"],
        "brand": item["brand"],
        "contact": item["contact"],
        "amount_cents": item["amount_cents"],
        "currency": item["currency"],
        "amount": kit.fmt_money(item["amount_cents"], item["currency"]),
        "status": item["status"],
        "due_on": item["due_on"],
        "overdue": bool(
            item["due_on"] and item["due_on"] < today and item["status"] in OPEN
        ),
        "notes": item["notes"],
        "paid_at": item["paid_at"],
        "paid_on": str(item["paid_at"] or "")[:10],
        "video": {"id": video["id"], "title": video["title"]} if video else None,
        "video_id": video["id"] if video else "",
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _values(
    sc: sqlite3.Connection,
    ch: dict[str, Any],
    raw: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    merged = {**current, **raw}
    values = {
        "brand": kit.text(merged.get("brand"), "the brand", max_len=120, required=True),
        "contact": kit.text(merged.get("contact"), "who to talk to", max_len=200),
        "amount_cents": kit.cents_value(
            merged.get("amount_cents"), "the amount", default=0
        ),
        "currency": kit.currency(merged.get("currency"), default="USD"),
        "status": kit.choice(
            merged.get("status"), "the status", STATUSES, default="proposed"
        ),
        "due_on": kit.day(merged.get("due_on"), "the due date"),
        "notes": kit.text(merged.get("notes"), "notes", max_len=2000, multiline=True),
    }
    if values["amount_cents"] > 100_000_000:
        raise ValueError("The amount is too large.")
    video_id = str(merged.get("video_id") or "")
    if video_id:
        service.own_video(sc, ch, video_id)  # LookupError when it is not theirs
    values["video_id"] = video_id
    return values


def _paid_at(raw: dict[str, Any], current: str | None) -> str:
    """When a paid deal's money arrived: the day given (not in the future), or what it was, or now."""
    if "paid_on" in raw and raw.get("paid_on"):
        paid_on = kit.day(raw.get("paid_on"), "the day it was paid")
        if paid_on > store.day():
            raise ValueError("The day it was paid cannot be in the future.")
        if current and current[:10] == paid_on:
            return current
        return f"{paid_on}T12:00:00.000000Z"
    return current or stamp()


def _promote(sc: sqlite3.Connection, deal_id: str, video_id: str) -> None:
    """A video with a brand deal says so to its viewers; the deal remembers it turned the label on."""
    if not video_id:
        return
    video = row(sc, "SELECT paid_promotion FROM vi_videos WHERE id = ?", (video_id,))
    if video and not video["paid_promotion"]:
        sc.execute(
            "UPDATE vi_videos SET paid_promotion = 1, updated_at = ? WHERE id = ?",
            (stamp(), video_id),
        )
        sc.execute("UPDATE vi_deals SET promoted = 1 WHERE id = ?", (deal_id,))


def _release(sc: sqlite3.Connection, deal: dict[str, Any]) -> None:
    """The deal no longer links its video (moved, unlinked, cancelled or deleted): the label it turned on goes
    off, unless another deal still links the video (that deal keeps it on)."""
    if not deal.get("video_id") or not deal.get("promoted"):
        return
    other = row(
        sc,
        "SELECT id FROM vi_deals WHERE video_id = ? AND id != ? AND status != 'cancelled' LIMIT 1",
        (deal["video_id"], deal["id"]),
    )
    if other:
        sc.execute("UPDATE vi_deals SET promoted = 1 WHERE id = ?", (other["id"],))
    else:
        sc.execute(
            "UPDATE vi_videos SET paid_promotion = 0, updated_at = ? WHERE id = ?",
            (stamp(), deal["video_id"]),
        )
    sc.execute("UPDATE vi_deals SET promoted = 0 WHERE id = ?", (deal["id"],))


def create(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _adult(ch)
    service._active(ch)
    count = int(
        store.scalar(
            sc, "SELECT COUNT(*) FROM vi_deals WHERE channel_id = ?", (ch["id"],)
        )
        or 0
    )
    if count >= MAX_DEALS:
        raise ValueError(
            f"You can keep at most {MAX_DEALS} deals. Delete old ones first."
        )
    values = _values(sc, ch, raw, {})
    deal_id = new_id()
    moment = stamp()
    sc.execute(
        """INSERT INTO vi_deals (id, channel_id, brand, contact, amount_cents, currency, video_id, status, due_on, notes,
        paid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            deal_id,
            ch["id"],
            values["brand"],
            values["contact"],
            values["amount_cents"],
            values["currency"],
            values["video_id"],
            values["status"],
            values["due_on"],
            values["notes"],
            _paid_at(raw, None) if values["status"] == "paid" else None,
            moment,
            moment,
        ),
    )
    if values["status"] != "cancelled":
        _promote(sc, deal_id, values["video_id"])
    sc.commit()
    return view(sc, _deal(sc, ch, deal_id))


def update(
    sc: sqlite3.Connection, ch: dict[str, Any], deal_id: str, raw: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Change a deal; also returns its status before, so the caller can record or void the money."""
    _adult(ch)
    found = _deal(sc, ch, deal_id)
    values = _values(sc, ch, raw, found)
    paid_at = (
        _paid_at(raw, found["paid_at"] if found["status"] == "paid" else None)
        if values["status"] == "paid"
        else None
    )
    linked = values["status"] != "cancelled" and values["video_id"]
    if found["video_id"] and (
        found["video_id"] != values["video_id"] or values["status"] == "cancelled"
    ):
        _release(sc, found)
    store.update(
        sc,
        "vi_deals",
        found["id"],
        {**values, "paid_at": paid_at, "updated_at": stamp()},
    )
    if linked:
        _promote(sc, found["id"], values["video_id"])
    sc.commit()
    return view(sc, _deal(sc, ch, found["id"])), found["status"]


def delete(sc: sqlite3.Connection, ch: dict[str, Any], deal_id: str) -> dict[str, Any]:
    _adult(ch)
    found = _deal(sc, ch, deal_id)
    _release(sc, found)
    sc.execute("DELETE FROM vi_deals WHERE id = ?", (found["id"],))
    sc.commit()
    return found


def listing(
    sc: sqlite3.Connection, ch: dict[str, Any], status: str = "", q: str = ""
) -> dict[str, Any]:
    """The creator's deals (newest change first), with totals: waiting to be paid, paid this year, overdue."""
    _adult(ch)
    where, params = "channel_id = ?", [ch["id"]]
    if status == "open":
        where += " AND status IN ('proposed', 'agreed', 'delivered')"
    elif status:
        kit.choice(status, "the status", STATUSES)
        where += " AND status = ?"
        params.append(status)
    text = kit.text(q, "what to search for", max_len=100)
    if text:
        like = "%" + re.sub(r"([!%_])", r"!\1", text.lower()) + "%"
        where += " AND (lower(brand) LIKE ? ESCAPE '!' OR lower(contact) LIKE ? ESCAPE '!' OR lower(notes) LIKE ? ESCAPE '!')"
        params += [like, like, like]
    found = rows(
        sc,
        f"SELECT * FROM vi_deals WHERE {where} ORDER BY updated_at DESC LIMIT 500",  # noqa: S608 - fragments are ours
        tuple(params),
    )
    year = store.day()[:4]

    def money(sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [
            {**item, "amount": kit.fmt_money(int(item["cents"] or 0), item["currency"])}
            for item in rows(sc, sql, args)
        ]

    return {
        "deals": [view(sc, item) for item in found],
        "waiting": money(
            """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_deals WHERE channel_id = ?
            AND status IN ('agreed', 'delivered') GROUP BY currency ORDER BY cents DESC""",
            (ch["id"],),
        ),
        "paid_this_year": money(
            """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_deals WHERE channel_id = ?
            AND status = 'paid' AND paid_at >= ? GROUP BY currency ORDER BY cents DESC""",
            (ch["id"], f"{year}-01-01"),
        ),
        "overdue": int(
            store.scalar(
                sc,
                """SELECT COUNT(*) FROM vi_deals WHERE channel_id = ? AND due_on != '' AND due_on < ?
                AND status IN ('proposed', 'agreed', 'delivered')""",
                (ch["id"], store.day()),
            )
            or 0
        ),
        "statuses": list(STATUSES),
    }


CSV_COLUMNS = (
    ("brand", "Brand"),
    ("contact", "Contact"),
    ("status", "Status"),
    ("amount", "Amount"),
    ("currency", "Currency"),
    ("due_on", "Due"),
    ("paid_on", "Paid on"),
    ("video", "Video"),
    ("notes", "Notes"),
)


def as_csv(
    sc: sqlite3.Connection, ch: dict[str, Any], status: str = "", q: str = ""
) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([label for _, label in CSV_COLUMNS])
    for item in listing(sc, ch, status, q)["deals"]:
        values = {
            **item,
            "amount": f"{item['amount_cents'] / 100:.2f}",
            "paid_on": str(item["paid_at"] or "")[:10],
            "video": item["video"]["title"] if item["video"] else "",
        }
        writer.writerow([creator._cell(values[key]) for key, _ in CSV_COLUMNS])
    return out.getvalue().encode("utf-8-sig")


def due_lines(sc: sqlite3.Connection, ch: dict[str, Any]) -> list[dict[str, Any]]:
    """Open deals due today or overdue (Today reminds the creator)."""
    return rows(
        sc,
        """SELECT id, brand, due_on FROM vi_deals WHERE channel_id = ? AND due_on != '' AND due_on <= ?
        AND status IN ('proposed', 'agreed', 'delivered') ORDER BY due_on LIMIT 3""",
        (ch["id"], store.day()),
    )
