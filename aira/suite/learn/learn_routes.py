"""Learn's routes (``/api/learn/*``), its Today card, its pulse and its public deck page.

Every route is for one person (``life.current``): their decks, their progress on shared decks, their goal.
Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life, public
from aira.suite.learn import cards_io, progress, sales, service, store

APP = store.APP
BASE = "/api/learn"
ID = "([0-9a-f]{32})"


class _Session:
    """The connection and the person for one request."""

    def __init__(self, request: Request) -> None:
        self.request = request

    def __enter__(self) -> tuple[Any, dict[str, Any]]:
        self.connection = self.request.connect()
        store.ensure(self.connection)
        try:
            self.me = life.current(self.request, self.connection)
        except BaseException:
            self.connection.close()
            raise
        return self.connection, self.me

    def __exit__(self, *exc: Any) -> None:
        self.connection.close()


def _deck_view(
    connection: Any, me: dict[str, Any], deck: dict[str, Any], today: Any
) -> dict[str, Any]:
    owner = deck["owner_id"]
    return {
        **{
            k: deck[k]
            for k in (
                "id",
                "title",
                "description",
                "emoji",
                "shared",
                "source",
                "age_group",
                "created_at",
                "updated_at",
            )
        },
        "price_cents": int(deck.get("price_cents") or 0),
        "preview_count": int(deck.get("preview_count") or 5),
        "mine": owner == me["id"],
        # A copy of someone else's deck is studied, not sold.
        "copy_of_other": deck.get("source") == "copy"
        and deck.get("copied_from") != me["id"],
        "owner_name": me["name"]
        if owner == me["id"]
        else life.name_of(connection, owner),
        "progress": service.deck_progress(connection, me, deck["id"], today),
    }


# ---------------------------------------------------------------------------
# overview and settings
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        data = service.overview(connection, me)
        data["leaderboard"] = service.leaderboard(connection)[:5]
        data["profile"] = {
            "id": me["id"],
            "name": me["name"],
            "age_group": me["age_group"],
            "adult": life.is_adult(me),
        }
        return 200, data


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        saved = store.save_settings(connection, me, request.payload)
        return 200, {
            "settings": {
                k: saved[k]
                for k in (
                    "goal_kind",
                    "goal_value",
                    "new_per_day",
                    "reviews_per_day",
                    "reading_counts",
                )
            }
        }


# ---------------------------------------------------------------------------
# decks and cards
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/decks")
def decks_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        today = service.today_of(connection)
        decks = [
            _deck_view(connection, me, d, today)
            for d in store.decks_for(connection, me)
        ]
        return 200, {"decks": decks, "starters": service.starter_list(me)}


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    """The search box across the top: decks, cards and books holding every word of ``q`` (``limit`` of each)."""
    with _Session(request) as (connection, me):
        return 200, store.search(
            connection, me, request.q("q"), request.qint("limit", 20, 1, 50)
        )


@aira_route("POST", f"{BASE}/decks")
def decks_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = store.create_deck(connection, me["id"], request.payload)
        for item in (request.payload.get("cards") or [])[:200]:
            if isinstance(item, dict):
                store.add_card(connection, deck["id"], item)
        kit.log(
            connection,
            APP,
            "deck",
            f"{me['name']} made the deck {deck['title']}",
            ref_type="deck",
            ref_id=deck["id"],
        )
        service.award_badges(connection, me, service.today_of(connection))
        connection.commit()
        return 201, {
            "deck": _deck_view(connection, me, deck, service.today_of(connection))
        }


@aira_route("POST", f"{BASE}/starters")
def starters_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = service.add_starter(
            connection, me, str(request.payload.get("key") or "")
        )
        return 201, {
            "deck": _deck_view(connection, me, deck, service.today_of(connection))
        }


@aira_route("POST", f"{BASE}/decks/generate")
def decks_generate(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = service.generate_deck(connection, me, request.payload)
        view = _deck_view(
            connection,
            me,
            store.deck(connection, deck["id"]),
            service.today_of(connection),
        )
        return 201, {
            "deck": view,
            "note": deck["note"],
            "cards_added": deck["cards_added"],
        }


@aira_route("GET", f"{BASE}/decks/{ID}")
def decks_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = store.visible_deck(connection, me, request.match.group(1))
        view = _deck_view(connection, me, deck, service.today_of(connection))
        view["cards"] = store.cards(connection, deck["id"])
        link = (
            public.link_for(connection, APP, "deck", deck["id"])
            if deck["owner_id"] == me["id"]
            else None
        )
        view["link"] = link
        view["can_share_publicly"] = deck["owner_id"] == me["id"] and life.is_adult(me)
        view["can_sell"] = view["can_share_publicly"] and (
            not view["copy_of_other"] or sales.for_sale(deck)
        )
        # Someone else's deck on sale is theirs to sell: studied here, not copied.
        view["can_copy"] = deck["owner_id"] != me["id"] and not sales.for_sale(deck)
        if view["can_share_publicly"]:
            view["payment"] = sales.payment_view(connection, me)
        if view["can_sell"]:
            view["sold"] = kit.count(
                connection,
                "SELECT COUNT(*) FROM ln_sales WHERE deck_id = ? AND status = 'paid'",
                (deck["id"],),
            )
            view["currency"] = kit.business(connection)["currency"]
        return 200, {"deck": view}


@aira_route("PATCH", f"{BASE}/decks/{ID}")
def decks_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = store.own_deck(connection, me, request.match.group(1))
        values = store.clean_deck(request.payload, deck)
        kit.update_row(
            connection, "ln_decks", deck["id"], {**values, "updated_at": kit.now()}
        )
        connection.commit()
        return 200, {
            "deck": _deck_view(
                connection,
                me,
                store.deck(connection, deck["id"]),
                service.today_of(connection),
            )
        }


@aira_route("DELETE", f"{BASE}/decks/{ID}")
def decks_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        service.delete_deck(connection, me, request.match.group(1))
        public.revoke(connection, APP, "deck", request.match.group(1))
        # Requests to buy a deleted deck can no longer be met; paid sales stay in the seller's records.
        connection.execute(
            "UPDATE ln_sales SET status = 'cancelled', cancelled_at = ? WHERE deck_id = ? AND status = 'pending'",
            (kit.now(), request.match.group(1)),
        )
        connection.commit()
        return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/decks/{ID}/copy")
def decks_copy(request: Request) -> tuple[int, dict[str, Any]]:
    """Your own copy of a deck you may study (for example one shared by the household), to change freely."""
    with _Session(request) as (connection, me):
        deck = service.copy_deck(connection, me, request.match.group(1))
        return 201, {
            "deck": _deck_view(connection, me, deck, service.today_of(connection))
        }


@aira_route("POST", f"{BASE}/decks/{ID}/import")
def decks_import(request: Request) -> tuple[int, dict[str, Any]]:
    """Add many cards at once from pasted text or a CSV file's contents (``{"text"}``)."""
    with _Session(request) as (connection, me):
        result = cards_io.import_cards(
            connection,
            me,
            request.match.group(1),
            str(request.payload.get("text") or ""),
        )
    request.audit("learn.cards_import", "deck", request.match.group(1))
    return 200, result


