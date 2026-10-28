"""Marketplace: buy and sell with friends and neighbours on Social, and earn from things you no longer need.

* A **listing** is something an adult sells (or gives away): a title, a price in their currency, a category, its
  condition, where it can be picked up, up to six photos and, if they have one, their own payment link. It is for
  everyone on Social or only for friends.
* Buyers search and filter (words, category, highest price, newest or cheapest first), **save** listings, and
  **message the seller** about one: the chat starts with "Is it still available?" and stays open between the two
  adults even when they are not friends (a Marketplace chat).
* The seller marks it **reserved**, **sold** (to whom, for how much) or available again. "Earned from sales" adds
  up what they sold (a sold listing they remove still counts). A listing is the seller's own: only their own
  payment link is shown, and the public page carries their name. Someone who runs the account's business (the
  household's adults; hosted, the organization's owner, admin or coach: :func:`may_sell_for_business`) may
  instead sell **for the business**: the listing then uses the business's payment link and a sale is recorded in
  the Business hub revenue ledger ("available again" takes it back out). Aira never moves money: the buyer pays
  the seller's payment link or in person.
* A public listing can be **shared** as a page anyone opens without Aira (the photos, price, place and a
  "Pay" button for the seller's link), where a visitor says they are interested and leaves how to reach them.

Children never see the Marketplace; teens may look but only message sellers who are their friends; only adults
sell. Blocking, suspension and moderation work as everywhere in Social (a listing can be reported and hidden).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira import suite
from aira.suite import kit, life
from aira.suite.social import service, store
from aira.suite.social.store import new_id, row, rows, stamp

CATEGORIES = (
    "home",
    "clothes",
    "kids",
    "electronics",
    "vehicles",
    "sports",
    "books",
    "garden",
    "services",
    "other",
)
CONDITIONS = ("new", "like_new", "good", "used", "for_parts")
AUDIENCES = ("public", "friends")
STATUSES = ("available", "reserved", "sold")
SORTS = ("newest", "cheapest", "dearest")
LIMIT_PER_HOUR = 20
# Hosted roles that work in the organization's business apps (``suite.manage`` in aira_cloud.rbac).
BUSINESS_ROLES = ("owner", "admin", "coach")


def _adult(acct: dict[str, Any]) -> bool:
    return acct["age_group"] not in ("child", "teen")


def can_browse(acct: dict[str, Any]) -> bool:
    return acct["age_group"] != "child"


def may_sell_for_business(profile: dict[str, Any]) -> bool:
    """Whether this person may sell for the account's business (its payment link, its revenue ledger).

    Hosted, the business is the organization's: only the roles that run its business apps (owner, admin, coach).
    On this computer it is the household's: its adults (as for the Business hub). Everyone else sells as
    themselves."""
    member = suite.actor()
    if member is not None:
        return str(member.get("role") or "") in BUSINESS_ROLES
    return life.is_adult(profile)


def business_for(
    profile: dict[str, Any], business: dict[str, Any]
) -> dict[str, Any] | None:
    """The business this person may sell for (``{"name", "payment_link"}``), or None."""
    if not may_sell_for_business(profile):
        return None
    link = str(business.get("payment_link") or "")
    return {
        "name": str(business.get("name") or ""),
        "payment_link": link if link.lower().startswith("https://") else "",
    }


def payment_link(listing: dict[str, Any]) -> str:
    """The link a buyer pays: the seller's own, or the business's for a listing sold for the business."""
    return listing["payment_link"] or (
        listing["business_link"] if listing["for_business"] else ""
    )


def _seller_only(me_: dict[str, Any]) -> None:
    service._active(me_)
    if not _adult(me_):
        raise PermissionError("Selling on the Marketplace is for adults.")


def _row(sc: sqlite3.Connection, listing_id: str) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_listings WHERE id = ?",
        (store.check_id(listing_id, "listing"),),
    )
    if not found:
        raise LookupError("That listing was not found.")
    return found


def can_see(
    sc: sqlite3.Connection, viewer: dict[str, Any], listing: dict[str, Any]
) -> bool:
    if listing.get("deleted_at") or listing.get("hidden_at"):
        return False
    if listing["seller_id"] == viewer["id"]:
        return True
    if not can_browse(viewer):
        return False
    try:
        seller = service.account(sc, listing["seller_id"])
    except LookupError:
        return False
    if seller.get("suspended_at") or service.blocked(sc, viewer["id"], seller["id"]):
        return False
    if listing["audience"] == "friends":
        return service.are_friends(sc, viewer["id"], seller["id"])
    return True


def visible(
    sc: sqlite3.Connection, viewer: dict[str, Any], listing_id: str
) -> dict[str, Any]:
    listing = _row(sc, listing_id)
    if not can_see(sc, viewer, listing):
        raise LookupError("That listing was not found.")
    return listing


def mine(
    sc: sqlite3.Connection, me_: dict[str, Any], listing_id: str
) -> dict[str, Any]:
    listing = _row(sc, listing_id)
    if listing["seller_id"] != me_["id"] or listing["deleted_at"]:
        raise LookupError("That listing was not found.")
    return listing


def _values(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    if (
        "price" in raw
    ):  # the form's text ("" or 0 gives it away); "ten" or "12abc" is an error, never a guess
        price = kit.cents(raw["price"], "the price", default=0)
    else:
        price = kit.cents_value(merged.get("price_cents"), "the price", default=0)
    link = kit.url(merged.get("payment_link"), "your payment link")
    if link and not link.lower().startswith("https://"):
        raise ValueError("Your payment link must start with https://.")
    return {
        "title": kit.text(
            merged.get("title"), "what you are selling", max_len=100, required=True
        ),
        "about": kit.text(
            merged.get("about"), "a description", max_len=2000, multiline=True
        ),
        "price_cents": price,
        "category": kit.choice(
            merged.get("category"), "the category", CATEGORIES, default="other"
        ),
        "condition": kit.choice(
            merged.get("condition"), "the condition", CONDITIONS, default="used"
        ),
        "place": kit.text(merged.get("place"), "where to pick it up", max_len=80),
        "photos": store.dumps(service._photos(merged.get("photos"))),
        "audience": kit.choice(
            merged.get("audience"), "who can see it", AUDIENCES, default="public"
        ),
        "payment_link": link,
    }


def _business_values(
    raw: dict[str, Any], current: bool, business: dict[str, Any] | None
) -> dict[str, Any]:
    """``for_business`` and the business's payment link, refused for someone who does not run the business."""
    wanted = kit.flag(raw["for_business"]) if "for_business" in raw else current
    if wanted and business is None:
        raise PermissionError(
            "Only the people who run the business can sell for it. Sell it as yourself."
        )
    return {
        "for_business": 1 if wanted else 0,
        "business_link": (business or {}).get("payment_link", "") if wanted else "",
    }


