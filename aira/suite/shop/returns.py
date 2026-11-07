"""Returns: a customer sends items back from their order page, the shop approves or rejects, marks them
received, and the money goes back through the order's refund path.

1. **Requested** (``request``): from the order page, within the shop's return window (``return_window_days``
   after the order was delivered or picked up, or shipped when it never was marked delivered), for items of a
   paid order not already being returned, with a reason. The customer is emailed, and so is the shop.
2. **Approved** or **rejected** (``decide``), with a note the customer reads (what to do next: the shop's return
   instructions are added to an approval).
3. **Received** (``receive``): the items go back into stock, then the refund is made through
   :func:`aira.suite.shop.service.refund` (Razorpay when the order was paid online, otherwise the shop's own
   refund), which writes the negative ledger line. A refund that fails leaves the return received, with the
   error, to try again (``refund``).
4. **Refunded**: the customer is emailed with the amount.

The refund is the returned items' share of what the customer paid for goods (their discount and tax with them;
delivery is not refunded unless the shop says so with an amount). Every customer email is kind ``return_update``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from aira.suite import kit, public
from aira.suite.shop import service, store
from aira.suite.shop.store import APP

REASONS: dict[str, str] = {
    "damaged": "It arrived damaged",
    "wrong_item": "I got the wrong item",
    "not_as_described": "It is not as described",
    "does_not_fit": "It does not fit",
    "changed_mind": "I changed my mind",
    "other": "Something else",
}
OPEN_STATUSES = ("requested", "approved", "received")
FIRST_NUMBER = 1


def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["items"] = kit.loads(out.get("items"), []) or []
    out["history"] = kit.loads(out.get("history"), []) or []
    out["restocked"] = bool(out.get("restocked"))
    out["reason_label"] = REASONS.get(out.get("reason") or "", out.get("reason") or "")
    return out


def get(connection: sqlite3.Connection, return_id: str) -> dict[str, Any]:
    return _decode(kit.require_row(connection, "sh_returns", return_id, "return"))


def for_order(connection: sqlite3.Connection, order_id: str) -> list[dict[str, Any]]:
    return [
        _decode(row)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM sh_returns WHERE order_id = ? ORDER BY number",
            (order_id,),
        )
    ]


def list_returns(
    connection: sqlite3.Connection, *, status: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    where, params = "", []
    if status == "open":
        where = f"WHERE r.status IN ({', '.join('?' for _ in OPEN_STATUSES)})"
        params = list(OPEN_STATUSES)
    elif status:
        where = "WHERE r.status = ?"
        params = [status]
    rows = kit.fetch_all(
        connection,
        f"""SELECT r.*, o.number AS order_number, o.customer_name, o.customer_email, o.currency
        FROM sh_returns r JOIN sh_orders o ON o.id = r.order_id {where}
        ORDER BY r.created_at DESC LIMIT ?""",  # noqa: S608
        (*params, max(1, min(limit, 2000))),
    )
    return [_decode(row) for row in rows]


def _history(entry: Mapping[str, Any], status: str, note: str = "") -> str:
    items = list(entry.get("history") or [])
    items.append({"status": status, "at": kit.now(), "note": note[:300]})
    return kit.dumps(items[-50:])


def _sent_at(order: Mapping[str, Any]) -> str:
    """When the window starts: delivered (or picked up), else shipped; '' while the order has not gone out."""
    for wanted in ("delivered", "shipped"):
        found = [
            entry.get("at")
            for entry in order.get("history") or []
            if entry.get("status") == wanted
        ]
        if found:
            return str(found[-1])
    return ""


def window(
    connection: sqlite3.Connection, order: Mapping[str, Any]
) -> dict[str, Any]:
    """Whether the customer may ask for a return now, until when, and why not."""
    days = int(store.settings(connection)["return_window_days"] or 0)
    sent = _sent_at(order)
    until = kit.iso(kit.parse_time(sent) + timedelta(days=days)) if sent and days else ""
    reason = ""
    if not days:
        reason = "This shop does not take returns online. Contact the shop."
    elif order["fulfilment_status"] == "cancelled":
        reason = "This order was cancelled."
    elif order["payment_status"] != "paid":
        reason = (
            "This order was refunded."
            if order["payment_status"] == "refunded"
            else "Returns open once the order is paid."
        )
    elif not sent:
        reason = "Returns open once the order has been sent to you."
    elif kit.now() > until:
        reason = f"The {days}-day return window has closed."
    return {"open": not reason, "until": until, "days": days, "reason": reason}


def returnable(connection: sqlite3.Connection, order: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each line with how many can still be returned (bought, less what is in returns not rejected)."""
    taken: dict[tuple[str, str], int] = {}
    for entry in for_order(connection, str(order["id"])):
        if entry["status"] == "rejected":
            continue
        for item in entry["items"]:
            key = (str(item["product_id"]), str(item.get("option") or ""))
            taken[key] = taken.get(key, 0) + int(item["quantity"])
    out = []
    for index, line in enumerate(order["items"]):
        key = (str(line["product_id"]), str(line.get("option") or ""))
        left = max(0, int(line["quantity"]) - taken.get(key, 0))
        taken[key] = max(0, taken.get(key, 0) - int(line["quantity"]))
        out.append(
            {
                "line": index,
                "product_id": line["product_id"],
                "name": line["name"],
                "option": line.get("option") or "",
                "unit_cents": int(line["unit_cents"]),
                "quantity": int(line["quantity"]),
                "returnable": left,
            }
        )
    return out


