"""Your newsletter: an adult picks the stories that matter to their readers, adds a line on why, and publishes an
issue. Readers subscribe on the newsletter's public page and get every issue by email. That is how a person earns
from News: a local digest ("what happened in our town this week"), a trade digest (prices, tenders and rules for
farmers or shopkeepers), a study digest for exam candidates.

* A free newsletter: anyone subscribes with their name and email and gets every issue; the page shows every issue.
* A paid newsletter (a monthly price): the page shows the first story of each issue as a preview. A reader asks to
  subscribe, pays through the writer's own payment link or instructions with a reference (Aira does not take card
  payments), and the writer marks the payment received: the money goes into the revenue ledger (Business hub), the
  reader is paid up for 30 more days and gets an access code by email that opens every issue on the page. "Undo
  payment" takes the last payment out of the ledger.
* Issues go to subscribers through the outbox (Business hub > Outbox): they wait for approval unless the kind
  ``newsletter_issue`` is listed under automatic sending.
* A reader stops with their email and code on the page (one still waiting to pay uses their payment reference); the
  writer can remove anyone.
* The page is made with the first issue. Once the writer turns it off it stays off (publishing or confirming a
  payment never brings it back) until they create a new link.
* A free newsletter that gets a price tells its free readers by email: they keep their place, and get issues again
  once they pay. A paid newsletter made free lets everyone waiting to pay in.

Children cannot run a newsletter (no money, no public pages). In the hosted build each member can have their own.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import timedelta
from typing import Any

from aira.suite import kit, life, public
from aira.suite.news import store

APP = store.APP
KIND = "newsletter"
PERIOD = timedelta(days=30)
MAX_PRICE_CENTS = 10_000_00
MAX_PICKS = 20
PENDING_CAP = 300  # open requests a newsletter holds (stops a flood from one page)
DAILY_CAP = 50  # new readers or requests a newsletter takes in a day (the page cannot be used to mail strangers)
PUBLIC_ISSUES = 12
STATUSES = ("active", "pending", "paid", "cancelled")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread


def _hash(code: str) -> str:
    clean = code.strip().upper().replace("-", "").replace(" ", "")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _reference() -> str:
    return "NW-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def _adult(me: dict[str, Any]) -> None:
    if not life.is_adult(me):
        raise PermissionError("Newsletters are for adults.")


def _money(connection: sqlite3.Connection, cents: int, code: str = "") -> str:
    return kit.fmt_money(cents, code or kit.business(connection)["currency"])


# ---------------------------------------------------------------------------
# the newsletter itself
# ---------------------------------------------------------------------------


def mine(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection, "SELECT * FROM nw_letters WHERE owner_id = ?", (me["id"],)
    )


def _own(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    _adult(me)
    row = mine(connection, me)
    if row is None:
        raise ValueError("Start your newsletter first: give it a name.")
    return row


def is_paid(row: dict[str, Any]) -> bool:
    return int(row.get("price_cents") or 0) > 0


def save(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    *,
    source: Any = None,
) -> dict[str, Any]:
    """Start the newsletter or change its name, description and monthly price (0 makes it free)."""
    _adult(me)
    row = mine(connection, me)
    name = kit.text(
        raw.get("name", (row or {}).get("name")),
        "a name for your newsletter",
        max_len=80,
        required=True,
    )
    about = kit.text(
        raw.get("about", (row or {}).get("about", "")),
        "what it is about",
        max_len=500,
        multiline=True,
    )
    if "price_cents" in raw:
        # No default: a price that could not be read arrives empty and must not make a paid newsletter free.
        price = kit.cents_value(
            raw.get("price_cents"), "the monthly price (a number like 2.50)", minimum=0
        )
    else:
        price = int((row or {}).get("price_cents") or 0)
    if price > MAX_PRICE_CENTS:
        raise ValueError("The price is too high (at most 10,000 a month).")
    currency = kit.business(connection)["currency"]
    stamp = kit.now()
    freed = told = 0
    if row is None:
        connection.execute(
            """INSERT INTO nw_letters (id, owner_id, name, about, price_cents, currency, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (kit.new_id(), me["id"], name, about, price, currency, stamp, stamp),
        )
        kit.log(connection, APP, "newsletter", f"{me['name']} started {name}")
    else:
        connection.execute(
            "UPDATE nw_letters SET name = ?, about = ?, price_cents = ?, currency = ?, updated_at = ? WHERE id = ?",
            (name, about, price, currency, stamp, row["id"]),
        )
        changed = {**row, "name": name, "price_cents": price, "currency": currency}
        if is_paid(row) and not price:
            # Free now: everyone waiting to pay is in (with a code and a welcome email).
            for reader in kit.fetch_all(
                connection,
                "SELECT * FROM nw_subscribers WHERE letter_id = ? AND status = 'pending'",
                (row["id"],),
            ):
                _make_free(connection, changed, reader, source)
                freed += 1
        elif price and not is_paid(row):
            # Paid now: free readers stop getting issues until they pay, so each one is told (never silently).
            for reader in kit.fetch_all(
                connection,
                "SELECT * FROM nw_subscribers WHERE letter_id = ? AND status = 'active'",
                (row["id"],),
            ):
                _tell_price(connection, changed, reader, source)
                told += 1
    connection.commit()
    letter = mine(connection, me) or {}
    link = public.link_for(connection, APP, KIND, letter["id"])
    if link and link["title"] != name:
        public.create_link(connection, APP, KIND, letter["id"], title=name)
    return {**letter, "freed": freed, "told": told}