@aira_route("GET", f"{BASE}/decks/{ID}/export")
def decks_export(request: Request) -> Raw:
    with _Session(request) as (connection, me):
        filename, body = cards_io.export_csv(connection, me, request.match.group(1))
    return Raw(200, body, "text/csv; charset=utf-8", filename=filename)


@aira_route("POST", f"{BASE}/decks/{ID}/price")
def decks_price(request: Request) -> tuple[int, dict[str, Any]]:
    """Sell a deck (``{"price_cents", "preview_count"}``; a price of 0 makes its page free again)."""
    with _Session(request) as (connection, me):
        deck = sales.set_price(connection, me, request.match.group(1), request.payload)
        link = public.link_for(connection, APP, "deck", deck["id"])
        if sales.for_sale(deck) and not link:
            link = public.create_link(
                connection, APP, "deck", deck["id"], title=deck["title"]
            )
            connection.commit()
        view = _deck_view(connection, me, deck, service.today_of(connection))
        view["link"] = link
    request.audit("learn.deck_price", "deck", deck["id"])
    return 200, {"deck": view}


@aira_route("POST", f"{BASE}/decks/{ID}/cards")
def cards_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = store.own_deck(connection, me, request.match.group(1))
        card = store.add_card(connection, deck["id"], request.payload)
        connection.commit()
        return 201, {"card": card}


@aira_route("PATCH", f"{BASE}/cards/{ID}")
def cards_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        card = store.card(connection, request.match.group(1))
        store.own_deck(connection, me, card["deck_id"])
        values = store.clean_card(request.payload, card)
        kit.update_row(
            connection, "ln_cards", card["id"], {**values, "updated_at": kit.now()}
        )
        connection.commit()
        return 200, {"card": store.card(connection, card["id"])}


