"""Channel memberships: a creator's monthly price for members-only videos, paid on the creator's own page.

Like support from viewers (creator.py), no money moves through Aira. The creator (an adult who accepted the creator
terms, with a payment page or UPI id saved under Support) sets a **plan**: a monthly price, a currency and what members
get. A viewer (an adult) **joins** for 1, 3, 6 or 12 months: they get a reference to put on their payment and the
creator's payment details. When the money arrives the creator marks the payment **received**: the membership is paid
up 30 days a month from today (or from when it would have ended, for a renewal), the member may watch the channel's
members-only videos until then, and on this computer the payment joins the Business hub's revenue. A payment marked
received by mistake can be marked **not received**, which takes those months back; one marked not received by
mistake can still be marked received.

What members bring in a month is worked out from what each active member paid (their last payment, per month, in
its currency), not from today's price: a plan whose price changes does not change what earlier members paid.

Children and teens neither join nor sell memberships; a members-only video stays locked for them.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

from aira.suite import kit
from aira.suite.videos import creator, service, store
from aira.suite.videos.store import new_id, row, rows, stamp

PRICE_MIN_CENTS = 100
PRICE_MAX_CENTS = 100_000
MONTHS = (1, 3, 6, 12)
MONTH_DAYS = 30
PAYMENT_STATUSES = ("pledged", "received", "declined", "cancelled")
RENEW_DAYS = 5  # Today reminds a member this many days before their membership ends


def _plan_row(sc: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    found = row(sc, "SELECT * FROM vi_member_plans WHERE channel_id = ?", (channel_id,))
    return found or {
        "channel_id": channel_id,
        "enabled": 0,
        "price_cents": 0,
        "currency": "USD",
        "perks": "",
    }


def _pay_details(sc: sqlite3.Connection, channel_id: str) -> dict[str, str]:
    """Where members pay: the creator's own payment page and UPI id, saved under Support from viewers."""
    found = row(
        sc, "SELECT link, upi, note FROM vi_support WHERE channel_id = ?", (channel_id,)
    )
    return {
        "link": (found or {}).get("link", ""),
        "upi": (found or {}).get("upi", ""),
    }


def _active_members(sc: sqlite3.Connection, channel_id: str) -> int:
    return int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_members WHERE channel_id = ? AND paid_until > ?",
            (channel_id, stamp()),
        )
        or 0
    )


def monthly_from_members(sc: sqlite3.Connection, channel_id: str) -> dict[str, int]:
    """{currency: cents a month} from the channel's active members, each by the last payment they made."""
    out: dict[str, int] = {}
    plan = _plan_row(sc, channel_id)
    for item in rows(
        sc,
        """SELECT m.id, (SELECT p.amount_cents || ':' || p.months || ':' || p.currency FROM vi_member_payments p
            WHERE p.membership_id = m.id AND p.status = 'received' ORDER BY p.decided_at DESC LIMIT 1) AS paid
        FROM vi_members m WHERE m.channel_id = ? AND m.paid_until > ?""",
        (channel_id, stamp()),
    ):
        if item["paid"]:
            cents, months, currency = str(item["paid"]).split(":", 2)
            monthly = round(int(cents) / max(1, int(months)))
        else:  # a membership with no payment on record (should not happen): today's price
            monthly, currency = int(plan["price_cents"] or 0), plan["currency"]
        out[currency] = out.get(currency, 0) + monthly
    return out


def money_list(amounts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"currency": code, "cents": cents, "amount": kit.fmt_money(cents, code)}
        for code, cents in sorted(amounts.items(), key=lambda item: -item[1])
    ]


