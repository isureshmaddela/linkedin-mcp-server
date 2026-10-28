"""Referral partners for Courses & Events: people who share a course or an event page with their own link
(``?ref=CODE``) and earn a commission on what their referrals pay.

A referral is recorded when someone enrols or orders through a partner's link. It earns its commission only when
the business marks the payment received (a free enrolment or free tickets count as a referral that earns
nothing), it is voided when the sale is refunded or cancelled, and it is paid when the business records a payout
(the business pays the partner itself, outside Aira, the same way customers pay it). Each partner has a page of
their own (public kind ``academy/partner``) with their links and what they earned, without customer names.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator, Mapping
from typing import Any

from aira.suite import kit, public

APP = "academy"  # the routes and the partner's page live under /api/academy (the Courses & Events app)
STATUSES = ("pending", "earned", "paid", "void")
SOURCES = {"enrolment": "academy", "order": "events"}
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS xp_partners (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        code TEXT NOT NULL UNIQUE,
        commission_percent INTEGER NOT NULL DEFAULT 10,
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS xp_referrals (
        id TEXT PRIMARY KEY,
        partner_id TEXT NOT NULL,
        app TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        item_type TEXT NOT NULL DEFAULT '',
        item_id TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        customer TEXT NOT NULL DEFAULT '',
        amount_cents INTEGER NOT NULL DEFAULT 0,
        commission_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        payout_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        earned_at TEXT,
        paid_at TEXT,
        UNIQUE (source_type, source_id)
    )""",
    "CREATE INDEX IF NOT EXISTS xp_referrals_partner ON xp_referrals (partner_id, status)",
    """CREATE TABLE IF NOT EXISTS xp_payouts (
        id TEXT PRIMARY KEY,
        partner_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        referrals INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS xp_payouts_partner ON xp_payouts (partner_id, created_at)",
)


def _v2(connection: sqlite3.Connection) -> Iterator[str]:
    """Each referral keeps the commission rate in force when the sale was made (older rows: NULL, the rate of
    their partner when they are paid)."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(xp_referrals)")}
    if "commission_percent" not in columns:
        yield "ALTER TABLE xp_referrals ADD COLUMN commission_percent INTEGER"


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "experiences.partners.v1", SCHEMA)
    kit.ensure(connection, "experiences.partners.v2", _v2(connection))


# ---------------------------------------------------------------------------
# partners
# ---------------------------------------------------------------------------


def ref_scope(connection: sqlite3.Connection) -> str:
    """A random name for this business that its public pages carry, so a visitor's partner code is kept for this
    business only (every hosted business shares one web address, and codes are chosen freely)."""
    ensure(connection)
    scope = str(kit.get_setting(connection, APP, "ref_scope") or "")
    if not scope:
        scope = secrets.token_hex(8)
        kit.set_setting(connection, APP, "ref_scope", scope)
        connection.commit()
    return scope


def normalize_code(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum() or ch in "-_")[
        :30
    ]


def _new_code(connection: sqlite3.Connection, name: str) -> str:
    stem = "".join(ch for ch in name.upper() if ch.isalnum())[:8] or "PARTNER"
    for _ in range(50):
        code = f"{stem}{''.join(secrets.choice(_CODE_ALPHABET) for _ in range(3))}"
        if not kit.fetch_one(
            connection, "SELECT 1 AS x FROM xp_partners WHERE code = ?", (code,)
        ):
            return code
    raise RuntimeError("Could not make a unique partner code.")


def _clean(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **{k: v for k, v in raw.items() if v is not None}}
    return {
        "name": kit.text(
            merged.get("name"), "the partner's name", max_len=120, required=True
        ),
        "email": kit.email(merged.get("email"), "the partner's email"),
        "code": normalize_code(merged.get("code")),
        "commission_percent": kit.integer(
            merged.get("commission_percent"),
            "the commission",
            default=10,
            minimum=0,
            maximum=90,
        ),
        "notes": kit.text(
            merged.get("notes"), "the notes", max_len=2000, multiline=True
        ),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }


def _check_code(
    connection: sqlite3.Connection, code: str, partner_id: str = ""
) -> None:
    if len(code) < 3:
        raise ValueError("The partner code needs at least 3 letters or digits.")
    clash = kit.fetch_one(
        connection,
        "SELECT id FROM xp_partners WHERE code = ? AND id != ?",
        (code, partner_id),
    )
    if clash:
        raise ValueError(f"Another partner already uses the code {code}.")


def create_partner(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    ensure(connection)
    values = _clean(raw)
    values["code"] = values["code"] or _new_code(connection, values["name"])
    _check_code(connection, values["code"])
    now = kit.now()
    values.update(id=kit.new_id(), created_at=now, updated_at=now)
    connection.execute(
        """INSERT INTO xp_partners (id, name, email, code, commission_percent, notes, active, created_at, updated_at)
        VALUES (:id, :name, :email, :code, :commission_percent, :notes, :active, :created_at, :updated_at)""",
        values,
    )
    kit.log(
        connection,
        APP,
        "partner",
        f"New partner: {values['name']} ({values['commission_percent']}% commission)",
        ref_type="partner",
        ref_id=values["id"],
    )
    connection.commit()
    return partner(connection, values["id"])


def update_partner(
    connection: sqlite3.Connection, partner_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = partner(connection, partner_id)
    values = _clean(raw, current)
    values["code"] = values["code"] or current["code"]
    _check_code(connection, values["code"], partner_id)
    kit.update_row(
        connection, "xp_partners", partner_id, {**values, "updated_at": kit.now()}
    )
    connection.commit()
    return partner(connection, partner_id)


def delete_partner(connection: sqlite3.Connection, partner_id: str) -> dict[str, Any]:
    """Delete a partner with no referrals; one with referrals is switched off so its history stays."""
    partner(connection, partner_id)
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM xp_referrals WHERE partner_id = ?",
        (partner_id,),
    ):
        kit.update_row(
            connection,
            "xp_partners",
            partner_id,
            {"active": 0, "updated_at": kit.now()},
        )
        public.revoke(connection, APP, "partner", partner_id)
        connection.commit()
        return {"deleted": False, "deactivated": True}
    public.revoke(connection, APP, "partner", partner_id)
    connection.execute("DELETE FROM xp_partners WHERE id = ?", (partner_id,))
    connection.commit()
    return {"deleted": True, "deactivated": False}


def partner(connection: sqlite3.Connection, partner_id: str) -> dict[str, Any]:
    ensure(connection)
    found = kit.require_row(connection, "xp_partners", partner_id, "partner")
    found["active"] = bool(found["active"])
    return found


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    def total(status: str, key: str = "commission_cents") -> int:
        return sum(int(row[key] or 0) for row in rows if row["status"] == status)

    live = [row for row in rows if row["status"] != "void"]
    return {
        "referrals": len(live),
        "sales_cents": sum(
            int(row["amount_cents"] or 0)
            for row in live
            if row["status"] in ("earned", "paid")
        ),
        "pending_cents": total("pending"),
        "owed_cents": total("earned"),
        "paid_cents": total("paid"),
        "earned_cents": total("earned") + total("paid"),
    }


def partners(connection: sqlite3.Connection, *, q: str = "") -> list[dict[str, Any]]:
    ensure(connection)
    sql, params = "SELECT * FROM xp_partners", []
    if q:
        like = f"%{kit.text(q, 'the search', max_len=100)}%"
        sql += " WHERE name LIKE ? OR email LIKE ? OR code LIKE ?"
        params = [like, like, like]
    rows = kit.fetch_all(
        connection, sql + " ORDER BY active DESC, name COLLATE NOCASE", params
    )
    by_partner: dict[str, list[dict[str, Any]]] = {}
    for referral in kit.fetch_all(connection, "SELECT * FROM xp_referrals"):
        by_partner.setdefault(referral["partner_id"], []).append(referral)
    for row in rows:
        row["active"] = bool(row["active"])
        row.update(_totals(by_partner.get(row["id"], [])))
        row["link"] = public.link_for(connection, APP, "partner", row["id"])
    return rows


def summary(connection: sqlite3.Connection) -> dict[str, int]:
    ensure(connection)
    totals = _totals(kit.fetch_all(connection, "SELECT * FROM xp_referrals"))
    totals["partners"] = kit.count(
        connection, "SELECT COUNT(*) FROM xp_partners WHERE active = 1"
    )
    return totals


def find_active(connection: sqlite3.Connection, code: Any) -> dict[str, Any] | None:
    clean = normalize_code(code)
    if not clean:
        return None
    ensure(connection)
    return kit.fetch_one(
        connection, "SELECT * FROM xp_partners WHERE code = ? AND active = 1", (clean,)
    )


def _same_person(found: Mapping[str, Any], customer_email: Any) -> bool:
    mine = str(found.get("email") or "").strip().lower()
    return bool(mine) and mine == str(customer_email or "").strip().lower()


def check_code(
    connection: sqlite3.Connection, code: Any, customer_email: Any = ""
) -> dict[str, Any] | None:
    """For a sale the business enters itself ('Enrol a student', 'Sell tickets'): a partner code that was typed
    must belong to a partner who is switched on, and not to the buyer. Raises ValueError so the form says so (a
    customer's own link never gets this check: a wrong code there never blocks a sale)."""
    typed = str(code or "").strip()
    if not typed:
        return None
    clean = normalize_code(typed)
    ensure(connection)
    found = (
        kit.fetch_one(connection, "SELECT * FROM xp_partners WHERE code = ?", (clean,))
        if clean
        else None
    )
    if not found:
        raise ValueError(
            f"No partner has the code {clean or typed}. Check it in Partners, or leave the partner code empty."
        )
    if not found["active"]:
        raise ValueError(
            f"{found['name']} ({found['code']}) is switched off. Switch them on in Partners, or leave the partner code empty."
        )
    if _same_person(found, customer_email):
        raise ValueError(
            f"That is {found['name']}'s own email. A partner cannot earn commission on their own purchase."
        )
    return found


def referrals(connection: sqlite3.Connection, partner_id: str) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT * FROM xp_referrals WHERE partner_id = ? ORDER BY created_at DESC LIMIT 1000",
        (partner_id,),
    )