def _tell_price(
    connection: sqlite3.Connection,
    letter: dict[str, Any],
    reader: dict[str, Any],
    source: Any = None,
) -> None:
    """A free reader, now that the newsletter has a price: what it costs and how to keep reading."""
    link = public.link_for(connection, APP, KIND, letter["id"])
    price = _money(connection, int(letter["price_cents"]), letter["currency"])
    how = (
        "To keep getting it, open its page and press Subscribe with this email address:\n"
        f"{kit.public_url(link['path'], source)}"
        if link
        else "To keep getting it, reply to this email and ask how to pay."
    )
    kit.queue_email(
        connection,
        APP,
        "newsletter_price",
        to=reader["email"],
        subject=f"{letter['name']} now costs {price} a month",
        body=(
            f"Hello {reader['name']},\n\nThank you for reading {letter['name']}. From now on it costs {price} a "
            f"month, and new issues go to readers who have paid.\n\n{how}\n\nIf you do nothing, you simply get "
            "no more issues."
        ),
        ref_type="newsletter_subscriber",
        ref_id=reader["id"],
    )


def _page_link(
    connection: sqlite3.Connection, letter: dict[str, Any]
) -> dict[str, Any] | None:
    """The newsletter's live page link. Made on first use, but never again after the writer turned it off."""
    link = public.link_for(connection, APP, KIND, letter["id"])
    if link:
        return link
    if _turned_off(connection, letter):
        return None
    return public.create_link(connection, APP, KIND, letter["id"], title=letter["name"])


def _turned_off(connection: sqlite3.Connection, letter: dict[str, Any]) -> bool:
    """The writer turned the page off (and has not made a new link since)."""
    if public.link_for(connection, APP, KIND, letter["id"]):
        return False
    return bool(
        kit.count(
            connection,
            "SELECT COUNT(*) FROM suite_public_links WHERE app = ? AND kind = ? AND ref_id = ? AND revoked_at IS NOT NULL",
            (APP, KIND, letter["id"]),
        )
    )