def plan_settings(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    """The creator's side: the plan, whether they can turn it on (and why not), and the headline numbers."""
    found = _plan_row(sc, ch["id"])
    pay = _pay_details(sc, ch["id"])
    reasons = []
    if not service.adult(ch):
        reasons.append("Memberships are for adults.")
    elif not ch.get("terms_accepted_at"):
        reasons.append("Accept the creator terms first (Earnings > Ads and payouts).")
    if ch.get("suspended_at"):
        reasons.append("Your channel is suspended.")
    if not (pay["link"] or pay["upi"]):
        reasons.append(
            "Save your payment page or UPI id under Support from viewers, so members know where to pay."
        )
    active = _active_members(sc, ch["id"])
    monthly = money_list(monthly_from_members(sc, ch["id"]))
    return {
        "enabled": bool(found["enabled"]),
        "price_cents": int(found["price_cents"] or 0),
        "currency": found["currency"],
        "perks": found["perks"],
        "can_enable": not reasons,
        "why_not": reasons,
        "pay": pay,
        "active_members": active,
        # what the active members pay a month (by what they paid, per currency)
        "monthly": monthly,
        "monthly_cents": monthly[0]["cents"] if len(monthly) == 1 else 0,
        "members_only_videos": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM vi_videos WHERE channel_id = ? AND members_only = 1 AND deleted_at IS NULL",
                (ch["id"],),
            )
            or 0
        ),
        "currencies": list(kit.CURRENCIES),
        "months": list(MONTHS),
    }