def payouts(connection: sqlite3.Connection, partner_id: str) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT * FROM xp_payouts WHERE partner_id = ? ORDER BY created_at DESC LIMIT 200",
        (partner_id,),
    )


def detail(connection: sqlite3.Connection, partner_id: str) -> dict[str, Any]:
    found = partner(connection, partner_id)
    rows = referrals(connection, partner_id)
    return {
        # history counts refunded referrals too: a partner with any history is switched off, never deleted
        "partner": {**found, **_totals(rows), "history": len(rows)},
        "referrals": rows,
        "payouts": payouts(connection, partner_id),
        "link": public.link_for(connection, APP, "partner", partner_id),
        "share_links": share_links(connection),
    }


# ---------------------------------------------------------------------------
# referrals: recorded on a sale, earned on payment, voided on refund
# ---------------------------------------------------------------------------


def _commission(amount_cents: int, percent: int) -> int:
    return kit.percent_of(int(amount_cents), int(percent) * 100)


def record(
    connection: sqlite3.Connection,
    code: Any,
    *,
    source_type: str,
    source_id: str,
    item_type: str,
    item_id: str,
    label: str,
    customer: str,
    amount_cents: int,
    customer_email: str = "",
    paid: bool = False,
) -> dict[str, Any] | None:
    """Attribute a sale to the partner whose code the customer arrived with (an unknown or switched-off code is
    ignored: it never stops a sale). ``paid`` means the money is already in (a free sale earns nothing). A
    partner buying with their own code earns nothing: the sale is not counted and the activity log says so."""
    found = find_active(connection, code)
    if not found or source_type not in SOURCES:
        return None
    if _same_person(found, customer_email):
        kit.log(
            connection,
            SOURCES[source_type],
            "referral_skipped",
            f"Not counted as a referral: {found['name']} used their own partner code ({label})",
            ref_type="partner",
            ref_id=found["id"],
        )
        return None
    if kit.fetch_one(
        connection,
        "SELECT id FROM xp_referrals WHERE source_type = ? AND source_id = ?",
        (source_type, source_id),
    ):
        return None
    amount = max(0, int(amount_cents))
    earned = paid or amount == 0
    now = kit.now()
    referral = {
        "id": kit.new_id(),
        "partner_id": found["id"],
        "app": SOURCES[source_type],
        "source_type": source_type,
        "source_id": source_id,
        "item_type": item_type,
        "item_id": item_id,
        "label": label[:200],
        "customer": customer[:200],
        "amount_cents": amount,
        # While the sale waits for payment this is what the partner will earn once it is paid.
        "commission_cents": _commission(amount, found["commission_percent"]),
        "commission_percent": int(found["commission_percent"]),
        "status": "earned" if earned else "pending",
        "created_at": now,
        "earned_at": now if earned else None,
    }
    connection.execute(
        """INSERT INTO xp_referrals (id, partner_id, app, source_type, source_id, item_type, item_id, label, customer,
        amount_cents, commission_cents, commission_percent, status, created_at, earned_at)
        VALUES (:id, :partner_id, :app, :source_type, :source_id, :item_type, :item_id, :label, :customer,
        :amount_cents, :commission_cents, :commission_percent, :status, :created_at, :earned_at)""",
        referral,
    )
    kit.log(
        connection,
        SOURCES[source_type],
        "referral",
        f"{found['name']} referred {customer} ({label})",
        ref_type="partner",
        ref_id=found["id"],
    )
    return referral


