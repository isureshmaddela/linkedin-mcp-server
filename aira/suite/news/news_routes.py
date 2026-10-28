"""News routes (``/api/news/*``), the newsletter's public page, its Today cards, pulse, hub numbers and background.

Every route works for the person asking (:func:`aira.suite.life.current`); :mod:`aira.suite.news.service` holds the
rules for reading the news and :mod:`aira.suite.news.letter` those of the newsletter. Loaded by
:func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, life, public
from aira.suite.news import letter, service, store

APP = store.APP
BASE = "/api/news"
ID = "([0-9a-f]{32})"
# POST routes that only read (hosted rules: a reader may call them).
HOSTED_RULES = {
    "refresh": "read",
    "article_read": "read",
    "newsletter_suggest": "read",
}


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    try:
        store.ensure(connection)
        me = life.current(request, connection)
    except BaseException:
        connection.close()  # the picker, or a failure: do not leave the connection behind
        raise
    return connection, me


@aira_route("GET", f"{BASE}/feed")
def feed(request: Request) -> tuple[int, dict[str, Any]]:
    """A tab's articles: ``topic`` = top, a section code, foryou, source:<id> or all; ``since`` for live updates."""
    since = kit.moment(request.q("since"), "since") if request.q("since") else ""
    connection, me = _open(request)
    with connection:
        data = service.articles(
            connection,
            me,
            topic=request.q("topic", "top") or "top",
            since=since,
            limit=request.qint("limit", 40, 1, 100),
        )
        if not since:
            service.mark_opened(
                connection, me
            )  # the person looked: the badge starts again from now
        return 200, data


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.settings_view(connection, me)


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.save_settings(connection, me, request.payload)