def share(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    letter = _own(connection, me)
    return public.create_link(connection, APP, KIND, letter["id"], title=letter["name"])


def unshare(connection: sqlite3.Connection, me: dict[str, Any]) -> None:
    letter = _own(connection, me)
    public.revoke(connection, APP, KIND, letter["id"])


def _numbers(connection: sqlite3.Connection, letter: dict[str, Any]) -> dict[str, Any]:
    now = kit.now()
    counts = (
        kit.fetch_one(
            connection,
            """SELECT COUNT(CASE WHEN status = 'active' THEN 1 END) AS free,
            COUNT(CASE WHEN status = 'paid' AND paid_until > ? THEN 1 END) AS paid,
            COUNT(CASE WHEN status = 'paid' AND paid_until <= ? THEN 1 END) AS lapsed,
            COUNT(CASE WHEN status = 'pending' THEN 1 END) AS waiting
            FROM nw_subscribers WHERE letter_id = ?""",
            (now, now, letter["id"]),
        )
        or {}
    )
    money = (
        kit.fetch_one(
            connection,
            """SELECT COALESCE(SUM(amount_cents), 0) AS earned,
            COALESCE(SUM(CASE WHEN substr(paid_at, 1, 7) = ? THEN amount_cents END), 0) AS month
            FROM nw_payments WHERE letter_id = ? AND voided_at IS NULL""",
            (now[:7], letter["id"]),
        )
        or {}
    )
    return {
        "free": int(counts.get("free") or 0),
        "paid": int(counts.get("paid") or 0),
        "lapsed": int(counts.get("lapsed") or 0),
        "waiting": int(counts.get("waiting") or 0),
        "earned_cents": int(money.get("earned") or 0),
        "month_cents": int(money.get("month") or 0),
    }


def overview(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """Everything the newsletter tab shows: the newsletter, its link, the next issue's picks, issues and numbers."""
    _adult(me)
    business = kit.business(connection)
    letter = mine(connection, me)
    base = {
        "currency": business["currency"],
        "payment_ready": bool(
            business.get("payment_link") or business.get("payment_instructions")
        ),
    }
    if letter is None:
        return {**base, "newsletter": None}
    issues = kit.fetch_all(
        connection,
        """SELECT i.id, i.number, i.title, i.intro, i.emailed, i.published_at,
        (SELECT COUNT(*) FROM nw_picks p WHERE p.issue_id = i.id) AS stories
        FROM nw_issues i WHERE i.letter_id = ? ORDER BY i.number DESC LIMIT 50""",
        (letter["id"],),
    )
    return {
        **base,
        "newsletter": {
            k: letter[k] for k in ("id", "name", "about", "price_cents", "currency")
        },
        "link": public.link_for(connection, APP, KIND, letter["id"]),
        "page_off": _turned_off(connection, letter),
        "picks": picks(connection, letter),
        "issues": issues,
        "numbers": _numbers(connection, letter),
        "recipients": len(_recipients(connection, letter)),
    }


# ---------------------------------------------------------------------------
# picks: the stories of the next issue
# ---------------------------------------------------------------------------


def picks(
    connection: sqlite3.Connection, letter: dict[str, Any]
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        """SELECT id, title, link, source, note, added_at FROM nw_picks
        WHERE letter_id = ? AND issue_id IS NULL ORDER BY position, added_at""",
        (letter["id"],),
    )


def add_pick(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Put an article (by its id) in the next issue, with an optional note on why it matters."""
    letter = _own(connection, me)
    article_id = str(raw.get("article_id") or "")
    article = kit.fetch_one(
        connection, "SELECT * FROM nw_articles WHERE id = ?", (article_id,)
    )
    if not article:
        raise LookupError("That article was not found.")
    note = kit.text(raw.get("note"), "your note", max_len=500, multiline=True)
    current = picks(connection, letter)
    same = next((p for p in current if p["link"] == article["link"]), None)
    if same:
        if note:
            connection.execute(
                "UPDATE nw_picks SET note = ? WHERE id = ?", (note, same["id"])
            )
            connection.commit()
        return next(p for p in picks(connection, letter) if p["id"] == same["id"])
    if len(current) >= MAX_PICKS:
        raise ValueError(
            f"An issue holds up to {MAX_PICKS} stories. Publish it or remove one."
        )
    pick_id = kit.new_id()
    connection.execute(
        """INSERT INTO nw_picks (id, letter_id, issue_id, title, link, source, note, position, added_at)
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        (
            pick_id,
            letter["id"],
            article["title"],
            article["link"],
            article["source"],
            note,
            len(current),
            kit.now(),
        ),
    )
    connection.commit()
    return next(p for p in picks(connection, letter) if p["id"] == pick_id)


def _own_pick(
    connection: sqlite3.Connection, letter: dict[str, Any], pick_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM nw_picks WHERE id = ? AND letter_id = ? AND issue_id IS NULL",
        (pick_id, letter["id"]),
    )
    if not row:
        raise LookupError("That story is not in your next issue.")
    return row


def update_pick(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    pick_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Change a story's note, or move it (``move``: "up" or "down")."""
    letter = _own(connection, me)
    row = _own_pick(connection, letter, pick_id)
    if "note" in raw:
        note = kit.text(raw.get("note"), "your note", max_len=500, multiline=True)
        connection.execute(
            "UPDATE nw_picks SET note = ? WHERE id = ?", (note, row["id"])
        )
    move = str(raw.get("move") or "")
    if move:
        if move not in {"up", "down"}:
            raise ValueError("Move a story up or down.")
        order = [p["id"] for p in picks(connection, letter)]
        at = order.index(row["id"])
        to = at - 1 if move == "up" else at + 1
        if 0 <= to < len(order):
            order[at], order[to] = order[to], order[at]
        for position, pid in enumerate(order):
            connection.execute(
                "UPDATE nw_picks SET position = ? WHERE id = ?", (position, pid)
            )
    connection.commit()
    return next(p for p in picks(connection, letter) if p["id"] == row["id"])


def remove_pick(
    connection: sqlite3.Connection, me: dict[str, Any], pick_id: str
) -> None:
    letter = _own(connection, me)
    row = _own_pick(connection, letter, pick_id)
    connection.execute("DELETE FROM nw_picks WHERE id = ?", (row["id"],))
    connection.commit()


def suggest_intro(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """A title and a short opening for the next issue (the writing model, or a plain template)."""
    letter = _own(connection, me)
    chosen = picks(connection, letter)
    if not chosen:
        raise ValueError("Add a story to the next issue first.")
    number = _next_number(connection, letter)
    fallback = {
        "title": f"{letter['name']}, issue {number}",
        "intro": (
            f"Hello! Here {'is the story' if len(chosen) == 1 else f'are the {len(chosen)} stories'} "
            f"I picked for you this time, starting with: {chosen[0]['title']}."
        ),
    }
    listed = "\n".join(
        f"- {p['title']}" + (f" (my note: {p['note']})" if p["note"] else "")
        for p in chosen
    )
    prompt = (
        f"You help {me['name']} write their newsletter called {letter['name']!r}"
        + (f" ({letter['about']})" if letter["about"] else "")
        + ". Write a short, warm title (at most 8 words) and an opening of two or three plain sentences for "
        "this issue, using only these stories. No opinions beyond the notes. Answer as JSON "
        '{"title": "...", "intro": "..."}.\n\nStories:\n' + listed
    )
    drafted = kit.draft(prompt, fallback)
    return {
        "title": str(drafted.get("title") or fallback["title"])[:120],
        "intro": str(drafted.get("intro") or fallback["intro"])[:2000],
        "source": drafted.get("source", "template"),
    }


# ---------------------------------------------------------------------------
# issues
# ---------------------------------------------------------------------------


def _next_number(connection: sqlite3.Connection, letter: dict[str, Any]) -> int:
    return (
        kit.count(
            connection,
            "SELECT COALESCE(MAX(number), 0) FROM nw_issues WHERE letter_id = ?",
            (letter["id"],),
        )
        + 1
    )


def _recipients(
    connection: sqlite3.Connection, letter: dict[str, Any]
) -> list[dict[str, Any]]:
    """Who gets an issue: every subscriber of a free newsletter; readers paid up to now for a paid one."""
    if is_paid(letter):
        return kit.fetch_all(
            connection,
            "SELECT * FROM nw_subscribers WHERE letter_id = ? AND status = 'paid' AND paid_until > ? ORDER BY created_at",
            (letter["id"], kit.now()),
        )
    return kit.fetch_all(
        connection,
        "SELECT * FROM nw_subscribers WHERE letter_id = ? AND status IN ('active', 'paid') ORDER BY created_at",
        (letter["id"],),
    )


def _issue_email(
    letter: dict[str, Any],
    issue: dict[str, Any],
    stories: list[dict[str, Any]],
    reader: dict[str, Any],
    page: str,
) -> tuple[str, str]:
    lines = [f"Hello {reader['name']},", ""]
    if issue["intro"]:
        lines += [issue["intro"], ""]
    for n, story in enumerate(stories, 1):
        lines.append(
            f"{n}. {story['title']}"
            + (f" ({story['source']})" if story["source"] else "")
        )
        if story["note"]:
            lines.append(f"   {story['note']}")
        lines.append(f"   {story['link']}")
        lines.append("")
    if page:
        lines += [
            f"Every issue is on the newsletter's page: {page}",
            "To stop getting this newsletter, open that page and use your email and access code, or reply to "
            "this email.",
        ]
    else:
        lines.append("To stop getting this newsletter, reply to this email.")
    return f"{letter['name']}: {issue['title']}", "\n".join(lines)


def publish(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    *,
    source: Any = None,
) -> dict[str, Any]:
    """Publish the next issue: it appears on the page, and every reader who should get it gets it by email."""
    letter = _own(connection, me)
    chosen = picks(connection, letter)
    if not chosen:
        raise ValueError("Add at least one story to the issue first.")
    number = _next_number(connection, letter)
    title = kit.text(raw.get("title"), "a title", max_len=120) or (
        f"{letter['name']}, issue {number}"
    )
    intro = kit.text(raw.get("intro"), "an opening", max_len=2000, multiline=True)
    issue = {
        "id": kit.new_id(),
        "letter_id": letter["id"],
        "number": number,
        "title": title,
        "intro": intro,
        "published_at": kit.now(),
    }
    connection.execute(
        """INSERT INTO nw_issues (id, letter_id, number, title, intro, emailed, published_at)
        VALUES (:id, :letter_id, :number, :title, :intro, 0, :published_at)""",
        issue,
    )
    connection.execute(
        "UPDATE nw_picks SET issue_id = ? WHERE letter_id = ? AND issue_id IS NULL",
        (issue["id"], letter["id"]),
    )
    link = _page_link(connection, letter)
    page = kit.public_url(link["path"], source) if link else ""
    emailed = 0
    for reader in _recipients(connection, letter):
        subject, body = _issue_email(letter, issue, chosen, reader, page)
        if kit.queue_email(
            connection,
            APP,
            "newsletter_issue",
            to=reader["email"],
            subject=subject,
            body=body,
            ref_type="newsletter_issue",
            ref_id=issue["id"],
        ):
            emailed += 1
    connection.execute(
        "UPDATE nw_issues SET emailed = ? WHERE id = ?", (emailed, issue["id"])
    )
    kit.log(
        connection,
        APP,
        "issue",
        f"{letter['name']} issue {number} published ({len(chosen)} stories)",
        ref_type="newsletter_issue",
        ref_id=issue["id"],
        public=True,
    )
    connection.commit()
    return {**issue, "stories": len(chosen), "emailed": emailed, "link": link}


def delete_issue(
    connection: sqlite3.Connection, me: dict[str, Any], issue_id: str
) -> None:
    """Take an issue off the page (emails already sent stay sent)."""
    letter = _own(connection, me)
    row = kit.fetch_one(
        connection,
        "SELECT id FROM nw_issues WHERE id = ? AND letter_id = ?",
        (issue_id, letter["id"]),
    )
    if not row:
        raise LookupError("That issue was not found.")
    connection.execute("DELETE FROM nw_picks WHERE issue_id = ?", (issue_id,))
    connection.execute("DELETE FROM nw_issues WHERE id = ?", (issue_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# the public page: read, subscribe, open with a code, stop
# ---------------------------------------------------------------------------


def _public_issues(
    connection: sqlite3.Connection, letter: dict[str, Any], *, full: bool
) -> list[dict[str, Any]]:
    issues = kit.fetch_all(
        connection,
        """SELECT id, number, title, intro, published_at FROM nw_issues WHERE letter_id = ?
        ORDER BY number DESC LIMIT ?""",
        (letter["id"], PUBLIC_ISSUES),
    )
    out = []
    for issue in issues:
        stories = kit.fetch_all(
            connection,
            "SELECT title, link, source, note FROM nw_picks WHERE issue_id = ? ORDER BY position, added_at",
            (issue["id"],),
        )
        shown = stories if full else stories[:1]
        out.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "intro": issue["intro"],
                "published_at": issue["published_at"],
                "stories": shown,
                "more": len(stories) - len(shown),
            }
        )
    return out


def page_data(connection: sqlite3.Connection, letter: dict[str, Any]) -> dict[str, Any]:
    """What a visitor sees: every issue of a free newsletter; for a paid one the first story of each as a preview."""
    paid = is_paid(letter)
    return {
        "name": letter["name"],
        "about": letter["about"],
        "writer": life.name_of(connection, letter["owner_id"]),
        "paid": paid,
        "price_cents": int(letter["price_cents"] or 0),
        "currency": letter["currency"] or kit.business(connection)["currency"],
        "issues": _public_issues(connection, letter, full=not paid),
    }


def subscribe(
    connection: sqlite3.Connection,
    letter: dict[str, Any],
    payload: dict[str, Any],
    *,
    visitor: Any = None,
) -> dict[str, Any]:
    """A visitor subscribes. Free: they are in, and a welcome email carries their code. Paid: a request to pay.

    Asking again with the same email gives back the same request (or says they are already subscribed): nothing
    about another reader is ever shown.
    """
    name = kit.text(payload.get("name"), "your name", max_len=80, required=True)
    address = kit.email(payload.get("email"), "your email address", required=True)
    existing = kit.fetch_one(
        connection,
        "SELECT * FROM nw_subscribers WHERE letter_id = ? AND lower(email) = lower(?) AND status != 'cancelled'",
        (letter["id"], address),
    )
    paid = is_paid(letter)
    if existing and not paid:
        if existing["status"] == "pending":  # the newsletter became free: they are in
            _make_free(connection, letter, existing, visitor)
            connection.commit()
        return {"status": "subscribed", "already": True}
    if existing:
        paid_up = (
            existing["status"] == "paid" and (existing["paid_until"] or "") > kit.now()
        )
        if paid_up:
            return {"status": "subscribed", "already": True}
        if (
            existing["status"] == "active"
        ):  # joined while it was free: now it asks to pay
            connection.execute(
                "UPDATE nw_subscribers SET status = 'pending' WHERE id = ?",
                (existing["id"],),
            )
            connection.commit()
        return _request_view(
            letter, existing
        )  # a lapsed reader pays again with the same reference
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM nw_subscribers WHERE letter_id = ? AND created_at > ?",
            (letter["id"], kit.iso(kit.utcnow() - timedelta(days=1))),
        )
        >= DAILY_CAP
    ):
        raise ValueError(
            "This newsletter has had many new readers today. Please try again tomorrow."
        )
    if paid and (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM nw_subscribers WHERE letter_id = ? AND status = 'pending'",
            (letter["id"],),
        )
        >= PENDING_CAP
    ):
        raise ValueError(
            "The writer has many requests waiting. Please try again in a few days."
        )
    code = new_code()
    row = {
        "id": kit.new_id(),
        "letter_id": letter["id"],
        "name": name,
        "email": address,
        "status": "pending" if paid else "active",
        "reference": _reference(),
        "code_hash": "" if paid else _hash(code),
        "created_at": kit.now(),
    }
    connection.execute(
        """INSERT INTO nw_subscribers (id, letter_id, name, email, status, reference, code_hash, created_at)
        VALUES (:id, :letter_id, :name, :email, :status, :reference, :code_hash, :created_at)""",
        row,
    )
    if paid:
        kit.log(
            connection,
            APP,
            "subscribe_request",
            f"{name} asked to subscribe to {letter['name']}",
            ref_type="newsletter_subscriber",
            ref_id=row["id"],
            amount_cents=int(letter["price_cents"]),
            public=True,
        )
        connection.commit()
        return _request_view(letter, row)
    _welcome(connection, letter, row, code, visitor)
    kit.log(
        connection,
        APP,
        "subscribed",
        f"{name} subscribed to {letter['name']}",
        ref_type="newsletter_subscriber",
        ref_id=row["id"],
        public=True,
    )
    connection.commit()
    return {"status": "subscribed", "already": False}


def _welcome(
    connection: sqlite3.Connection,
    letter: dict[str, Any],
    reader: dict[str, Any],
    code: str,
    source: Any = None,
) -> None:
    """The email a free reader gets: they are in, and their code (to stop the newsletter themselves)."""
    link = public.link_for(connection, APP, KIND, letter["id"])
    page = kit.public_url(link["path"], source) if link else ""
    kit.queue_email(
        connection,
        APP,
        "newsletter_welcome",
        to=reader["email"],
        subject=f"Welcome to {letter['name']}",
        body=(
            f"Hello {reader['name']},\n\nYou are subscribed to {letter['name']}: every new issue comes to this "
            f"address.\n\nYour code is {code}. Keep it: with your email it stops the newsletter whenever you like"
            + (f", on its page:\n{page}" if page else ".")
        ),
        ref_type="newsletter_subscriber",
        ref_id=reader["id"],
    )


def _make_free(
    connection: sqlite3.Connection,
    letter: dict[str, Any],
    reader: dict[str, Any],
    source: Any = None,
) -> None:
    """A reader who was waiting to pay, now that the newsletter is free: in, with a code and a welcome email."""
    code = new_code()
    connection.execute(
        "UPDATE nw_subscribers SET status = 'active', code_hash = ? WHERE id = ?",
        (_hash(code), reader["id"]),
    )
    _welcome(connection, letter, reader, code, source)


def _request_view(letter: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "pending",
        "reference": row["reference"],
        "amount_cents": int(letter["price_cents"]),
        "currency": letter["currency"],
    }


def _by_code(
    connection: sqlite3.Connection, letter: dict[str, Any], code: str, email: str = ""
) -> dict[str, Any] | None:
    if not code or not code.strip():
        return None
    sql = "SELECT * FROM nw_subscribers WHERE letter_id = ? AND code_hash = ? AND status != 'cancelled'"
    params: list[Any] = [letter["id"], _hash(code)]
    if email:
        sql += " AND lower(email) = lower(?)"
        params.append(email)
    return kit.fetch_one(connection, sql, params)


def unlock(
    connection: sqlite3.Connection, letter: dict[str, Any], code: str
) -> list[dict[str, Any]]:
    """Every story of every issue, for a reader whose code is paid up (a free newsletter shows them anyway)."""
    if not is_paid(letter):
        return _public_issues(connection, letter, full=True)
    reader = _by_code(connection, letter, code)
    if reader is None or reader["status"] != "paid":
        raise PermissionError(
            "That access code is not right. Check the email you were sent."
        )
    if not reader["paid_until"] or reader["paid_until"] <= kit.now():
        raise PermissionError(
            "Your subscription has ended. Subscribe again below to keep reading."
        )
    return _public_issues(connection, letter, full=True)


def unsubscribe(
    connection: sqlite3.Connection, letter: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    address = kit.email(payload.get("email"), "your email address", required=True)
    code = str(payload.get("code") or "")
    reader = _by_code(connection, letter, code, address)
    if reader is None and code.strip():
        # Someone still waiting to pay has no code yet: their payment reference withdraws the request.
        reader = kit.fetch_one(
            connection,
            """SELECT * FROM nw_subscribers WHERE letter_id = ? AND status = 'pending' AND lower(email) = lower(?)
            AND upper(reference) = ?""",
            (letter["id"], address, code.strip().upper()),
        )
    if reader is None:
        raise PermissionError(
            "That email and code do not match a subscriber. If you have not paid yet, use your payment reference."
        )
    connection.execute(
        "UPDATE nw_subscribers SET status = 'cancelled', cancelled_at = ?, code_hash = '' WHERE id = ?",
        (kit.now(), reader["id"]),
    )
    kit.log(
        connection,
        APP,
        "unsubscribed",
        f"{reader['name']} stopped {letter['name']}",
        ref_type="newsletter_subscriber",
        ref_id=reader["id"],
    )
    connection.commit()
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# the writer's side: subscribers and payments
# ---------------------------------------------------------------------------


def _subscriber_view(row: dict[str, Any], now: str) -> dict[str, Any]:
    view = {
        k: row[k]
        for k in (
            "id",
            "name",
            "email",
            "status",
            "reference",
            "paid_until",
            "created_at",
            "cancelled_at",
        )
    }
    view["paid_up"] = bool(
        row["status"] == "paid" and row["paid_until"] and row["paid_until"] > now
    )
    return view


def subscribers(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    status: str = "",
    q: str = "",
) -> list[dict[str, Any]]:
    letter = _own(connection, me)
    clauses, params = ["letter_id = ?"], [letter["id"]]
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", STATUSES))
    if q.strip():
        like = f"%{q.strip()[:80]}%"
        clauses.append("(name LIKE ? OR email LIKE ? OR reference LIKE ?)")
        params += [like, like, like]
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM nw_subscribers WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 2000",  # noqa: S608 - fixed clauses
        params,
    )
    now = kit.now()
    return [_subscriber_view(row, now) for row in rows]


def _own_subscriber(
    connection: sqlite3.Connection, letter: dict[str, Any], subscriber_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM nw_subscribers WHERE id = ? AND letter_id = ?",
        (subscriber_id, letter["id"]),
    )
    if not row:
        raise LookupError("That subscriber was not found.")
    return row


def mark_paid(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    subscriber_id: str,
    *,
    source: Any = None,
) -> dict[str, Any]:
    """A month's payment arrived: it goes into the ledger, the reader is paid up 30 days longer and gets a new code.

    Each call is one more month (a reader who pays for three months is marked three times). The code is shown to
    the writer once, so they can pass it on themselves when no mail server is set up.
    """
    letter = _own(connection, me)
    row = _own_subscriber(connection, letter, subscriber_id)
    if row["status"] == "cancelled":
        raise ValueError(
            "This reader stopped the newsletter. Ask them to subscribe again."
        )
    if not is_paid(letter):
        raise ValueError(
            "Your newsletter is free. Set a monthly price to take payments."
        )
    now = kit.utcnow()
    current = row["paid_until"] and kit.parse_time(row["paid_until"])
    start = current if current and current > now else now
    until = kit.iso(start + PERIOD)
    payment_id = kit.new_id()
    paid_at = kit.now()
    connection.execute(
        "INSERT INTO nw_payments (id, subscriber_id, letter_id, amount_cents, currency, paid_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            payment_id,
            row["id"],
            letter["id"],
            int(letter["price_cents"]),
            letter["currency"],
            paid_at,
        ),
    )
    code = new_code()
    connection.execute(
        "UPDATE nw_subscribers SET status = 'paid', paid_until = ?, code_hash = ? WHERE id = ?",
        (until, _hash(code), row["id"]),
    )
    kit.record_revenue(
        connection,
        APP,
        source_type="newsletter_payment",
        source_id=payment_id,
        amount_cents=int(letter["price_cents"]),
        currency_code=letter["currency"],
        label=f"Newsletter: {letter['name']}",
        customer=row["name"],
        at=paid_at,
    )
    link = _page_link(connection, letter)
    where = (
        f"Open the newsletter's page and enter it to read every issue:\n{kit.public_url(link['path'], source)}"
        if link
        else "Keep it: the newsletter's page is closed for now, and every new issue comes by email."
    )
    message = kit.queue_email(
        connection,
        APP,
        "newsletter_access",
        to=row["email"],
        subject=f"Your subscription to {letter['name']}",
        body=(
            f"Hello {row['name']},\n\nThank you: your payment (reference {row['reference']}) has arrived. "
            f"You are subscribed to {letter['name']} until {until[:10]}, and every new issue comes to this "
            f"address.\n\nYour access code is {code}\n\n{where}\n\n"
            "With your email, the code also stops the newsletter whenever you like."
        ),
        ref_type="newsletter_subscriber",
        ref_id=row["id"],
    )
    kit.log(
        connection,
        APP,
        "subscription_paid",
        f"{row['name']} paid for {letter['name']}",
        ref_type="newsletter_subscriber",
        ref_id=row["id"],
        amount_cents=int(letter["price_cents"]),
    )
    connection.commit()
    fresh = _own_subscriber(connection, letter, row["id"])
    return {
        "subscriber": _subscriber_view(fresh, kit.now()),
        "code": code,
        "email": "queued" if message else "none",
        "email_status": (message or {}).get("status", ""),
        "amount": _money(connection, int(letter["price_cents"]), letter["currency"]),
    }