def earn(
    connection: sqlite3.Connection, source_type: str, source_id: str, amount_cents: int
) -> None:
    """The sale was paid: the commission is owed to the partner (once; a payout already made is not changed). It
    is worked out at the rate in force when the sale was made: the rate both sides were shown while it waited."""
    ensure(connection)
    row = kit.fetch_one(
        connection,
        """SELECT r.*, p.commission_percent AS partner_percent FROM xp_referrals r
        JOIN xp_partners p ON p.id = r.partner_id WHERE r.source_type = ? AND r.source_id = ?""",
        (source_type, source_id),
    )
    if not row or row["status"] == "paid":
        return
    rate = (
        row["commission_percent"]
        if row["commission_percent"] is not None
        else row["partner_percent"]
    )
    amount = max(0, int(amount_cents))
    kit.update_row(
        connection,
        "xp_referrals",
        row["id"],
        {
            "amount_cents": amount,
            "commission_cents": _commission(amount, rate),
            "commission_percent": int(rate),
            "status": "earned",
            "earned_at": row["earned_at"] or kit.now(),
        },
    )


def void(connection: sqlite3.Connection, source_type: str, source_id: str) -> None:
    """The sale was refunded or cancelled: no commission (a commission already paid out stays on record)."""
    ensure(connection)
    connection.execute(
        "UPDATE xp_referrals SET status = 'void', commission_cents = 0 WHERE source_type = ? AND source_id = ? AND status IN ('pending', 'earned')",
        (source_type, source_id),
    )


def pay_out(
    connection: sqlite3.Connection, partner_id: str, note: Any = "", *, via: Any = None
) -> dict[str, Any]:
    """Record that the business paid the partner everything owed (it pays them itself, outside Aira)."""
    found = partner(connection, partner_id)
    owed = kit.fetch_all(
        connection,
        "SELECT id, commission_cents FROM xp_referrals WHERE partner_id = ? AND status = 'earned'",
        (partner_id,),
    )
    amount = sum(int(row["commission_cents"] or 0) for row in owed)
    if amount <= 0:
        raise ValueError(f"Nothing is owed to {found['name']} right now.")
    now = kit.now()
    payout = {
        "id": kit.new_id(),
        "partner_id": partner_id,
        "amount_cents": amount,
        "referrals": len(owed),
        "note": kit.text(note, "the note", max_len=500),
        "created_at": now,
    }
    connection.execute(
        "INSERT INTO xp_payouts (id, partner_id, amount_cents, referrals, note, created_at) VALUES (:id, :partner_id, :amount_cents, :referrals, :note, :created_at)",
        payout,
    )
    connection.executemany(
        "UPDATE xp_referrals SET status = 'paid', paid_at = ?, payout_id = ? WHERE id = ? AND status = 'earned'",
        [(now, payout["id"], row["id"]) for row in owed],
    )
    profile = kit.business(connection)
    kit.log(
        connection,
        APP,
        "partner_payout",
        f"Paid {found['name']} {kit.fmt_money(amount, profile['currency'])} in commission",
        ref_type="partner",
        ref_id=partner_id,
        amount_cents=amount,
    )
    if found["email"]:
        page = public.link_for(connection, APP, "partner", partner_id)
        kit.queue_email(
            connection,
            APP,
            "partner_payout",
            to=found["email"],
            subject=f"Your commission from {profile['name'] or 'us'}",
            body="\n".join(
                [
                    f"Hi {found['name']},",
                    "",
                    f"We have paid you {kit.fmt_money(amount, profile['currency'])} in commission for {len(owed)} "
                    f"referral{'s' if len(owed) != 1 else ''}. Thank you for sharing our courses and events!",
                    *(
                        [f"Your partner page: {kit.public_url(page['path'], via)}"]
                        if page
                        else []
                    ),
                    "",
                    profile["name"] or "",
                ]
            ),
            ref_type="partner",
            ref_id=partner_id,
        )
    connection.commit()
    return payout