@aira_route("DELETE", f"{BASE}/cards/{ID}")
def cards_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        card = store.card(connection, request.match.group(1))
        sales.can_drop_card(connection, store.own_deck(connection, me, card["deck_id"]))
        connection.execute("DELETE FROM ln_cards WHERE id = ?", (card["id"],))
        connection.execute("DELETE FROM ln_progress WHERE card_id = ?", (card["id"],))
        connection.commit()
        return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/decks/{ID}/link")
def decks_link(request: Request) -> tuple[int, dict[str, Any]]:
    """A read-only page of the deck a relative can study in their browser (adults, the deck's owner)."""
    with _Session(request) as (connection, me):
        deck = store.own_deck(connection, me, request.match.group(1))
        if not life.is_adult(me):
            raise PermissionError("Ask an adult to share decks outside the household.")
        link = public.create_link(
            connection, APP, "deck", deck["id"], title=deck["title"]
        )
        kit.log(
            connection,
            APP,
            "share",
            f"{me['name']} shared the deck {deck['title']}",
            ref_type="deck",
            ref_id=deck["id"],
        )
        connection.commit()
    request.audit("learn.deck_link", "deck", deck["id"])
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/decks/{ID}/link")
def decks_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        deck = store.own_deck(connection, me, request.match.group(1))
        sales.can_turn_off_link(deck)
        public.revoke(connection, APP, "deck", deck["id"])
        return 200, {"link": None}


# ---------------------------------------------------------------------------
# studying, quizzes and maths
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/study")
def study(request: Request) -> tuple[int, dict[str, Any]]:
    """The next cards to study; ``mode=weak`` gives the cards this person keeps missing, due or not."""
    with _Session(request) as (connection, me):
        if request.q("mode") == "weak":
            today = service.today_of(connection)
            return 200, {
                **progress.weak_queue(
                    connection,
                    me,
                    deck_id=request.q("deck_id"),
                    limit=request.qint("limit", 20, 1, 50),
                ),
                "counts": service.counts(connection, me, today),
                "goal": service.goal_state(connection, me, today),
                "mode": "weak",
            }
        return 200, service.study_queue(
            connection,
            me,
            deck_id=request.q("deck_id"),
            limit=request.qint("limit", 20, 1, 50),
        )