def save_plan(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    service._active(ch)
    if not service.adult(ch):
        raise PermissionError("Memberships are for adults.")
    current = _plan_row(sc, ch["id"])
    merged = {**current, **raw}
    enabled = kit.flag(merged.get("enabled"))
    price = kit.cents_value(merged.get("price_cents"), "the monthly price", default=0)
    currency = kit.currency(merged.get("currency"), default="USD")
    perks = kit.text(
        merged.get("perks"), "what members get", max_len=500, multiline=True
    )
    if enabled:
        if not ch.get("terms_accepted_at"):
            raise PermissionError(
                "Accept the creator terms first (on the Earnings page)."
            )
        pay = _pay_details(sc, ch["id"])
        if not (pay["link"] or pay["upi"]):
            raise ValueError(
                "Save your payment page or UPI id under Support from viewers first, so members know where to pay."
            )
        if not PRICE_MIN_CENTS <= price <= PRICE_MAX_CENTS:
            low = kit.fmt_money(PRICE_MIN_CENTS, currency)
            high = kit.fmt_money(PRICE_MAX_CENTS, currency)
            raise ValueError(f"Choose a monthly price from {low} to {high}.")
    sc.execute(
        """INSERT INTO vi_member_plans (channel_id, enabled, price_cents, currency, perks, updated_at)
        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(channel_id) DO UPDATE SET enabled = excluded.enabled,
        price_cents = excluded.price_cents, currency = excluded.currency, perks = excluded.perks,
        updated_at = excluded.updated_at""",
        (ch["id"], 1 if enabled else 0, price, currency, perks, stamp()),
    )
    sc.commit()
    return plan_settings(sc, service.channel(sc, ch["id"]))


def _open_plan(
    sc: sqlite3.Connection, channel_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    target = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (channel_id,))
    if (
        not target
        or target["suspended_at"]
        or not service.adult(target)
        or not target["terms_accepted_at"]
    ):
        return None
    plan = _plan_row(sc, channel_id)
    pay = _pay_details(sc, channel_id)
    if (
        not plan["enabled"]
        or not plan["price_cents"]
        or not (pay["link"] or pay["upi"])
    ):
        return None
    return target, plan


def _membership(
    sc: sqlite3.Connection, channel_id: str, member_id: str
) -> dict[str, Any] | None:
    return row(
        sc,
        "SELECT * FROM vi_members WHERE channel_id = ? AND member_id = ?",
        (channel_id, member_id),
    )


def for_viewer(
    sc: sqlite3.Connection, me_: dict[str, Any], channel_id: str
) -> dict[str, Any] | None:
    """What a viewer sees on a channel or a members-only video: the plan, and their own membership."""
    if channel_id == me_["id"]:
        return None
    mine = _membership(sc, channel_id, me_["id"])
    found = _open_plan(sc, channel_id)
    if not found and not mine:
        return None
    plan = found[1] if found else _plan_row(sc, channel_id)
    waiting = (
        row(
            sc,
            "SELECT * FROM vi_member_payments WHERE membership_id = ? AND status = 'pledged' ORDER BY created_at DESC LIMIT 1",
            (mine["id"],),
        )
        if mine
        else None
    )
    paid_until = mine["paid_until"] if mine else None
    return {
        "open": bool(found),
        "price_cents": int(plan["price_cents"] or 0),
        "currency": plan["currency"],
        "price": kit.fmt_money(int(plan["price_cents"] or 0), plan["currency"]),
        "perks": plan["perks"],
        "member": bool(paid_until and paid_until > stamp()),
        "paid_until": paid_until,
        "waiting": payment_view(sc, waiting, for_creator=False) if waiting else None,
        "can_join": bool(found) and service.adult(me_) and not me_.get("suspended_at"),
        "months": list(MONTHS),
    }


def _reference(payment_id: str) -> str:
    return f"MEM-{payment_id[:8].upper()}"


def payment_view(
    sc: sqlite3.Connection, item: dict[str, Any], *, for_creator: bool
) -> dict[str, Any]:
    other_id = item["member_id"] if for_creator else item["channel_id"]
    other = (
        row(sc, "SELECT * FROM vi_channels WHERE id = ?", (other_id,))
        if other_id
        else None
    )
    membership = row(
        sc, "SELECT paid_until FROM vi_members WHERE id = ?", (item["membership_id"],)
    )
    out = {
        "id": item["id"],
        "months": item["months"],
        "amount_cents": item["amount_cents"],
        "currency": item["currency"],
        "amount": kit.fmt_money(item["amount_cents"], item["currency"]),
        "reference": item["reference"],
        "status": item["status"],
        "created_at": item["created_at"],
        "decided_at": item["decided_at"],
        "paid_until": membership["paid_until"] if membership else None,
    }
    fallback = {
        "id": "",
        "name": "A former member" if for_creator else "Removed channel",
        "avatar": "🙂",
    }
    out["member" if for_creator else "channel"] = (
        service.mini(other) if other else fallback
    )
    return out


def join(
    sc: sqlite3.Connection, me_: dict[str, Any], channel_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """A viewer joins (or renews) for some months; the answer says how much, where to pay and the reference."""
    service._active(me_)
    if not service.adult(me_):
        raise PermissionError("Memberships are for adults.")
    channel_id = store.check_id(channel_id, "channel")
    if channel_id == me_["id"]:
        raise ValueError("You cannot join your own channel.")
    found = _open_plan(sc, channel_id)
    if not found:
        raise LookupError("This channel does not take members right now.")
    target, plan = found
    try:
        months = int(raw.get("months") or 1)
    except (TypeError, ValueError):
        months = 0
    if months not in MONTHS:
        raise ValueError("Choose 1, 3, 6 or 12 months.")
    mine = _membership(sc, channel_id, me_["id"])
    moment = stamp()
    if not mine:
        membership_id = new_id()
        sc.execute(
            "INSERT INTO vi_members (id, channel_id, member_id, paid_until, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?)",
            (membership_id, channel_id, me_["id"], moment, moment),
        )
    else:
        membership_id = mine["id"]
        if row(
            sc,
            "SELECT 1 AS x FROM vi_member_payments WHERE membership_id = ? AND status = 'pledged'",
            (membership_id,),
        ):
            raise ValueError(
                "You already have a payment waiting for the creator. Withdraw it first to change it."
            )
    service.rate(sc, me_["id"], "tip")
    payment_id = new_id()
    amount = int(plan["price_cents"]) * months
    sc.execute(
        """INSERT INTO vi_member_payments (id, membership_id, channel_id, member_id, months, amount_cents, currency, reference,
        status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pledged', ?)""",
        (
            payment_id,
            membership_id,
            channel_id,
            me_["id"],
            months,
            amount,
            plan["currency"],
            _reference(payment_id),
            moment,
        ),
    )
    service.notify(
        sc,
        channel_id,
        "member",
        f"{me_['name']} is joining your channel for {_months(months)} ({kit.fmt_money(amount, plan['currency'])}). "
        "Mark it received on the Earnings page when the money arrives.",
        ref_type="membership",
        ref_id=payment_id,
    )
    sc.commit()
    item = row(sc, "SELECT * FROM vi_member_payments WHERE id = ?", (payment_id,)) or {}
    pay = _pay_details(sc, channel_id)
    return {
        "payment": payment_view(sc, item, for_creator=False),
        "pay": {
            **pay,
            "name": target["name"],
            "upi_link": creator.upi_link(
                pay["upi"],
                target["name"],
                amount,
                plan["currency"],
                _reference(payment_id),
            ),
        },
    }


def _months(n: int) -> str:
    return "1 month" if n == 1 else f"{n} months"


def withdraw(
    sc: sqlite3.Connection, me_: dict[str, Any], payment_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM vi_member_payments WHERE id = ? AND member_id = ?",
        (store.check_id(payment_id, "payment"), me_["id"]),
    )
    if not found:
        raise LookupError("That payment was not found.")
    if found["status"] != "pledged":
        raise ValueError(
            "Only a payment that has not been received yet can be withdrawn."
        )
    sc.execute(
        "UPDATE vi_member_payments SET status = 'cancelled', decided_at = ? WHERE id = ?",
        (stamp(), found["id"]),
    )
    sc.commit()
    return payment_view(sc, {**found, "status": "cancelled"}, for_creator=False)


def decide(
    sc: sqlite3.Connection, ch: dict[str, Any], payment_id: str, action: str
) -> dict[str, Any]:
    """``received``: the membership is paid up for the months bought, from today or from when it would have ended.
    ``not_received``: a waiting payment is declined; one received by mistake gives those months back. A payment
    marked not received by mistake can still be marked received."""
    found = row(
        sc,
        "SELECT * FROM vi_member_payments WHERE id = ? AND channel_id = ?",
        (store.check_id(payment_id, "payment"), ch["id"]),
    )
    if not found:
        raise LookupError("That payment was not found.")
    action = kit.choice(action, "the answer", ("received", "not_received"))
    allowed = {
        "received": ("pledged", "declined"),
        "not_received": ("pledged", "received"),
    }[action]
    if found["status"] not in allowed:
        raise ValueError(
            "The member withdrew this payment."
            if found["status"] == "cancelled"
            else "This payment was already answered."
        )
    membership = row(
        sc, "SELECT * FROM vi_members WHERE id = ?", (found["membership_id"],)
    )
    now = store.utcnow()
    days = timedelta(days=MONTH_DAYS * int(found["months"]))
    if membership:
        current = (
            store.parse(membership["paid_until"]) if membership["paid_until"] else None
        )
        if action == "received":
            start = current if current and current > now else now
            paid_until: str | None = stamp(start + days)
        else:
            paid_until = membership["paid_until"]
            if found["status"] == "received" and current:
                paid_until = stamp(current - days)
        sc.execute(
            "UPDATE vi_members SET paid_until = ?, updated_at = ? WHERE id = ?",
            (paid_until, stamp(), membership["id"]),
        )
    status = "received" if action == "received" else "declined"
    sc.execute(
        "UPDATE vi_member_payments SET status = ?, decided_at = ? WHERE id = ?",
        (status, stamp(), found["id"]),
    )
    if status == "received" and found["member_id"]:
        service.notify(
            sc,
            found["member_id"],
            "membership",
            f"Welcome! {ch['name']} received your membership payment of "
            f"{kit.fmt_money(found['amount_cents'], found['currency'])}. You can watch their members-only videos.",
            ref_type="channel",
            ref_id=ch["id"],
        )
    sc.commit()
    return payment_view(
        sc,
        row(sc, "SELECT * FROM vi_member_payments WHERE id = ?", (found["id"],))
        or found,
        for_creator=True,
    )


def creator_view(
    sc: sqlite3.Connection, ch: dict[str, Any], status: str = ""
) -> dict[str, Any]:
    """The creator's Memberships page: the plan, the members, and payments (waiting first)."""
    if status:
        kit.choice(status, "the status", PAYMENT_STATUSES)
        payments = rows(
            sc,
            "SELECT * FROM vi_member_payments WHERE channel_id = ? AND status = ? ORDER BY created_at DESC LIMIT 200",
            (ch["id"], status),
        )
    else:
        payments = rows(
            sc,
            """SELECT * FROM vi_member_payments WHERE channel_id = ?
            ORDER BY CASE WHEN status = 'pledged' THEN 0 ELSE 1 END, created_at DESC LIMIT 200""",
            (ch["id"],),
        )
    now = stamp()
    members = []
    for item in rows(
        sc,
        "SELECT * FROM vi_members WHERE channel_id = ? AND paid_until IS NOT NULL ORDER BY paid_until DESC LIMIT 500",
        (ch["id"],),
    ):
        person = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (item["member_id"],))
        members.append(
            {
                "member": service.mini(person)
                if person
                else {"id": "", "name": "A former member", "avatar": "🙂"},
                "paid_until": item["paid_until"],
                "active": item["paid_until"] > now,
                "since": item["created_at"],
            }
        )
    totals = rows(
        sc,
        """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_member_payments WHERE channel_id = ?
        AND status = 'received' GROUP BY currency ORDER BY cents DESC""",
        (ch["id"],),
    )
    return {
        "plan": plan_settings(sc, ch),
        "members": members,
        "payments": [payment_view(sc, item, for_creator=True) for item in payments],
        "waiting": waiting_count(sc, ch["id"]),
        "totals": [
            {**item, "amount": kit.fmt_money(int(item["cents"] or 0), item["currency"])}
            for item in totals
        ],
    }


def waiting_count(sc: sqlite3.Connection, channel_id: str) -> int:
    return int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_member_payments WHERE channel_id = ? AND status = 'pledged'",
            (channel_id,),
        )
        or 0
    )