def undo_payment(
    connection: sqlite3.Connection, me: dict[str, Any], subscriber_id: str
) -> dict[str, Any]:
    """The last payment was marked by mistake (or refunded): out of the ledger, 30 days less."""
    letter = _own(connection, me)
    row = _own_subscriber(connection, letter, subscriber_id)
    last = kit.fetch_one(
        connection,
        "SELECT * FROM nw_payments WHERE subscriber_id = ? AND voided_at IS NULL ORDER BY paid_at DESC, rowid DESC",
        (row["id"],),
    )
    if not last:
        raise ValueError("This reader has no payment to undo.")
    connection.execute(
        "UPDATE nw_payments SET voided_at = ? WHERE id = ?", (kit.now(), last["id"])
    )
    kit.void_revenue(connection, APP, "newsletter_payment", last["id"])
    left = kit.count(
        connection,
        "SELECT COUNT(*) FROM nw_payments WHERE subscriber_id = ? AND voided_at IS NULL",
        (row["id"],),
    )
    if left:
        until = kit.iso(kit.parse_time(row["paid_until"]) - PERIOD)
        connection.execute(
            "UPDATE nw_subscribers SET paid_until = ? WHERE id = ?", (until, row["id"])
        )
    elif row["status"] == "cancelled":
        # A removed (or stopped) reader stays removed: a refund never puts them back on the waiting list.
        connection.execute(
            "UPDATE nw_subscribers SET paid_until = NULL WHERE id = ?", (row["id"],)
        )
    else:
        connection.execute(
            "UPDATE nw_subscribers SET status = 'pending', paid_until = NULL, code_hash = '' WHERE id = ?",
            (row["id"],),
        )
    connection.commit()
    return _subscriber_view(_own_subscriber(connection, letter, row["id"]), kit.now())