def create(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    raw: dict[str, Any],
    *,
    currency: str = "USD",
    business: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A new listing, the seller's own (their payment link only) unless ``for_business`` and they may
    (``business`` from :func:`business_for`; None when they may not). ``currency`` is the default."""
    _seller_only(me_)
    values = _values(raw)
    values.update(_business_values(raw, False, business))
    values["title"] = service.filtered(sc, values["title"])
    values["about"] = service.filtered(sc, values["about"])
    _rate(sc, me_)
    listing_id = new_id()
    moment = stamp()
    sc.execute(
        """INSERT INTO so_listings (id, seller_id, title, about, price_cents, currency, category, condition, place, photos,
        audience, payment_link, for_business, business_link, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?)""",
        (
            listing_id,
            me_["id"],
            values["title"],
            values["about"],
            values["price_cents"],
            kit.currency(raw.get("currency") or currency),
            values["category"],
            values["condition"],
            values["place"],
            values["photos"],
            values["audience"],
            values["payment_link"],
            values["for_business"],
            values["business_link"],
            moment,
            moment,
        ),
    )
    sc.commit()
    return view(sc, me_, _row(sc, listing_id))


def _rate(sc: sqlite3.Connection, me_: dict[str, Any]) -> None:
    window = stamp()[:13]
    found = row(
        sc,
        "SELECT window, count FROM so_rate WHERE account_id = ? AND kind = 'listing'",
        (me_["id"],),
    )
    current = found["count"] if found and found["window"] == window else 0
    if current >= LIMIT_PER_HOUR:
        raise PermissionError(
            "You are listing a lot at once. Take a break and try again later."
        )
    sc.execute(
        """INSERT INTO so_rate (account_id, kind, window, count) VALUES (?, 'listing', ?, 1)
        ON CONFLICT(account_id, kind) DO UPDATE SET count = CASE WHEN window = excluded.window THEN count + 1 ELSE 1 END,
        window = excluded.window""",
        (me_["id"], window),
    )


def update(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    listing_id: str,
    raw: dict[str, Any],
    *,
    business: dict[str, Any] | None = None,
) -> dict[str, Any]:
    listing = mine(sc, me_, listing_id)
    service._active(me_)
    current = {**listing, "photos": store.loads(listing["photos"], [])}
    values = _values(raw, current)
    if "for_business" in raw or business is not None:
        values.update(_business_values(raw, bool(listing["for_business"]), business))
    elif listing[
        "for_business"
    ]:  # they no longer run the business: the listing becomes their own
        values.update({"for_business": 0, "business_link": ""})
    values["title"] = service.filtered(sc, values["title"])
    values["about"] = service.filtered(sc, values["about"])
    values["updated_at"] = stamp()
    service._update(sc, "so_listings", listing["id"], values)
    sc.commit()
    return view(sc, me_, _row(sc, listing["id"]))


def set_status(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    listing_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Reserved, sold (to whom, for how much: the listed price unless told) or available again."""
    listing = mine(sc, me_, listing_id)
    status = kit.choice(raw.get("status"), "the status", STATUSES)
    values: dict[str, Any] = {"status": status, "updated_at": stamp()}
    if status == "sold":
        if "sold_price" in raw and raw.get("sold_price") not in (None, ""):
            values["sold_cents"] = kit.cents(raw["sold_price"], "the price you got")
        else:
            values["sold_cents"] = kit.cents_value(
                raw.get("sold_cents"),
                "the price you got",
                default=listing["price_cents"],
            )
        values["buyer"] = kit.text(raw.get("buyer"), "who bought it", max_len=80)
        values["sold_at"] = (
            listing["sold_at"] if listing["status"] == "sold" else stamp()
        )
    else:
        values.update({"sold_cents": 0, "buyer": "", "sold_at": None})
    service._update(sc, "so_listings", listing["id"], values)
    sc.commit()
    return view(sc, me_, _row(sc, listing["id"]))


def delete(
    sc: sqlite3.Connection, me_: dict[str, Any], listing_id: str
) -> dict[str, Any]:
    listing = mine(sc, me_, listing_id)
    moment = stamp()
    sc.execute(
        "UPDATE so_listings SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (moment, moment, listing["id"]),
    )
    sc.execute("DELETE FROM so_listing_saves WHERE listing_id = ?", (listing["id"],))
    # its Marketplace chats go back to the ordinary rules for a one-to-one chat
    sc.execute(
        "UPDATE so_chats SET context = '', listing_id = '' WHERE listing_id = ?",
        (listing["id"],),
    )
    sc.commit()
    return listing


def save(
    sc: sqlite3.Connection, me_: dict[str, Any], listing_id: str, on: bool
) -> dict[str, Any]:
    listing = visible(sc, me_, listing_id)
    if on:
        sc.execute(
            "INSERT OR IGNORE INTO so_listing_saves (account_id, listing_id, created_at) VALUES (?, ?, ?)",
            (me_["id"], listing["id"], stamp()),
        )
    else:
        sc.execute(
            "DELETE FROM so_listing_saves WHERE account_id = ? AND listing_id = ?",
            (me_["id"], listing["id"]),
        )
    sc.commit()
    return {"saved": on}


def seen(sc: sqlite3.Connection, me_: dict[str, Any], listing: dict[str, Any]) -> None:
    """Someone other than the seller opened it (the seller sees how many times)."""
    if listing["seller_id"] != me_["id"]:
        sc.execute(
            "UPDATE so_listings SET views = views + 1 WHERE id = ?", (listing["id"],)
        )
        sc.commit()


def view(
    sc: sqlite3.Connection, viewer: dict[str, Any], listing: dict[str, Any]
) -> dict[str, Any]:
    seller = service.account(sc, listing["seller_id"])
    own = listing["seller_id"] == viewer["id"]
    out: dict[str, Any] = {
        "id": listing["id"],
        "title": listing["title"],
        "about": listing["about"],
        "price_cents": listing["price_cents"],
        "currency": listing["currency"],
        "price": "Free"
        if not listing["price_cents"]
        else kit.fmt_money(listing["price_cents"], listing["currency"]),
        "category": listing["category"],
        "condition": listing["condition"],
        "place": listing["place"],
        "photos": store.loads(listing["photos"], []),
        "audience": listing["audience"],
        "payment_link": payment_link(listing),
        "for_business": bool(listing["for_business"]),
        "status": listing["status"],
        "seller": service.mini(seller),
        "mine": own,
        "saved": bool(
            row(
                sc,
                "SELECT 1 AS x FROM so_listing_saves WHERE account_id = ? AND listing_id = ?",
                (viewer["id"], listing["id"]),
            )
        ),
        "can_message": not own
        and (_adult(viewer) or service.are_friends(sc, viewer["id"], seller["id"]))
        and not viewer.get("suspended_at"),
        "created_at": listing["created_at"],
        "updated_at": listing["updated_at"],
    }
    if own:
        out.update(
            {
                "views": listing["views"],
                "own_payment_link": listing["payment_link"],
                "buyer": listing["buyer"],
                "sold_cents": listing["sold_cents"],
                "sold": kit.fmt_money(listing["sold_cents"], listing["currency"])
                if listing["status"] == "sold"
                else "",
                "sold_at": listing["sold_at"],
                "saves": kit.count(
                    sc,
                    "SELECT COUNT(*) FROM so_listing_saves WHERE listing_id = ?",
                    (listing["id"],),
                ),
            }
        )
    return out


def browse(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    *,
    q: str = "",
    category: str = "",
    max_price: Any = "",
    sort: str = "newest",
    limit: int = 60,
) -> dict[str, Any]:
    """What is for sale (available or reserved) that this person may see, filtered and sorted."""
    if not can_browse(me_):
        raise PermissionError("Ask a grown-up: the Marketplace is for older people.")
    words = kit.text(q, "a search", max_len=60).lower()
    category = (
        kit.choice(category, "the category", CATEGORIES, default="") if category else ""
    )
    sort = kit.choice(sort, "the order", SORTS, default="newest")
    ceiling = (
        kit.cents(max_price, "the highest price")
        if str(max_price or "").strip()
        else None
    )
    where = [
        "deleted_at IS NULL",
        "hidden_at IS NULL",
        "status IN ('available', 'reserved')",
    ]
    params: list[Any] = []
    if words:
        where.append(
            "(lower(title) LIKE ? OR lower(about) LIKE ? OR lower(place) LIKE ?)"
        )
        like = f"%{words}%"
        params += [like, like, like]
    if category:
        where.append("category = ?")
        params.append(category)
    if ceiling is not None:
        where.append("price_cents <= ?")
        params.append(ceiling)
    order = {
        "newest": "created_at DESC",
        "cheapest": "price_cents ASC, created_at DESC",
        "dearest": "price_cents DESC, created_at DESC",
    }[sort]
    found = rows(
        sc,
        f"SELECT * FROM so_listings WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 400",  # noqa: S608 - fixed clauses
        tuple(params),
    )
    out = []
    for item in found:
        if item["seller_id"] != me_["id"] and can_see(sc, me_, item):
            out.append(view(sc, me_, item))
        if len(out) >= max(1, min(limit, 100)):
            break
    return {
        "listings": out,
        "categories": list(CATEGORIES),
        "conditions": list(CONDITIONS),
    }


def selling(sc: sqlite3.Connection, me_: dict[str, Any]) -> dict[str, Any]:
    """The seller's own listings and what selling has earned them (per currency). A sold listing they removed
    still counts (the money was earned, and its line stays in the Business hub too)."""
    found = rows(
        sc,
        "SELECT * FROM so_listings WHERE seller_id = ? AND deleted_at IS NULL ORDER BY status = 'sold', created_at DESC LIMIT 200",
        (me_["id"],),
    )
    sales = rows(
        sc,
        "SELECT currency, sold_cents FROM so_listings WHERE seller_id = ? AND status = 'sold'",
        (me_["id"],),
    )
    earned: dict[str, int] = {}
    for item in sales:
        if item["sold_cents"]:
            earned[item["currency"]] = (
                earned.get(item["currency"], 0) + item["sold_cents"]
            )
    listings = [view(sc, me_, item) for item in found]
    return {
        "listings": listings,
        "summary": {
            "for_sale": sum(1 for item in found if item["status"] == "available"),
            "reserved": sum(1 for item in found if item["status"] == "reserved"),
            "sold": len(sales),
            "views": sum(item["views"] for item in found if item["status"] != "sold"),
            "earned": [
                {"currency": code, "cents": cents, "text": kit.fmt_money(cents, code)}
                for code, cents in sorted(earned.items())
            ],
        },
    }


def saved(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rows(
        sc,
        """SELECT l.* FROM so_listing_saves s JOIN so_listings l ON l.id = s.listing_id
        WHERE s.account_id = ? ORDER BY s.created_at DESC LIMIT 100""",
        (me_["id"],),
    ):
        if can_see(sc, me_, item):
            out.append(view(sc, me_, item))
    return out


def ask_seller(
    sc: sqlite3.Connection, me_: dict[str, Any], listing_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Open (or reopen) a chat with the seller about this listing and send the first message."""
    listing = visible(sc, me_, listing_id)
    if listing["seller_id"] == me_["id"]:
        raise ValueError("This is your own listing.")
    if listing["status"] == "sold":
        raise ValueError("This has been sold.")
    seller = service.account(sc, listing["seller_id"])
    if not _adult(me_) and not service.are_friends(sc, me_["id"], seller["id"]):
        raise PermissionError("You can message sellers who are your friends.")
    service._active(me_)
    x, y = service._pair(me_["id"], seller["id"])
    key = f"{x}:{y}"
    chat = row(sc, "SELECT * FROM so_chats WHERE direct_key = ?", (key,))
    moment = stamp()
    if chat:
        sc.execute(
            "UPDATE so_chats SET listing_id = ?, context = CASE WHEN context = '' THEN 'market' ELSE context END WHERE id = ?",
            (listing["id"], chat["id"]),
        )
        sc.execute(
            "UPDATE so_chat_members SET left_at = NULL WHERE chat_id = ? AND account_id = ?",
            (chat["id"], me_["id"]),
        )
        chat_id = chat["id"]
    else:
        chat_id = new_id()
        sc.execute(
            """INSERT INTO so_chats (id, kind, title, direct_key, created_by, created_at, updated_at, context, listing_id)
            VALUES (?, 'direct', '', ?, ?, ?, ?, 'market', ?)""",
            (chat_id, key, me_["id"], moment, moment, listing["id"]),
        )
        for member in (me_["id"], seller["id"]):
            sc.execute(
                "INSERT INTO so_chat_members (chat_id, account_id, role, joined_at, last_read_at) VALUES (?, ?, 'member', ?, ?)",
                (chat_id, member, moment, moment if member == me_["id"] else ""),
            )
    sc.commit()
    text = kit.text(raw.get("text"), "a message", max_len=1000, multiline=True) or (
        f"Hi! Is “{listing['title']}” still available?"
    )
    service.send(sc, me_, chat_id, {"text": text})
    found = row(sc, "SELECT * FROM so_chats WHERE id = ?", (chat_id,)) or {}
    return service.chat_summary(sc, me_, found)


def public_page(sc: sqlite3.Connection, listing_id: str) -> dict[str, Any]:
    """What a visitor without Aira sees: only a public listing that is still for sale, from an adult."""
    try:
        listing = _row(sc, listing_id)
        seller = service.account(sc, listing["seller_id"])
    except LookupError:
        return {"available": False, "title": "This listing is no longer shared."}
    if (
        listing["deleted_at"]
        or listing["hidden_at"]
        or listing["audience"] != "public"
        or seller.get("suspended_at")
        or not _adult(seller)
    ):
        return {"available": False, "title": "This listing is no longer shared."}
    return {
        "available": True,
        "title": listing["title"],
        "listing": {
            "title": listing["title"],
            "about": listing["about"],
            "price": "Free"
            if not listing["price_cents"]
            else kit.fmt_money(listing["price_cents"], listing["currency"]),
            "price_cents": listing["price_cents"],
            "category": listing["category"],
            "condition": listing["condition"],
            "place": listing["place"],
            "photos": store.loads(listing["photos"], []),
            "status": listing["status"],
            "payment_link": payment_link(listing)
            if listing["status"] != "sold"
            else "",
            # a personal listing is headed with the seller's name, not the account's business
            "for_business": bool(listing["for_business"]),
            "seller": {"name": seller["display_name"], "avatar": seller["avatar"]},
            "created_at": listing["created_at"],
        },
    }


def interested(
    sc: sqlite3.Connection, listing_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """A visitor of the public page says they want it: the seller gets a notification with how to reach them."""
    page = public_page(sc, listing_id)
    if not page["available"] or page["listing"]["status"] == "sold":
        raise LookupError("This listing is no longer for sale.")
    name = kit.text(payload.get("name"), "your name", max_len=60, required=True)
    contact = kit.text(
        payload.get("contact"), "how to reach you", max_len=120, required=True
    )
    note = kit.text(payload.get("message"), "a message", max_len=500, multiline=True)
    listing = _row(sc, listing_id)
    text = f"{name} is interested in “{listing['title']}”. Reach them at {contact}." + (
        f" They said: {note}" if note else ""
    )
    sc.execute(
        """INSERT INTO so_notifications (id, account_id, kind, actor_id, ref_type, ref_id, text, created_at)
        VALUES (?, ?, 'market_interest', '', 'listing', ?, ?, ?)""",
        (
            new_id(),
            listing["seller_id"],
            listing["id"],
            service.filtered(sc, text)[:300],
            stamp(),
        ),
    )
    sc.commit()
    return {"sent": True}