def invite(
    connection: sqlite3.Connection, partner_id: str, *, via: Any = None
) -> dict[str, Any]:
    """Make the partner's page and email it to them (through the outbox)."""
    found = partner(connection, partner_id)
    if not found["active"]:
        raise ValueError("This partner is switched off. Switch them on first.")
    link = public.create_link(
        connection, APP, "partner", partner_id, title=f"Partner: {found['name']}"
    )
    if not found["email"]:
        raise ValueError(
            "Add the partner's email first, or copy their page link and send it yourself."
        )
    profile = kit.business(connection)
    kit.queue_email(
        connection,
        APP,
        "partner_invite",
        to=found["email"],
        subject=f"Earn by sharing {profile['name'] or 'our'} courses and events",
        body="\n".join(
            [
                f"Hi {found['name']},",
                "",
                f"You can earn {found['commission_percent']}% of every sale that comes through your own links.",
                "Your partner page has your links for every course and event, and shows what you have earned:",
                kit.public_url(link["path"], via),
                "",
                f"Your code: {found['code']}",
                "",
                profile["name"] or "",
            ]
        ),
        ref_type="partner",
        ref_id=partner_id,
    )
    connection.commit()
    return link


# ---------------------------------------------------------------------------
# what can be shared, and the partner's own page
# ---------------------------------------------------------------------------


def share_links(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """The live pages a partner can share: published courses and upcoming published events with a page link."""
    from aira.suite.academy import store as academy_store
    from aira.suite.events import store as events_store

    academy_store.ensure(connection)
    events_store.ensure(connection)
    out = []
    for course in kit.fetch_all(
        connection,
        "SELECT id, title, price_cents FROM ac_courses WHERE status = 'published' ORDER BY title COLLATE NOCASE",
    ):
        link = public.link_for(connection, "academy", "course", course["id"])
        if link:
            out.append(
                {
                    "type": "course",
                    "title": course["title"],
                    "price_cents": course["price_cents"],
                    "path": link["path"],
                }
            )
    for event in kit.fetch_all(
        connection,
        "SELECT id, title, starts_at FROM ev_events WHERE status = 'published' AND ends_at > ? ORDER BY starts_at",
        (kit.now(),),
    ):
        link = public.link_for(connection, "events", "event", event["id"])
        if link:
            out.append(
                {
                    "type": "event",
                    "title": event["title"],
                    "starts_at": event["starts_at"],
                    "path": link["path"],
                }
            )
    return out


def partner_page(connection: sqlite3.Connection, partner_id: str) -> dict[str, Any]:
    """What the partner sees: their code, links and earnings; never the names of the people they referred."""
    found = partner(connection, partner_id)
    if not found["active"]:
        raise LookupError("This partner page is no longer available.")
    rows = referrals(connection, partner_id)
    return {
        "partner": {
            "name": found["name"],
            "code": found["code"],
            "commission_percent": found["commission_percent"],
        },
        "totals": _totals(rows),
        "links": share_links(connection),
        "referrals": [
            {
                "label": row["label"],
                "amount_cents": int(row["amount_cents"] or 0),
                "commission_cents": int(row["commission_cents"] or 0),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows[:100]
        ],
        "payouts": [
            {"amount_cents": int(p["amount_cents"]), "created_at": p["created_at"]}
            for p in payouts(connection, partner_id)[:50]
        ],
        "catalog": public.link_for(connection, APP, "catalog", "catalog"),
        "timezone": kit.business(connection)["timezone"],
    }