def remove_subscriber(
    connection: sqlite3.Connection, me: dict[str, Any], subscriber_id: str
) -> dict[str, Any]:
    """Stop sending to a reader (their payments stay in the records)."""
    letter = _own(connection, me)
    row = _own_subscriber(connection, letter, subscriber_id)
    connection.execute(
        "UPDATE nw_subscribers SET status = 'cancelled', cancelled_at = ?, code_hash = '' WHERE id = ?",
        (kit.now(), row["id"]),
    )
    connection.commit()
    return _subscriber_view(_own_subscriber(connection, letter, row["id"]), kit.now())


SUBSCRIBER_COLUMNS = (
    "name",
    "email",
    "status",
    "paid_up",
    "paid_until",
    "reference",
    "created_at",
    "cancelled_at",
)


def today_numbers(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> dict[str, Any] | None:
    """This writer's newsletter numbers for their Today card (readers, earned this month, waiting to pay)."""
    if not life.is_adult(me):
        return None
    letter = mine(connection, me)
    if letter is None:
        return None
    numbers = _numbers(connection, letter)
    return {
        "name": letter["name"],
        "paid": is_paid(letter),
        "readers": numbers["paid"]
        if is_paid(letter)
        else numbers["free"] + numbers["paid"],
        "month": _money(connection, numbers["month_cents"], letter["currency"]),
        "month_cents": numbers["month_cents"],
        "waiting": numbers["waiting"],
    }


def waiting_for(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    """Readers waiting for this writer to confirm a payment (for Today)."""
    if not life.is_adult(me):
        return 0
    letter = mine(connection, me)
    if letter is None:
        return 0
    return kit.count(
        connection,
        "SELECT COUNT(*) FROM nw_subscribers WHERE letter_id = ? AND status = 'pending'",
        (letter["id"],),
    )