def my_memberships(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    """The channels this person is (or was) a member of, with a waiting payment if any."""
    out = []
    now = stamp()
    for item in rows(
        sc,
        "SELECT * FROM vi_members WHERE member_id = ? ORDER BY updated_at DESC LIMIT 200",
        (me_["id"],),
    ):
        target = row(
            sc, "SELECT * FROM vi_channels WHERE id = ?", (item["channel_id"],)
        )
        if not target:
            continue
        waiting = row(
            sc,
            "SELECT * FROM vi_member_payments WHERE membership_id = ? AND status = 'pledged' ORDER BY created_at DESC LIMIT 1",
            (item["id"],),
        )
        if not item["paid_until"] and not waiting:
            continue  # joined, then withdrew before paying: nothing to show
        out.append(
            {
                "channel": service.mini(target),
                "paid_until": item["paid_until"],
                "active": bool(item["paid_until"] and item["paid_until"] > now),
                "waiting": payment_view(sc, waiting, for_creator=False)
                if waiting
                else None,
                "can_renew": _open_plan(sc, item["channel_id"]) is not None,
            }
        )
    return out


def ending_soon(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    """Memberships that end in the next few days (Today reminds the member to renew)."""
    now = store.utcnow()
    return rows(
        sc,
        """SELECT m.channel_id, m.paid_until, c.name FROM vi_members m JOIN vi_channels c ON c.id = m.channel_id
        WHERE m.member_id = ? AND m.paid_until > ? AND m.paid_until <= ? AND NOT EXISTS (
            SELECT 1 FROM vi_member_payments p WHERE p.membership_id = m.id AND p.status = 'pledged')
        ORDER BY m.paid_until LIMIT 5""",
        (me_["id"], stamp(now), stamp(now + timedelta(days=RENEW_DAYS))),
    )