@aira_route("POST", f"{BASE}/keywords")
def keywords_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"keyword": service.add_keyword(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/keywords/{ID}")
def keywords_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "keyword": service.update_keyword(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/keywords/{ID}")
def keywords_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_keyword(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/sources")
def sources_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        source = service.add_source(connection, me, request.payload)
    request.audit("news.source_add", "source", source["id"])
    return 201, {"source": source}


@aira_route("DELETE", f"{BASE}/sources/{ID}")
def sources_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_source(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/articles/{ID}/save")
def article_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"article": service.save(connection, me, request.match.group(1))}


@aira_route("DELETE", f"{BASE}/articles/{ID}/save")
def article_unsave(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"article": service.unsave(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/articles/{ID}/read")
def article_read(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "article": service.mark_read(connection, me, request.match.group(1))
        }


@aira_route("GET", f"{BASE}/saved")
def saved_list(request: Request) -> Any:
    """The person's saved articles; ``format=csv`` downloads them."""
    connection, me = _open(request)
    with connection:
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(
                    service.saved_rows(connection, me), service.SAVED_COLUMNS
                ),
                "text/csv; charset=utf-8",
                filename="saved-news.csv",
            )
        return 200, {"articles": service.saved(connection, me)}


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    """Any news about ``q`` in the person's language and country, without following it."""
    connection, me = _open(request)
    with connection:
        return 200, service.search(connection, me, request.q("q"))


@aira_route("POST", f"{BASE}/muted")
def muted_add(request: Request) -> tuple[int, dict[str, Any]]:
    """Hide a source (``source``: its name as the cards show it)."""
    connection, me = _open(request)
    with connection:
        return 201, {"muted": service.mute(connection, me, request.payload)}


@aira_route("DELETE", f"{BASE}/muted/{ID}")
def muted_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.unmute(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/refresh")
def refresh(request: Request) -> tuple[int, dict[str, Any]]:
    """Fetch everything the person follows now (each feed at most once per minute)."""
    connection, me = _open(request)
    with connection:
        results = []
        now = kit.utcnow()
        for row in service.feeds_for(connection, me, "all"):
            recent = (
                row.get("last_fetched")
                and (now - kit.parse_time(row["last_fetched"])).total_seconds() < 60
            )
            results.append(service.refresh(connection, row, force=not recent))
        failed = sum(1 for r in results if not r["ok"])
        return 200, {
            "feeds": len(results),
            "added": sum(r.get("added", 0) for r in results),
            "error": service.REFRESH_ERROR if failed else "",
        }


@aira_route("GET", f"{BASE}/briefing")
def briefing(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.briefing(connection, me)


# ---------------------------------------------------------------------------
# the newsletter (earning): the writer's side
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/newsletter")
def newsletter_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, letter.overview(connection, me)


@aira_route("POST", f"{BASE}/newsletter")
def newsletter_save(request: Request) -> tuple[int, dict[str, Any]]:
    """Start the newsletter, or change ``name``, ``about`` and ``price_cents`` (a month; 0 is free)."""
    connection, me = _open(request)
    with connection:
        saved = letter.save(connection, me, request.payload, source=request)
        data = letter.overview(connection, me)
    request.audit("news.newsletter_save", "newsletter", saved["id"])
    # freed: readers let in (the price went to 0); told: free readers emailed about the new price.
    return 200, {**data, "freed": saved["freed"], "told": saved["told"]}


@aira_route("POST", f"{BASE}/newsletter/link")
def newsletter_link(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"link": letter.share(connection, me)}


@aira_route("DELETE", f"{BASE}/newsletter/link")
def newsletter_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        letter.unshare(connection, me)
        return 200, {"link": None}


@aira_route("POST", f"{BASE}/newsletter/picks")
def newsletter_pick_add(request: Request) -> tuple[int, dict[str, Any]]:
    """Put an article in the next issue (``article_id``, optional ``note``)."""
    connection, me = _open(request)
    with connection:
        return 201, {"pick": letter.add_pick(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/newsletter/picks/{ID}")
def newsletter_pick_update(request: Request) -> tuple[int, dict[str, Any]]:
    """Change a story's ``note`` or ``move`` it up or down."""
    connection, me = _open(request)
    with connection:
        return 200, {
            "pick": letter.update_pick(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/newsletter/picks/{ID}")
def newsletter_pick_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        letter.remove_pick(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/newsletter/suggest")
def newsletter_suggest(request: Request) -> tuple[int, dict[str, Any]]:
    """A title and an opening for the next issue (changes nothing)."""
    connection, me = _open(request)
    with connection:
        return 200, letter.suggest_intro(connection, me)


@aira_route("POST", f"{BASE}/newsletter/issues")
def newsletter_publish(request: Request) -> tuple[int, dict[str, Any]]:
    """Publish the next issue (``title``, ``intro``): on the page, and by email to every reader who gets it."""
    connection, me = _open(request)
    with connection:
        issue = letter.publish(connection, me, request.payload, source=request)
    request.audit("news.issue_publish", "newsletter_issue", issue["id"])
    return 201, {"issue": issue}


@aira_route("DELETE", f"{BASE}/newsletter/issues/{ID}")
def newsletter_issue_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        letter.delete_issue(connection, me, request.match.group(1))
    request.audit("news.issue_delete", "newsletter_issue", request.match.group(1))
    return 200, {"removed": True}


@aira_route("GET", f"{BASE}/newsletter/subscribers")
def newsletter_subscribers(request: Request) -> Any:
    """The readers (``status``, ``q``); ``format=csv`` downloads them."""
    connection, me = _open(request)
    with connection:
        rows = letter.subscribers(
            connection, me, status=request.q("status"), q=request.q("q")
        )
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(rows, letter.SUBSCRIBER_COLUMNS),
                "text/csv; charset=utf-8",
                filename="newsletter-subscribers.csv",
            )
        return 200, {"subscribers": rows}


@aira_route("POST", f"{BASE}/newsletter/subscribers/{ID}/paid")
def newsletter_paid(request: Request) -> tuple[int, dict[str, Any]]:
    """A month's payment arrived: revenue recorded, 30 more days, a new access code emailed."""
    connection, me = _open(request)
    with connection:
        result = letter.mark_paid(
            connection, me, request.match.group(1), source=request
        )
    request.audit(
        "news.subscription_paid", "newsletter_subscriber", request.match.group(1)
    )
    return 200, result


@aira_route("POST", f"{BASE}/newsletter/subscribers/{ID}/undo")
def newsletter_undo(request: Request) -> tuple[int, dict[str, Any]]:
    """Undo the last payment (marked by mistake, or refunded)."""
    connection, me = _open(request)
    with connection:
        row = letter.undo_payment(connection, me, request.match.group(1))
    request.audit(
        "news.subscription_undo", "newsletter_subscriber", request.match.group(1)
    )
    return 200, {"subscriber": row}


@aira_route("DELETE", f"{BASE}/newsletter/subscribers/{ID}")
def newsletter_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        row = letter.remove_subscriber(connection, me, request.match.group(1))
    request.audit(
        "news.subscriber_remove", "newsletter_subscriber", request.match.group(1)
    )
    return 200, {"subscriber": row}


# ---------------------------------------------------------------------------
# the newsletter's public page
# ---------------------------------------------------------------------------


def _public_letter(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    row = kit.fetch_one(
        connection, "SELECT * FROM nw_letters WHERE id = ?", (link["ref_id"],)
    )
    if not row:
        raise LookupError("This link is no longer available.")
    return row


@public.page(APP, letter.KIND)
def _letter_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    """Every issue of a free newsletter; the first story of each (and the price) for a paid one."""
    return letter.page_data(connection, _public_letter(connection, link))


@public.action(APP, letter.KIND, "subscribe")
def _letter_subscribe(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """``{"name", "email"}``: subscribed (free), or the amount and reference to pay with (paid)."""
    row = _public_letter(connection, link)
    return 201, {
        "subscription": letter.subscribe(connection, row, payload, visitor=visitor)
    }


@public.action(APP, letter.KIND, "unsubscribe")
def _letter_unsubscribe(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """``{"email", "code"}``: stop the newsletter."""
    row = _public_letter(connection, link)
    return 200, letter.unsubscribe(connection, row, payload)


@public.query(APP, letter.KIND, "issues")
def _letter_issues(
    connection: Any, link: dict[str, Any], params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Every story, for a paid-up reader's access code (sent in the Aira-Secret header)."""
    row = _public_letter(connection, link)
    return 200, {
        "issues": letter.unlock(connection, row, str(params.get("secret") or ""))
    }


# ---------------------------------------------------------------------------
# Today, pulse, hub and background
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    data = service.today_lines(connection, me)
    lines = [
        {
            "text": a["title"],
            "sub": " · ".join(part for part in (a["source"], a["ago"]) if part),
        }
        for a in data["articles"]
    ]
    if lines:
        lines[-1] = {
            **lines[-1],
            "sub": f"{lines[-1]['sub']}. Open News to listen to them.",
        }
    cards = [
        life.card(
            APP,
            "Today's headlines",
            lines=lines,
            empty=data["error"] or "No headlines yet.",
        )
    ]
    numbers = letter.today_numbers(connection, me)
    if numbers:
        # The writer's own numbers (the Business hub lists only the business apps, so they show here too).
        lines = []
        waiting = numbers["waiting"]
        if waiting:
            who = "1 reader wants" if waiting == 1 else f"{waiting} readers want"
            lines.append(
                {
                    "text": f"{who} to subscribe",
                    "sub": "When a payment arrives, mark it received in News, My newsletter.",
                }
            )
        if numbers["readers"] or numbers["month_cents"]:
            kind = "paid reader" if numbers["paid"] else "reader"
            count = numbers["readers"]
            lines.append(
                {
                    "text": f"{count} {kind}{'' if count == 1 else 's'} · earned {numbers['month']} this month",
                    "sub": numbers["name"],
                }
            )
        if lines:
            cards.append(
                life.card(
                    APP,
                    "Your newsletter",
                    lines=lines,
                    link="#/news?tab=newsletter",
                    tone="warn" if waiting else "",
                )
            )
    return cards


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    return {
        "badge": service.badge(connection, me),
        "alerts": service.keyword_alerts(connection, me),
    }


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    """The newsletters' numbers for the Business hub (once someone has started one)."""
    store.ensure(connection)
    if not kit.count(connection, "SELECT COUNT(*) FROM nw_letters"):
        return {"cards": []}
    now = kit.now()
    readers = kit.count(
        connection,
        "SELECT COUNT(*) FROM nw_subscribers WHERE status = 'active' OR (status = 'paid' AND paid_until > ?)",
        (now,),
    )
    month = kit.count(
        connection,
        "SELECT COALESCE(SUM(amount_cents), 0) FROM nw_payments WHERE voided_at IS NULL AND substr(paid_at, 1, 7) = ?",
        (now[:7],),
    )
    waiting = kit.count(
        connection, "SELECT COUNT(*) FROM nw_subscribers WHERE status = 'pending'"
    )
    cards: list[dict[str, Any]] = [
        {"label": "Newsletter readers", "value": readers},
        {
            "label": "Earned from newsletters this month",
            "value": kit.fmt_money(month, kit.business(connection)["currency"]),
        },
    ]
    if waiting:
        cards.append(
            {
                "label": "Newsletter payments to confirm",
                "value": waiting,
                "tone": "warn",
            }
        )
    return {"cards": cards}


@background.task(APP)
def _background(connection: Any) -> dict[str, Any]:
    """Keep every followed feed fresh, so the news is ready when someone opens it."""
    store.ensure(connection)
    return service.background_refresh(connection)
