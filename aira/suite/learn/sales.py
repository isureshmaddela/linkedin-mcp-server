"""Selling a deck: an adult puts a price on a deck they made and earns from the people who study it.

The deck's public page then shows a few cards as a free preview, the price and a "buy" form. A buyer leaves their
name and email and pays through the seller's own payment link or instructions (Aira does not take card payments).
The seller marks the payment received and the buyer gets an access code that opens every card on the same page.
Marking a sale unpaid turns the code off. Children cannot sell.

Whose money it is:

* On a person's own computer the account is the household's: a seller may use the business profile's payment link,
  sales join the Business hub's revenue, and the access code is emailed through the outbox.
* Hosted, a member's deck income is their own, not the organization's (like a Stocks analyst's): they set their own
  payment link, and nothing goes into the organization's revenue, activity feed or outbox (which the member cannot
  open and where admins could read the code). The seller gets the email ready to send themselves.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Any

from aira import suite
from aira.suite import kit, life, public
from aira.suite.learn import store

APP = store.APP
STATUSES = ("pending", "paid", "cancelled")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread
MAX_PRICE_CENTS = 100_000_00
PREVIEW_MIN, PREVIEW_MAX = 1, 20
MIN_CARDS = 2  # a deck on sale always keeps at least one card for buyers
PENDING_PER_DECK = (
    200  # open requests a deck holds before new ones wait (stops a flood from one page)
)


def hosted() -> bool:
    """The hosted build (an organization's members): deck income is the member's own, not the organization's.

    Asked of the build, not of the request: a buyer on a public page has no actor, yet the deck is still a member's.
    The hosted build is the one that names the person behind each request (``suite.set_actor_resolver``)."""
    return suite._actor_resolver is not None or suite.actor() is not None  # noqa: SLF001


def _hash(code: str) -> str:
    return hashlib.sha256(
        code.strip().upper().replace("-", "").replace(" ", "").encode("utf-8")
    ).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _reference() -> str:
    return "LN-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def for_sale(deck: dict[str, Any]) -> bool:
    return int(deck.get("price_cents") or 0) > 0


def _card_count(connection: sqlite3.Connection, deck_id: str) -> int:
    return kit.count(
        connection, "SELECT COUNT(*) FROM ln_cards WHERE deck_id = ?", (deck_id,)
    )


def _adult(profile: dict[str, Any]) -> None:
    if not life.is_adult(profile):
        raise PermissionError("Selling decks is for adults.")


# ---------------------------------------------------------------------------
# how a seller is paid
# ---------------------------------------------------------------------------


def _own_payment(connection: sqlite3.Connection, profile_id: str) -> dict[str, str]:
    row = (
        kit.fetch_one(
            connection, "SELECT * FROM ln_sellers WHERE profile_id = ?", (profile_id,)
        )
        or {}
    )
    return {
        "payment_link": row.get("payment_link") or "",
        "payment_instructions": row.get("payment_instructions") or "",
    }


def _ready(payment: dict[str, str]) -> bool:
    return bool(payment["payment_link"] or payment["payment_instructions"])


def payment_for(connection: sqlite3.Connection, profile_id: str) -> dict[str, str]:
    """How buyers pay this seller: their own link or instructions; on the local build, else the business
    profile's. Hosted, the organization's payment link is never a member's."""
    own = _own_payment(connection, profile_id)
    if _ready(own):
        return {**own, "from": "own"}
    if not hosted():
        business = kit.business(connection)
        if business["payment_link"] or business["payment_instructions"]:
            return {
                "payment_link": business["payment_link"],
                "payment_instructions": business["payment_instructions"],
                "from": "business",
            }
    return {"payment_link": "", "payment_instructions": "", "from": ""}


def payment_view(
    connection: sqlite3.Connection, profile: dict[str, Any]
) -> dict[str, Any]:
    """What the seller sees about how they are paid (and, locally, whether the business profile's link stands in)."""
    own = _own_payment(connection, profile["id"])
    used = payment_for(connection, profile["id"])
    business = {} if hosted() else kit.business(connection)
    return {
        **own,
        "ready": _ready(used),
        "using": used["from"],
        "business_link": business.get("payment_link", ""),
        "business_instructions": business.get("payment_instructions", ""),
        "hosted": hosted(),
    }


def _write_payment(
    connection: sqlite3.Connection, profile: dict[str, Any], raw: dict[str, Any]
) -> None:
    current = _own_payment(connection, profile["id"])
    merged = {**current, **{k: raw[k] for k in current if k in raw}}
    link = kit.url(merged.get("payment_link"), "the payment link")
    instructions = kit.text(
        merged.get("payment_instructions"),
        "payment instructions",
        max_len=600,
        multiline=True,
    )
    connection.execute(
        """INSERT INTO ln_sellers (profile_id, payment_link, payment_instructions, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(profile_id) DO UPDATE SET payment_link = excluded.payment_link,
        payment_instructions = excluded.payment_instructions, updated_at = excluded.updated_at""",
        (profile["id"], link, instructions, kit.now()),
    )


def save_payment(
    connection: sqlite3.Connection, profile: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """The seller's own payment link and instructions. Decks on sale must keep a way for buyers to pay."""
    _adult(profile)
    _write_payment(connection, profile, raw)
    on_sale = kit.count(
        connection,
        "SELECT COUNT(*) FROM ln_decks WHERE owner_id = ? AND archived_at IS NULL AND price_cents > 0",
        (profile["id"],),
    )
    if on_sale and not _ready(payment_for(connection, profile["id"])):
        connection.rollback()
        raise ValueError(
            "Your decks on sale need a way for buyers to pay. Keep a payment link or instructions, "
            "or stop selling them first."
        )
    connection.commit()
    return payment_view(connection, profile)


# ---------------------------------------------------------------------------
# the price
# ---------------------------------------------------------------------------


def set_price(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    deck_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Put a price on a deck (0 makes its page free again) and choose how many cards are a free preview.

    The payload may carry the seller's ``payment_link`` and ``payment_instructions`` too (the Sell dialog asks for
    them), so a deck is never put on sale with no way for buyers to pay."""
    deck = store.own_deck(connection, profile, deck_id)
    if not life.is_adult(profile):
        raise PermissionError("Ask an adult to sell decks.")
    # No default: a price a person typed that could not be read arrives empty, and must not make the deck free.
    price = kit.cents_value(
        raw.get("price_cents"), "the price (a number like 4.99)", minimum=0
    )
    if price > MAX_PRICE_CENTS:
        raise ValueError("The price is too high (at most 100,000).")
    preview = kit.integer(
        raw.get("preview_count"),
        "the number of free cards",
        default=int(deck.get("preview_count") or 5),
        minimum=PREVIEW_MIN,
        maximum=PREVIEW_MAX,
    )
    if price:
        if deck.get("source") == "copy" and deck.get("copied_from") != profile["id"]:
            raise PermissionError(
                "This deck is a copy of someone else's deck, so it is not yours to sell. "
                "Make a deck of your own to sell it."
            )
        if _card_count(connection, deck["id"]) < MIN_CARDS:
            raise ValueError("Add at least two cards before selling this deck.")
        if "payment_link" in raw or "payment_instructions" in raw:
            _write_payment(connection, profile, raw)
        if not _ready(payment_for(connection, profile["id"])):
            connection.rollback()
            raise ValueError(
                "Add your payment link or payment instructions, so buyers know how to pay you."
            )
    connection.execute(
        "UPDATE ln_decks SET price_cents = ?, preview_count = ?, updated_at = ? WHERE id = ?",
        (price, preview, kit.now(), deck["id"]),
    )
    if not price:
        # Free again: open requests to buy it are closed (people who paid keep their access).
        connection.execute(
            "UPDATE ln_sales SET status = 'cancelled', cancelled_at = ? WHERE deck_id = ? AND status = 'pending'",
            (kit.now(), deck["id"]),
        )
    elif not hosted():
        kit.log(
            connection,
            APP,
            "sell",
            f"{profile['name']} put {deck['title']} on sale for {kit.fmt_money(price, kit.business(connection)['currency'])}",
            ref_type="deck",
            ref_id=deck["id"],
        )
    connection.commit()
    return store.deck(connection, deck["id"])


def can_drop_card(connection: sqlite3.Connection, deck: dict[str, Any]) -> None:
    """A deck on sale keeps at least two cards (one free, one for buyers)."""
    if for_sale(deck) and _card_count(connection, deck["id"]) <= MIN_CARDS:
        raise ValueError(
            "A deck on sale needs at least two cards. Add another card first, or stop selling it."
        )


def can_turn_off_link(deck: dict[str, Any]) -> None:
    """The page of a deck on sale is where buyers pay and where paid buyers study: it stays on while it sells."""
    if for_sale(deck):
        raise ValueError(
            "This deck is on sale: its page is where buyers study. Stop selling it first to turn the link off."
        )


# ---------------------------------------------------------------------------
# the public page: preview, buy, unlock
# ---------------------------------------------------------------------------


def _public_payment(connection: sqlite3.Connection, owner_id: str) -> dict[str, str]:
    payment = payment_for(connection, owner_id)
    return {
        "link": payment["payment_link"],
        "instructions": payment["payment_instructions"],
    }


def page_data(connection: sqlite3.Connection, deck: dict[str, Any]) -> dict[str, Any]:
    """What the deck's public page shows: every card for a free deck, a preview and the price for one on sale."""
    cards = [
        {"front": c["front"], "back": c["back"], "hint": c["hint"]}
        for c in store.cards(connection, deck["id"])
    ]
    data = {
        "title": deck["title"],
        "description": deck["description"],
        "emoji": deck["emoji"],
        "total": len(cards),
        "for_sale": False,
        "cards": cards,
    }
    if for_sale(deck):
        # At least one card is always kept for buyers, however many free cards were chosen.
        preview = max(
            0, min(int(deck.get("preview_count") or 5), PREVIEW_MAX, len(cards) - 1)
        )
        payment = _public_payment(connection, deck["owner_id"])
        data.update(
            {
                "for_sale": True,
                "price_cents": int(deck["price_cents"]),
                "currency": kit.business(connection)["currency"],
                "seller": life.name_of(connection, deck["owner_id"]),
                "cards": cards[:preview],
                # The seller's own way to be paid (hosted, never the organization's).
                "payment": payment,
                "buyable": len(cards) >= MIN_CARDS
                and bool(payment["link"] or payment["instructions"]),
            }
        )
    return data


def request_purchase(
    connection: sqlite3.Connection, deck: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """A visitor asks to buy the deck. The same email asking again gets the same open request back."""
    if not for_sale(deck):
        raise ValueError("This deck is free: study it right here.")
    if _card_count(connection, deck["id"]) < MIN_CARDS or not _ready(
        payment_for(connection, deck["owner_id"])
    ):
        raise ValueError(
            "This deck cannot be bought right now. Please try again later."
        )
    name = kit.text(payload.get("name"), "your name", max_len=80, required=True)
    address = kit.email(payload.get("email"), "your email address", required=True)
    open_one = kit.fetch_one(
        connection,
        "SELECT * FROM ln_sales WHERE deck_id = ? AND buyer_email = ? AND status = 'pending'",
        (deck["id"], address),
    )
    if open_one:
        return _buyer_view(connection, open_one)
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM ln_sales WHERE deck_id = ? AND status = 'pending'",
            (deck["id"],),
        )
        >= PENDING_PER_DECK
    ):
        raise ValueError(
            "The seller has many requests waiting. Please try again in a few days."
        )
    sale = {
        "id": kit.new_id(),
        "deck_id": deck["id"],
        "owner_id": deck["owner_id"],
        "buyer_name": name,
        "buyer_email": address,
        "amount_cents": int(deck["price_cents"]),
        "currency": kit.business(connection)["currency"],
        "reference": _reference(),
        "created_at": kit.now(),
    }
    connection.execute(
        """INSERT INTO ln_sales (id, deck_id, owner_id, buyer_name, buyer_email, amount_cents, currency, reference, created_at)
        VALUES (:id, :deck_id, :owner_id, :buyer_name, :buyer_email, :amount_cents, :currency, :reference, :created_at)""",
        sale,
    )
    if not hosted():
        kit.log(
            connection,
            APP,
            "sale_request",
            f"{name} asked to buy {deck['title']}",
            ref_type="sale",
            ref_id=sale["id"],
            amount_cents=sale["amount_cents"],
            public=True,
        )
    connection.commit()
    return _buyer_view(connection, {**sale, "status": "pending"})


def _buyer_view(connection: sqlite3.Connection, sale: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference": sale["reference"],
        "amount_cents": sale["amount_cents"],
        "currency": sale["currency"],
        "status": sale["status"],
        "payment": _public_payment(connection, sale["owner_id"]),
    }


def unlock(
    connection: sqlite3.Connection, deck: dict[str, Any], code: str
) -> list[dict[str, Any]]:
    """Every card of a deck on sale, for the holder of a paid sale's access code."""
    if not code or not code.strip():
        raise PermissionError("Enter your access code.")
    if not for_sale(deck):
        return [
            {"front": c["front"], "back": c["back"], "hint": c["hint"]}
            for c in store.cards(connection, deck["id"])
        ]
    found = kit.fetch_one(
        connection,
        "SELECT id FROM ln_sales WHERE deck_id = ? AND status = 'paid' AND code_hash = ?",
        (deck["id"], _hash(code)),
    )
    if not found:
        raise PermissionError(
            "That access code is not right. Check the email you were sent."
        )
    return [
        {"front": c["front"], "back": c["back"], "hint": c["hint"]}
        for c in store.cards(connection, deck["id"])
    ]


# ---------------------------------------------------------------------------
# the seller's side
# ---------------------------------------------------------------------------


def sale(
    connection: sqlite3.Connection, profile: dict[str, Any], sale_id: str
) -> dict[str, Any]:
    _adult(profile)
    row = kit.fetch_one(
        connection,
        "SELECT * FROM ln_sales WHERE id = ? AND owner_id = ?",
        (sale_id, profile["id"]),
    )
    if not row:
        raise LookupError("That sale was not found.")
    return row


def _view(connection: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    title = kit.fetch_one(
        connection, "SELECT title FROM ln_decks WHERE id = ?", (row["deck_id"],)
    )
    return {
        k: row[k]
        for k in (
            "id",
            "deck_id",
            "buyer_name",
            "buyer_email",
            "amount_cents",
            "currency",
            "reference",
            "status",
            "created_at",
            "paid_at",
            "cancelled_at",
        )
    } | {"deck_title": (title or {}).get("title", "A deleted deck")}


def sales(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    status: str = "",
    q: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    _adult(profile)
    clauses, params = ["s.owner_id = ?"], [profile["id"]]
    if status:
        clauses.append("s.status = ?")
        params.append(kit.choice(status, "the status", STATUSES))
    if q.strip():
        clauses.append(
            "(s.buyer_name LIKE ? OR s.buyer_email LIKE ? OR s.reference LIKE ? OR d.title LIKE ?)"
        )
        like = f"%{q.strip()[:80]}%"
        params.extend([like, like, like, like])
    rows = kit.fetch_all(
        connection,
        f"""SELECT s.* FROM ln_sales s LEFT JOIN ln_decks d ON d.id = s.deck_id WHERE {" AND ".join(clauses)}
        ORDER BY s.created_at DESC LIMIT ?""",  # noqa: S608 - fixed clauses
        (*params, limit),
    )
    return [_view(connection, row) for row in rows]


def summary(connection: sqlite3.Connection, profile: dict[str, Any]) -> dict[str, Any]:
    """The seller's headline numbers, how they are paid and their decks with a price."""
    _adult(profile)
    month = kit.now()[:7]
    numbers = (
        kit.fetch_one(
            connection,
            """SELECT COALESCE(SUM(CASE WHEN status = 'paid' THEN amount_cents END), 0) AS earned,
        COALESCE(SUM(CASE WHEN status = 'paid' AND substr(paid_at, 1, 7) = ? THEN amount_cents END), 0) AS month,
        COUNT(CASE WHEN status = 'paid' THEN 1 END) AS sold,
        COUNT(CASE WHEN status = 'pending' THEN 1 END) AS waiting,
        COALESCE(SUM(CASE WHEN status = 'pending' THEN amount_cents END), 0) AS waiting_cents
        FROM ln_sales WHERE owner_id = ?""",
            (month, profile["id"]),
        )
        or {}
    )
    decks = kit.fetch_all(
        connection,
        """SELECT d.id, d.title, d.emoji, d.price_cents, d.preview_count,
        (SELECT COUNT(*) FROM ln_cards c WHERE c.deck_id = d.id) AS card_count,
        (SELECT COUNT(*) FROM ln_sales s WHERE s.deck_id = d.id AND s.status = 'paid') AS sold,
        (SELECT COALESCE(SUM(amount_cents), 0) FROM ln_sales s WHERE s.deck_id = d.id AND s.status = 'paid') AS earned
        FROM ln_decks d WHERE d.owner_id = ? AND d.archived_at IS NULL AND d.price_cents > 0 ORDER BY d.title""",
        (profile["id"],),
    )
    for row in decks:
        row["link"] = public.link_for(connection, APP, "deck", row["id"])
    payment = payment_view(connection, profile)
    return {
        "earned_cents": int(numbers.get("earned") or 0),
        "month_cents": int(numbers.get("month") or 0),
        "sold": int(numbers.get("sold") or 0),
        "waiting": int(numbers.get("waiting") or 0),
        "waiting_cents": int(numbers.get("waiting_cents") or 0),
        "currency": kit.business(connection)["currency"],
        "payment_ready": payment["ready"],
        "payment": payment,
        "hosted": payment["hosted"],
        "decks": decks,
    }


def _access_email(
    deck_title: str, row: dict[str, Any], code: str, link: str
) -> tuple[str, str]:
    subject = f"Your access code for {deck_title}"
    body = (
        f"Hello {row['buyer_name']},\n\n"
        f"Thank you: your payment for {deck_title} (reference {row['reference']}) has arrived.\n\n"
        f"Your access code is {code}\n\n"
        f"Open the deck here and enter the code to study every card:\n{link}\n\n"
        "Keep this email: the code works whenever you come back."
    )
    return subject, body


def mark_paid(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    sale_id: str,
    *,
    source: Any = None,
) -> dict[str, Any]:
    """The seller saw the money arrive: record the revenue (locally) and give the buyer a new access code.

    Marking a paid sale paid again makes a fresh code (the old one stops working): that is how a lost code is
    resent. The code is shown to the seller once, with the email ready to send themselves (hosted, or when no
    mail server is set up)."""
    row = sale(connection, profile, sale_id)
    if row["status"] == "cancelled":
        raise ValueError("This request was cancelled. Ask the buyer to send a new one.")
    deck = (
        kit.fetch_one(
            connection, "SELECT * FROM ln_decks WHERE id = ?", (row["deck_id"],)
        )
        or {}
    )
    if not deck or deck.get("archived_at"):
        raise ValueError("This deck was deleted, so there is nothing to unlock.")
    if not for_sale(deck):
        raise ValueError(
            "This deck is free now: anyone with its link can study every card, so no code is needed."
        )
    code = new_code()
    was_paid = row["status"] == "paid"
    paid_at = row["paid_at"] or kit.now()
    connection.execute(
        "UPDATE ln_sales SET status = 'paid', code_hash = ?, paid_at = ? WHERE id = ?",
        (_hash(code), paid_at, row["id"]),
    )
    if not hosted():
        kit.record_revenue(
            connection,
            APP,
            source_type="deck_sale",
            source_id=row["id"],
            amount_cents=row["amount_cents"],
            currency_code=row["currency"],
            label=f"Deck sold: {deck['title']}",
            customer=row["buyer_name"],
            at=paid_at,
        )
        # Paid again after "Not paid": the ledger line moves to the new payment date (record_revenue keeps the
        # first date when it updates a line).
        connection.execute(
            "UPDATE suite_revenue SET at = ? WHERE app = ? AND source_type = 'deck_sale' AND source_id = ?",
            (paid_at, APP, row["id"]),
        )
    link = public.create_link(connection, APP, "deck", deck["id"], title=deck["title"])
    subject, body = _access_email(
        deck["title"], row, code, kit.public_url(link["path"], source)
    )
    message = (
        None
        if hosted()
        else kit.queue_email(
            connection,
            APP,
            "deck_access",
            to=row["buyer_email"],
            subject=subject,
            body=body,
            ref_type="sale",
            ref_id=row["id"],
        )
    )
    if not was_paid and not hosted():
        kit.log(
            connection,
            APP,
            "sale",
            f"{row['buyer_name']} bought {deck['title']}",
            ref_type="sale",
            ref_id=row["id"],
            amount_cents=row["amount_cents"],
        )
    connection.commit()
    fresh = sale(connection, profile, row["id"])
    return {
        "sale": _view(connection, fresh),
        "code": code,
        "email": "queued" if message else "none",
        "email_status": (message or {}).get("status", ""),
        # The email ready for the seller to send from their own mail.
        "message": {"to": row["buyer_email"], "subject": subject, "body": body},
    }


def mark_unpaid(
    connection: sqlite3.Connection, profile: dict[str, Any], sale_id: str
) -> dict[str, Any]:
    """Undo a payment marked by mistake (or a refund): the ledger line stops counting and the code stops working."""
    row = sale(connection, profile, sale_id)
    if row["status"] != "paid":
        raise ValueError("Only a paid sale can be marked unpaid.")
    connection.execute(
        "UPDATE ln_sales SET status = 'pending', code_hash = '', paid_at = NULL WHERE id = ?",
        (row["id"],),
    )
    if not hosted():
        kit.void_revenue(connection, APP, "deck_sale", row["id"])
    connection.commit()
    return _view(connection, sale(connection, profile, row["id"]))


def cancel(
    connection: sqlite3.Connection, profile: dict[str, Any], sale_id: str
) -> dict[str, Any]:
    row = sale(connection, profile, sale_id)
    if row["status"] == "paid":
        raise ValueError("Mark the sale unpaid first: it was paid.")
    connection.execute(
        "UPDATE ln_sales SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
        (kit.now(), row["id"]),
    )
    connection.commit()
    return _view(connection, sale(connection, profile, row["id"]))


def waiting_for(connection: sqlite3.Connection, profile: dict[str, Any]) -> int:
    """Requests waiting for this seller to confirm a payment (for Today)."""
    if not life.is_adult(profile):
        return 0
    return kit.count(
        connection,
        "SELECT COUNT(*) FROM ln_sales WHERE owner_id = ? AND status = 'pending'",
        (profile["id"],),
    )


SALE_COLUMNS = (
    "reference",
    "deck_title",
    "buyer_name",
    "buyer_email",
    "amount",
    "currency",
    "status",
    "created_at",
    "paid_at",
)


def csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "amount": f"{row['amount_cents'] / 100:.2f}"} for row in rows]