def value_of(order: Mapping[str, Any], items: list[Mapping[str, Any]]) -> int:
    """What the returned items cost the customer: their share of the goods total after discount, tax included."""
    subtotal = int(order["subtotal_cents"] or 0)
    if subtotal <= 0:
        return 0
    paid_for_goods = int(order["total_cents"]) - int(order["delivery_cents"] or 0)
    worth = sum(int(item["unit_cents"]) * int(item["quantity"]) for item in items)
    return max(0, min(paid_for_goods, round(paid_for_goods * worth / subtotal)))


def _next_number(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(number) FROM sh_returns").fetchone()
    return (int(row[0]) if row and row[0] is not None else FIRST_NUMBER - 1) + 1


def label(entry: Mapping[str, Any]) -> str:
    return f"R-{int(entry['number']):04d}"


def request(
    connection: sqlite3.Connection,
    order_id: str,
    raw: Mapping[str, Any],
    *,
    origin: Any = "",
) -> dict[str, Any]:
    """The customer asks to return items: ``{"items": [{"line", "quantity"}], "reason", "details"}``."""
    store.ensure(connection)
    order = store.get_order(connection, order_id)
    state = window(connection, order)
    if not state["open"]:
        raise ValueError(state["reason"])
    reason = kit.choice(raw.get("reason"), "the reason", tuple(REASONS), default="other")
    details = kit.text(raw.get("details"), "what happened", max_len=1000, multiline=True)
    if reason == "other" and not details:
        raise ValueError("Tell the shop why you are returning it.")
    lines = returnable(connection, order)
    wanted = raw.get("items")
    if not isinstance(wanted, list) or not wanted:
        raise ValueError("Choose what you are returning.")
    picked: dict[int, int] = {}
    for item in wanted[:100]:
        if not isinstance(item, Mapping):
            raise ValueError("Choose what you are returning.")
        index = kit.integer(item.get("line"), "the item", minimum=0, maximum=len(lines) - 1)
        quantity = kit.integer(item.get("quantity"), "how many", default=1, minimum=0, maximum=100_000)
        if quantity:
            picked[index] = picked.get(index, 0) + quantity
    if not picked:
        raise ValueError("Choose at least one item to return.")
    items = []
    for index, quantity in sorted(picked.items()):
        line = lines[index]
        if quantity > line["returnable"]:
            label_text = f"{line['name']}{f' ({line['option']})' if line['option'] else ''}"
            raise ValueError(
                f"You can return at most {line['returnable']} of {label_text}."
                if line["returnable"]
                else f"{label_text} is already being returned."
            )
        items.append(
            {
                "product_id": line["product_id"],
                "name": line["name"],
                "option": line["option"],
                "unit_cents": line["unit_cents"],
                "quantity": quantity,
            }
        )
    stamp = kit.now()
    entry = {
        "id": kit.new_id(),
        "number": _next_number(connection),
        "order_id": order_id,
        "status": "requested",
        "items": kit.dumps(items),
        "reason": reason,
        "details": details,
        "value_cents": value_of(order, items),
        "history": kit.dumps([{"status": "requested", "at": stamp, "note": ""}]),
        "created_at": stamp,
        "updated_at": stamp,
    }
    connection.execute(
        """INSERT INTO sh_returns (id, number, order_id, status, items, reason, details, value_cents, history,
        created_at, updated_at) VALUES (:id, :number, :order_id, :status, :items, :reason, :details, :value_cents,
        :history, :created_at, :updated_at)""",
        entry,
    )
    made = get(connection, entry["id"])
    kit.log(
        connection,
        APP,
        "return",
        f"Return {label(made)} asked for on order #{order['number']}",
        detail=f"{sum(i['quantity'] for i in items)} item(s): {made['reason_label']}",
        ref_type="order",
        ref_id=order_id,
        public=True,
    )
    connection.commit()
    _tell(connection, order, made, "requested", origin=origin)
    owner = kit.business(connection).get("email") or ""
    if owner:
        kit.queue_email(
            connection,
            APP,
            "return_requested_notice",
            to=owner,
            subject=f"Return {label(made)} for order #{order['number']}",
            body=(
                f"{order['customer_name']} asked to return {_items_text(items)} from order #{order['number']}.\n"
                f"Reason: {made['reason_label']}{f' - {details}' if details else ''}\n\n"
                "Approve or reject it in Shop > Returns."
            ),
            ref_type="order",
            ref_id=order_id,
        )
    connection.commit()
    return made


def decide(
    connection: sqlite3.Connection,
    return_id: str,
    approve: bool,
    *,
    note: str = "",
    actor: str = "",
    origin: Any = "",
) -> dict[str, Any]:
    entry = get(connection, return_id)
    if entry["status"] != "requested":
        raise ValueError(f"This return is already {entry['status']}.")
    text = kit.text(note, "a note for the customer", max_len=1000, multiline=True)
    if not approve and not text:
        raise ValueError("Tell the customer why the return is not accepted.")
    status = "approved" if approve else "rejected"
    connection.execute(
        "UPDATE sh_returns SET status = ?, shop_note = ?, decided_at = ?, history = ?, updated_at = ? WHERE id = ?",
        (status, text, kit.now(), _history(entry, status, text), kit.now(), return_id),
    )
    order = store.get_order(connection, entry["order_id"])
    updated = get(connection, return_id)
    kit.log(
        connection,
        APP,
        "return",
        f"Return {label(updated)} {status}",
        ref_type="order",
        ref_id=order["id"],
        actor=actor,
    )
    connection.commit()
    _tell(connection, order, updated, status, origin=origin)
    connection.commit()
    return updated


def receive(
    connection: sqlite3.Connection,
    return_id: str,
    *,
    restock: bool = True,
    amount_cents: Any = None,
    note: str = "",
    secret_store: Any = None,
    actor: str = "",
    origin: Any = "",
) -> dict[str, Any]:
    """The items are back: into stock (unless ``restock`` is off: they cannot be sold again), then the refund."""
    entry = get(connection, return_id)
    if entry["status"] not in ("approved", "requested"):
        raise ValueError(f"This return is {entry['status']}.")
    order = store.get_order(connection, entry["order_id"])
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if restock:
            for item in entry["items"]:
                service._take_stock(connection, item, +1)  # noqa: SLF001 - the shop's own stock move
        connection.execute(
            "UPDATE sh_returns SET status = 'received', restocked = ?, received_at = ?, history = ?, updated_at = ? WHERE id = ?",
            (
                1 if restock else 0,
                kit.now(),
                _history(entry, "received", "Back in stock" if restock else "Not put back in stock"),
                kit.now(),
                return_id,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    if restock:
        for product_id in {item["product_id"] for item in entry["items"]}:
            row = kit.fetch_one(connection, "SELECT * FROM sh_products WHERE id = ?", (product_id,))
            if row:
                service.notify_restocked(connection, store.decode_product(row))
    kit.log(
        connection,
        APP,
        "return",
        f"Return {label(entry)} received for order #{order['number']}",
        ref_type="order",
        ref_id=order["id"],
        actor=actor,
    )
    connection.commit()
    return refund(
        connection,
        return_id,
        amount_cents=amount_cents,
        note=note,
        secret_store=secret_store,
        actor=actor,
        origin=origin,
    )


def refund(
    connection: sqlite3.Connection,
    return_id: str,
    *,
    amount_cents: Any = None,
    note: str = "",
    secret_store: Any = None,
    actor: str = "",
    origin: Any = "",
) -> dict[str, Any]:
    """Give the money back for a received return through the order's refund (Razorpay or the shop's own).
    ValueError (with the return still received and the reason kept) when it cannot be made."""
    entry = get(connection, return_id)
    if entry["status"] != "received":
        raise ValueError(
            "Mark the return received first." if entry["status"] in ("requested", "approved") else f"This return is {entry['status']}."
        )
    order = store.get_order(connection, entry["order_id"])
    left = int(order["total_cents"]) - int(order["refunded_cents"] or 0)
    amount = kit.integer(
        amount_cents,
        "the refund amount (in cents)",
        default=min(int(entry["value_cents"]), left),
        minimum=0,
        maximum=max(0, left),
    )
    text = kit.text(note, "a note", max_len=300)
    if amount <= 0:
        _finish(connection, entry, order, 0, text or "Nothing to refund", origin=origin)
        return get(connection, return_id)
    try:
        service.refund(
            connection,
            order["id"],
            note=f"Return {label(entry)}" + (f": {text}" if text else ""),
            amount_cents=amount,
            secret_store=secret_store,
            actor=actor,
        )
    except (ValueError, LookupError, OSError, RuntimeError) as error:  # RazorpayError is a RuntimeError
        connection.rollback()
        connection.execute(
            "UPDATE sh_returns SET refund_error = ?, updated_at = ? WHERE id = ?",
            (str(error)[:300], kit.now(), return_id),
        )
        connection.commit()
        raise ValueError(f"The items are back, but the refund did not go through: {error}") from None
    _finish(connection, entry, store.get_order(connection, order["id"]), amount, text, origin=origin)
    return get(connection, return_id)


def _finish(
    connection: sqlite3.Connection,
    entry: Mapping[str, Any],
    order: Mapping[str, Any],
    amount: int,
    note: str,
    *,
    origin: Any,
) -> None:
    connection.execute(
        "UPDATE sh_returns SET status = 'refunded', refund_cents = ?, refund_error = '', refunded_at = ?, history = ?, updated_at = ? WHERE id = ?",
        (
            amount,
            kit.now(),
            _history(entry, "refunded", kit.fmt_money(amount, order["currency"]) + (f" · {note}" if note else "")),
            kit.now(),
            entry["id"],
        ),
    )
    connection.commit()
    _tell(connection, order, get(connection, entry["id"]), "refunded", origin=origin)
    connection.commit()


def _items_text(items: list[Mapping[str, Any]]) -> str:
    return ", ".join(
        f"{item['quantity']} x {item['name']}{f' ({item['option']})' if item.get('option') else ''}"
        for item in items
    )


def _tell(
    connection: sqlite3.Connection,
    order: Mapping[str, Any],
    entry: Mapping[str, Any],
    step: str,
    *,
    origin: Any = "",
) -> None:
    """The customer's email for one step of their return (it waits in the outbox like every message)."""
    if not order.get("customer_email"):
        return
    profile = kit.business(connection)
    shop = store.settings(connection)
    name = profile.get("name") or "our shop"
    first = str(order["customer_name"]).split(" ")[0] or "there"
    what = _items_text(entry["items"])
    link = public.link_for(connection, APP, "order", str(order["id"]))
    source = {"base_url": origin} if isinstance(origin, str) else origin
    page = kit.public_url(link["path"], source) if link else ""
    code = str(order.get("currency") or "USD")
    tag = label(entry)
    if step == "requested":
        subject = f"We got your return request {tag} - {name}"
        lines = [
            f"Thank you. We received your request to return {what} from order #{order['number']}.",
            "We will let you know once we have looked at it.",
        ]
    elif step == "approved":
        subject = f"Return {tag} approved - {name}"
        lines = [
            f"Your return of {what} from order #{order['number']} is approved.",
            str(entry.get("shop_note") or ""),
            str(shop.get("return_instructions") or ""),
            f"Please mark the parcel with {tag}. We refund you once the items reach us.",
        ]
    elif step == "rejected":
        subject = f"About your return {tag} - {name}"
        lines = [
            f"We are sorry: we cannot accept the return of {what} from order #{order['number']}.",
            str(entry.get("shop_note") or ""),
            "Reply to this email if you have a question.",
        ]
    else:
        subject = f"Refund for return {tag} - {name}"
        refunded = int(entry.get("refund_cents") or 0)
        lines = [
            f"We received the items you returned ({what}).",
            f"We have refunded {kit.fmt_money(refunded, code)}. It can take a few days to reach your account."
            if refunded
            else "",
        ]
    body = "\n\n".join(
        part
        for part in (
            f"Hi {first},",
            *lines,
            f"Your order: {page}" if page else "",
            f"Thanks,\n{name}",
        )
        if part
    )
    kit.queue_email(
        connection,
        APP,
        "return_update",
        to=str(order["customer_email"]),
        subject=subject,
        body=body,
        ref_type="order",
        ref_id=str(order["id"]),
    )


def public_view(connection: sqlite3.Connection, order: Mapping[str, Any]) -> dict[str, Any]:
    """What the order page shows about returns: whether one can be asked for, the lines and the returns so far."""
    state = window(connection, order)
    lines = returnable(connection, order) if state["open"] else []
    return {
        "open": state["open"] and any(line["returnable"] for line in lines),
        "until": state["until"],
        "days": state["days"],
        "reason": state["reason"],
        "reasons": [{"id": key, "label": text} for key, text in REASONS.items()],
        "lines": [
            {k: line[k] for k in ("line", "name", "option", "returnable")}
            for line in lines
            if line["returnable"]
        ],
        "returns": [
            {
                "label": label(entry),
                "status": entry["status"],
                "items": [
                    {k: item[k] for k in ("name", "option", "quantity")}
                    for item in entry["items"]
                ],
                "reason": entry["reason_label"],
                "shop_note": entry["shop_note"],
                "refund_cents": int(entry["refund_cents"] or 0),
                "created_at": entry["created_at"],
            }
            for entry in for_order(connection, str(order["id"]))
        ],
    }


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    out = {status: 0 for status in store.RETURN_STATUSES}
    for row in connection.execute("SELECT status, COUNT(*) FROM sh_returns GROUP BY status"):
        out[row[0]] = int(row[1])
    return out