@aira_route("POST", f"{BASE}/study/answer")
def study_answer(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _Session(request) as (connection, me):
        result = service.answer(
            connection,
            me,
            str(payload.get("card_id") or ""),
            str(payload.get("grade") or ""),
            seconds=kit.integer(
                payload.get("seconds"), "seconds", default=0, minimum=0, maximum=3600
            ),
        )
        return 200, result


@aira_route("GET", f"{BASE}/quizzes")
def quizzes_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 200, {
            "history": service.history(
                connection, me, request.qint("limit", 20, 1, 100)
            ),
            "levels": service.MATHS_LEVELS,
        }


@aira_route("POST", f"{BASE}/quizzes")
def quizzes_create(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _Session(request) as (connection, me):
        count = kit.integer(
            payload.get("count"),
            "the number of questions",
            default=10,
            minimum=1,
            maximum=30,
        )
        return 201, {
            "quiz": service.create_quiz(
                connection, me, str(payload.get("deck_id") or ""), count
            )
        }


@aira_route("POST", f"{BASE}/quizzes/{ID}/submit")
def quizzes_submit(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 200, service.submit_quiz(
            connection, me, request.match.group(1), request.payload.get("answers")
        )


@aira_route("POST", f"{BASE}/maths")
def maths_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 201, {"quiz": service.create_maths(connection, me, request.payload)}


@aira_route("POST", f"{BASE}/maths/{ID}/submit")
def maths_submit(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 200, service.submit_maths(
            connection, me, request.match.group(1), request.payload.get("answers")
        )


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/books")
def books_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        today = service.today_of(connection)
        minutes_today = store.day_row(connection, me["id"], today.isoformat())[
            "reading_minutes"
        ]
        week = kit.count(
            connection,
            "SELECT COALESCE(SUM(minutes), 0) FROM ln_reading WHERE profile_id = ? AND day >= ?",
            (me["id"], (today - timedelta(days=today.weekday())).isoformat()),
        )
        return 200, {
            "books": service.books(connection, me),
            "minutes_today": minutes_today,
            "minutes_week": week,
        }


@aira_route("POST", f"{BASE}/books")
def books_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 201, {"book": service.add_book(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/books/{ID}")
def books_update(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"title"?, "author"?, "finished"?}``."""
    with _Session(request) as (connection, me):
        return 200, {
            "book": service.update_book(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/books/{ID}")
def books_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        service.delete_book(connection, me, request.match.group(1))
        return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/reading")
def reading_log(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 200, service.log_reading(connection, me, request.payload)


@aira_route("GET", f"{BASE}/stats")
def stats(request: Request) -> Any:
    """The person's progress (calendar, retention, forecast, hardest cards); ``format=csv`` is the daily log."""
    with _Session(request) as (connection, me):
        if request.q("format") == "csv":
            return Raw(
                200,
                progress.daily_log(connection, me),
                "text/csv; charset=utf-8",
                filename="learning-log.csv",
            )
        return 200, progress.stats(connection, me)


# ---------------------------------------------------------------------------
# earning: selling decks
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/sales")
def sales_list(request: Request) -> Any:
    """The seller's requests and sales (``status``, ``q``), headline numbers; ``format=csv`` downloads them."""
    with _Session(request) as (connection, me):
        rows = sales.sales(connection, me, status=request.q("status"), q=request.q("q"))
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(sales.csv_rows(rows), sales.SALE_COLUMNS),
                "text/csv; charset=utf-8",
                filename="deck-sales.csv",
            )
        return 200, {"summary": sales.summary(connection, me), "sales": rows}


@aira_route("POST", f"{BASE}/seller")
def seller_save(request: Request) -> tuple[int, dict[str, Any]]:
    """How buyers pay you: ``{"payment_link", "payment_instructions"}`` (your own, not the organization's)."""
    with _Session(request) as (connection, me):
        payment = sales.save_payment(connection, me, request.payload)
    request.audit("learn.seller_payment", "profile", me["id"])
    return 200, {"payment": payment}


@aira_route("POST", f"{BASE}/sales/{ID}/paid")
def sales_paid(request: Request) -> tuple[int, dict[str, Any]]:
    """The payment arrived (or resend a lost code): revenue recorded, a new access code emailed to the buyer."""
    with _Session(request) as (connection, me):
        result = sales.mark_paid(connection, me, request.match.group(1), source=request)
    request.audit("learn.sale_paid", "sale", request.match.group(1))
    return 200, result


@aira_route("POST", f"{BASE}/sales/{ID}/unpaid")
def sales_unpaid(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        sale = sales.mark_unpaid(connection, me, request.match.group(1))
    request.audit("learn.sale_unpaid", "sale", request.match.group(1))
    return 200, {"sale": sale}


@aira_route("POST", f"{BASE}/sales/{ID}/cancel")
def sales_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        sale = sales.cancel(connection, me, request.match.group(1))
    request.audit("learn.sale_cancel", "sale", request.match.group(1))
    return 200, {"sale": sale}


@aira_route("GET", f"{BASE}/leaderboard")
def leaderboard(request: Request) -> tuple[int, dict[str, Any]]:
    with _Session(request) as (connection, me):
        return 200, {"people": service.leaderboard(connection), "me": me["id"]}


# ---------------------------------------------------------------------------
# Today, pulse and the hub
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    day = service.today_of(connection)
    numbers = service.counts(connection, me, day)
    goal = service.goal_state(connection, me, day)
    days = service.streak(connection, me["id"], day)
    waiting = numbers["due"] + numbers["new"]
    # Someone with no deck yet is invited to start, not told they are done.
    has_cards = bool(
        kit.count(
            connection,
            "SELECT COUNT(*) FROM ln_decks WHERE archived_at IS NULL AND (owner_id = ? OR shared = 1)",
            (me["id"],),
        )
    )
    if waiting:
        first = {
            "text": f"{waiting} card{'s' if waiting != 1 else ''} to practise",
            "sub": f"{numbers['due']} to review, {numbers['new']} new",
            "done": False,
        }
    elif has_cards:
        first = {
            "text": "All cards done for today",
            "sub": "Add a deck for more",
            "done": True,
        }
    else:
        first = {
            "text": "Start learning something new",
            "sub": "Pick a ready-made deck: times tables, Spanish, staying safe online...",
            "done": False,
        }
    lines = [
        first,
        {
            "text": f"Today's goal: {goal['label']}",
            "sub": "Well done!" if goal["done"] else "",
            "done": goal["done"],
        },
    ]
    if days:
        # A streak is never "done": crossing it out on a good day would read as a lost streak.
        lines.append(
            {
                "text": f"🔥 {days}-day streak",
                "sub": "Kept for today"
                if goal["done"]
                else "Practise today to keep it going",
            }
        )
    tone = (
        "good"
        if goal["done"]
        else ("warn" if days and service.streak_at_risk(connection, me) else "")
    )
    cards = [
        life.card(
            APP, "Practise today", lines=lines, tone=tone, link="#/learn?tab=study"
        )
    ]
    waiting_sales = sales.waiting_for(connection, me)
    if waiting_sales:
        # Its own card, so it opens Earn (where payments are confirmed), not Study.
        cards.append(
            life.card(
                APP,
                "Deck sales",
                lines=[
                    {
                        "text": f"{waiting_sales} {'person wants' if waiting_sales == 1 else 'people want'} to buy your decks",
                        "sub": "Check the payment arrived, then press Payment received",
                    }
                ],
                tone="warn",
                link="#/learn?tab=earn",
            )
        )
    return cards


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    day = service.today_of(connection)
    numbers = service.counts(connection, me, day)
    alerts = []
    if service.streak_at_risk(connection, me):
        days = service.streak(connection, me["id"], day)
        alerts.append(
            {
                "id": f"learn:streak:{me['id']}:{day.isoformat()}",
                "title": f"Keep your {days}-day streak!",
                "body": "A few minutes of practice today keeps it going.",
                "tone": "warn",
                "speak": True,
                "actions": [],
            }
        )
    return {"badge": min(99, numbers["due"] + numbers["new"]), "alerts": alerts}


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    store.ensure(connection)
    return [
        kit.step(
            "deck",
            "Add a deck",
            kit.count(connection, "SELECT COUNT(*) FROM ln_decks") > 0,
            "Pick a starter deck or make your own",
        ),
        kit.step(
            "study",
            "Study your first cards",
            kit.count(connection, "SELECT COUNT(*) FROM ln_reviews") > 0,
            "A few minutes a day",
        ),
        kit.step(
            "goal",
            "Meet a daily goal",
            kit.count(connection, "SELECT COUNT(*) FROM ln_days WHERE goal_met = 1")
            > 0,
            "Set it under Today",
        ),
        kit.step(
            "quiz",
            "Take a quiz",
            kit.count(
                connection, "SELECT COUNT(*) FROM ln_quizzes WHERE status = 'done'"
            )
            > 0,
            "From any deck, or maths practice",
        ),
    ]


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    store.ensure(connection)
    today = service.today_of(connection)
    week = kit.count(
        connection,
        "SELECT COALESCE(SUM(cards), 0) FROM ln_days WHERE day >= ?",
        ((today - timedelta(days=6)).isoformat(),),
    )
    money: list[dict[str, Any]] = []
    if not sales.hosted():
        # Hosted, members' deck income is their own, so it is not the organization's to sum up.
        month = kit.count(
            connection,
            "SELECT COALESCE(SUM(amount_cents), 0) FROM ln_sales WHERE status = 'paid' AND substr(paid_at, 1, 7) = ?",
            (kit.now()[:7],),
        )
        waiting = kit.count(
            connection, "SELECT COUNT(*) FROM ln_sales WHERE status = 'pending'"
        )
        money.append(
            {
                "label": "Earned from decks this month",
                "value": kit.fmt_money(month, kit.business(connection)["currency"]),
            }
        )
        if waiting:
            money.append(
                {"label": "Deck sales to confirm", "value": waiting, "tone": "warn"}
            )
    return {
        "cards": [
            {"label": "Cards studied this week", "value": week},
            *money,
            {
                "label": "Decks",
                "value": kit.count(
                    connection,
                    "SELECT COUNT(*) FROM ln_decks WHERE archived_at IS NULL",
                ),
            },
        ]
    }


# ---------------------------------------------------------------------------
# the public deck page (study in the browser; nothing is saved)
# ---------------------------------------------------------------------------


def _public_deck(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    try:
        return store.deck(connection, link["ref_id"])
    except LookupError:
        raise LookupError("This link is no longer available.") from None


@public.page(APP, "deck")
def _deck_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    """A free deck shows every card; a deck on sale shows a preview, the price and the buy form."""
    # The seller's own way to pay (page.payment), never the business's payment link.
    return {
        **sales.page_data(connection, _public_deck(connection, link)),
        "business_payment": False,
    }


@public.action(APP, "deck", "buy")
def _deck_buy(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """``{"name", "email"}``: ask to buy the deck; answers the amount and the reference to pay with."""
    deck = _public_deck(connection, link)
    return 201, {"request": sales.request_purchase(connection, deck, payload)}


@public.query(APP, "deck", "cards")
def _deck_cards(
    connection: Any, link: dict[str, Any], params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Every card, for a buyer's access code (sent in the Aira-Secret header)."""
    deck = _public_deck(connection, link)
    return 200, {
        "cards": sales.unlock(connection, deck, str(params.get("secret") or ""))
    }
