"""Routes for Service (``/api/service/*``), its public pages, its background task and its Business hub numbers.

Public pages: ``help_center`` (one per business: articles, search, votes and a contact form that opens a case),
``chat`` (one per business: a live chat a visitor starts from a link) and ``case`` (one per case: the customer's
view of the public conversation, where they reply and rate us). Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira import suite
from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, life, public
from aira.suite.service import desk, store
from aira.suite.service.store import APP

BASE = "/api/service"
ID = "([0-9a-f]{32})"
HELP_REF = "help"
CHAT_REF = "chat"
# POST routes that only read (a suggestion, filling a saved reply into the reply box).
HOSTED_RULES = {"cases_suggest": "read"}


def _grown_up(request: Request, connection: Any) -> None:
    """Service holds customers' names, emails and phone numbers and the business's money: in the single-user build
    a child or teen household profile is refused (403), as the hosted build refuses a member without a business
    role. The profile header is checked like every life app does (it must exist and be unlocked); with no header
    the request is the account itself. The hosted build's roles are checked by the gateway."""
    member = suite.actor()
    if member and member.get("member_id"):
        return
    header = str(request.header("X-Aira-Profile") or "").strip()
    if not header:
        return
    if not life.is_adult(life.current(request, connection)):
        raise PermissionError(
            "Service holds your customers' details and payments. Ask a grown-up in your household to open it."
        )


def _connect(request: Request) -> Any:
    connection = request.connect()
    try:
        _grown_up(request, connection)
    except Exception:
        connection.close()
        raise
    store.ensure(connection)
    return connection


def _actor(request: Request) -> str:
    user = request.user or {}
    return str(user.get("email") or user.get("name") or "Support")


# ---------------------------------------------------------------------------
# overview and settings
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "stats": desk.stats(connection),
            "views": desk.view_counts(connection, me=_actor(request)),
            "checklist": desk.checklist(connection),
            "help_link": public.link_for(connection, APP, "help_center", HELP_REF),
            "chat_link": public.link_for(connection, APP, "chat", CHAT_REF),
            "chat_online": desk.chat_online(connection),
            "me": _actor(request),
        }


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"settings": store.settings(connection)}


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        saved = store.save_settings(connection, request.payload)
    request.audit("service.settings", "settings", APP)
    return 200, {"settings": saved}


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/cases")
def cases_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _connect(request) as connection:
        cases = desk.list_cases(
            connection,
            view=request.q("view") or "open",
            me=_actor(request),
            q=request.q("q"),
            status=request.q("status"),
            priority=request.q("priority"),
            channel=request.q("channel"),
            tag=request.q("tag"),
        )
        if request.q("format") == "csv":
            rows = [{**case, "tags": ", ".join(case["tags"])} for case in cases]
            return Raw(
                200,
                kit.csv_bytes(rows, desk.CSV_COLUMNS),
                "text/csv; charset=utf-8",
                filename="cases.csv",
            )
        return 200, {
            "cases": cases,
            "views": desk.view_counts(connection, me=_actor(request)),
            "me": _actor(request),
        }


@aira_route("POST", f"{BASE}/cases")
def cases_create(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _connect(request) as connection:
        channel = kit.choice(
            payload.get("channel"),
            "the channel",
            ("manual", "phone", "email"),
            default="manual",
        )
        case = desk.create_case(
            connection, payload, channel=channel, origin=request, acknowledge=False
        )
    request.audit("service.case_create", "case", case["id"])
    return 201, {"case": case}


@aira_route("GET", f"{BASE}/cases/{ID}")
def cases_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, desk.case_detail(connection, request.match.group(1))


@aira_route("PATCH", f"{BASE}/cases/{ID}")
def cases_update(request: Request) -> tuple[int, dict[str, Any]]:
    case_id = request.match.group(1)
    with _connect(request) as connection:
        desk.update_case(
            connection, case_id, request.payload, actor=_actor(request), origin=request
        )
        detail = desk.case_detail(connection, case_id)
    request.audit("service.case_update", "case", case_id)
    return 200, detail


@aira_route("DELETE", f"{BASE}/cases/{ID}")
def cases_delete(request: Request) -> tuple[int, dict[str, Any]]:
    case_id = request.match.group(1)
    with _connect(request) as connection:
        removed = desk.delete_case(connection, case_id, actor=_actor(request))
    request.audit("service.case_delete", "case", case_id)
    return 200, {"deleted": True, "cases": removed}


@aira_route("POST", f"{BASE}/cases/bulk")
def cases_bulk(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _connect(request) as connection:
        result = desk.bulk(
            connection,
            payload.get("ids"),
            str(payload.get("action") or ""),
            actor=_actor(request),
            assignee=kit.text(payload.get("assignee"), "the assignee", max_len=120),
            origin=request,
        )
    request.audit(
        "service.case_bulk",
        "case",
        "",
        {"action": payload.get("action"), "done": result["done"]},
    )
    return 200, result


@aira_route("POST", f"{BASE}/cases/{ID}/charges")
def cases_charge(request: Request) -> tuple[int, dict[str, Any]]:
    case_id = request.match.group(1)
    with _connect(request) as connection:
        charge = desk.add_charge(
            connection, case_id, request.payload, actor=_actor(request), origin=request
        )
        detail = desk.case_detail(connection, case_id)
    request.audit("service.charge_add", "charge", charge["id"])
    return 201, {**detail, "charge": charge}


@aira_route("POST", f"{BASE}/charges/{ID}/paid")
def charges_paid(request: Request) -> tuple[int, dict[str, Any]]:
    charge_id = request.match.group(1)
    paid = kit.flag(request.payload.get("paid", True))
    with _connect(request) as connection:
        charge = desk.set_charge_paid(
            connection, charge_id, paid, actor=_actor(request)
        )
        detail = desk.case_detail(connection, charge["case_id"])
    request.audit(
        "service.charge_paid" if paid else "service.charge_unpaid", "charge", charge_id
    )
    return 200, {**detail, "charge": charge}


@aira_route("DELETE", f"{BASE}/charges/{ID}")
def charges_delete(request: Request) -> tuple[int, dict[str, Any]]:
    charge_id = request.match.group(1)
    with _connect(request) as connection:
        case_id = store.get_charge(connection, charge_id)["case_id"]
        desk.delete_charge(connection, charge_id, actor=_actor(request))
        detail = desk.case_detail(connection, case_id)
    request.audit("service.charge_delete", "charge", charge_id)
    return 200, detail


@aira_route("POST", f"{BASE}/cases/{ID}/reply")
def cases_reply(request: Request) -> tuple[int, dict[str, Any]]:
    case_id = request.match.group(1)
    with _connect(request) as connection:
        desk.reply(
            connection, case_id, request.payload, author=_actor(request), origin=request
        )
        detail = desk.case_detail(connection, case_id)
    request.audit("service.case_reply", "case", case_id)
    return 200, detail


@aira_route("POST", f"{BASE}/cases/{ID}/merge")
def cases_merge(request: Request) -> tuple[int, dict[str, Any]]:
    source_id = request.match.group(1)
    target_id = str(request.payload.get("into") or "")
    with _connect(request) as connection:
        case = desk.merge(connection, source_id, target_id, actor=_actor(request))
    request.audit("service.case_merge", "case", source_id, {"into": target_id})
    return 200, {"case": case}


@aira_route("POST", f"{BASE}/cases/{ID}/suggest")
def cases_suggest(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, desk.suggest_reply(connection, request.match.group(1))


@aira_route("POST", f"{BASE}/cases/{ID}/link")
def cases_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        case = store.get_case(connection, request.match.group(1))
        link = desk.case_link(connection, case)
    return 200, {"link": link, "url": kit.public_url(link["path"], request)}


# ---------------------------------------------------------------------------
# customers, reports, paid services
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/customers")
def customers_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _connect(request) as connection:
        people = desk.customers(connection, q=request.q("q"))
        if request.q("format") == "csv":
            currency = kit.business(connection)["currency"]
            rows = [
                {
                    **person,
                    "paid": kit.fmt_money(person["paid_cents"], currency),
                    "due": kit.fmt_money(person["due_cents"], currency),
                }
                for person in people
            ]
            return Raw(
                200,
                kit.csv_bytes(rows, desk.CUSTOMER_CSV_COLUMNS),
                "text/csv; charset=utf-8",
                filename="customers.csv",
            )
        return 200, {"customers": people}


@aira_route("GET", f"{BASE}/customers/cases")
def customers_cases(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"cases": desk.customer_cases(connection, request.q("key"))}


@aira_route("GET", f"{BASE}/reports")
def reports(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "stats": desk.stats(connection),
            "reports": desk.reports(connection),
            "checklist": desk.checklist(connection),
        }


@aira_route("GET", f"{BASE}/offers")
def offers_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "offers": desk.list_offers(connection),
            **desk.charge_lists(connection),
            "earned": desk.reports(connection)["money"],
            "help_link": public.link_for(connection, APP, "help_center", HELP_REF),
            "payment_link": kit.business(connection).get("payment_link") or "",
        }


@aira_route("POST", f"{BASE}/offers")
def offers_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        offer = desk.save_offer(connection, request.payload)
    request.audit("service.offer_create", "offer", offer["id"])
    return 201, {"offer": offer}


@aira_route("PATCH", f"{BASE}/offers/{ID}")
def offers_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        offer = desk.save_offer(connection, request.payload, request.match.group(1))
    request.audit("service.offer_update", "offer", offer["id"])
    return 200, {"offer": offer}


@aira_route("DELETE", f"{BASE}/offers/{ID}")
def offers_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        desk.delete_offer(connection, request.match.group(1))
    request.audit("service.offer_delete", "offer", request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# saved replies
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/macros")
def macros_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"macros": desk.list_macros(connection)}


@aira_route("POST", f"{BASE}/macros")
def macros_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 201, {"macro": desk.save_macro(connection, request.payload)}


@aira_route("PATCH", f"{BASE}/macros/{ID}")
def macros_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "macro": desk.save_macro(
                connection, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/macros/{ID}")
def macros_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.get_macro(connection, request.match.group(1))
        connection.execute(
            "DELETE FROM sv_macros WHERE id = ?", (request.match.group(1),)
        )
        connection.commit()
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/macros/{ID}/use")
def macros_use(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        text = desk.use_macro(
            connection,
            request.match.group(1),
            str(request.payload.get("case_id") or ""),
        )
    return 200, {"text": text}


# ---------------------------------------------------------------------------
# help centre
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/categories")
def categories_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"categories": desk.list_categories(connection)}


@aira_route("POST", f"{BASE}/categories")
def categories_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 201, {"category": desk.save_category(connection, request.payload)}


@aira_route("PATCH", f"{BASE}/categories/{ID}")
def categories_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "category": desk.save_category(
                connection, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/categories/{ID}")
def categories_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        desk.delete_category(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/articles")
def articles_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "articles": desk.list_articles(connection, q=request.q("q")),
            "categories": desk.list_categories(connection),
        }


@aira_route("POST", f"{BASE}/articles")
def articles_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        article = desk.save_article(connection, request.payload)
    request.audit("service.article_create", "article", article["id"])
    return 201, {"article": article}


@aira_route("GET", f"{BASE}/articles/{ID}")
def articles_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"article": store.get_article(connection, request.match.group(1))}


@aira_route("PATCH", f"{BASE}/articles/{ID}")
def articles_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        article = desk.save_article(connection, request.payload, request.match.group(1))
    request.audit("service.article_update", "article", article["id"])
    return 200, {"article": article}


@aira_route("DELETE", f"{BASE}/articles/{ID}")
def articles_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        desk.delete_article(connection, request.match.group(1))
    request.audit("service.article_delete", "article", request.match.group(1))
    return 200, {"deleted": True}


def _share(
    request: Request, kind: str, ref: str, title: str
) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        link = public.create_link(connection, APP, kind, ref, title=title)
        kit.log(
            connection, APP, "share", f"{title} link created", ref_type=kind, ref_id=ref
        )
        connection.commit()
    return 200, {"link": link, "url": kit.public_url(link["path"], request)}


def _unshare(request: Request, kind: str, ref: str) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        public.revoke(connection, APP, kind, ref)
    return 200, {"link": None}


@aira_route("POST", f"{BASE}/help/link")
def help_link(request: Request) -> tuple[int, dict[str, Any]]:
    return _share(request, "help_center", HELP_REF, "Help centre")


@aira_route("DELETE", f"{BASE}/help/link")
def help_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    return _unshare(request, "help_center", HELP_REF)


@aira_route("POST", f"{BASE}/chat/link")
def chat_link(request: Request) -> tuple[int, dict[str, Any]]:
    return _share(request, "chat", CHAT_REF, "Live chat")


@aira_route("DELETE", f"{BASE}/chat/link")
def chat_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    return _unshare(request, "chat", CHAT_REF)


# ---------------------------------------------------------------------------
# live chat (agents)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/chats")
def chats_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "chats": desk.list_chats(connection, include_ended=request.q("all") == "1"),
            "online": desk.chat_online(connection),
            "now": kit.now(),
        }


@aira_route("GET", f"{BASE}/chats/{ID}")
def chats_get(request: Request) -> tuple[int, dict[str, Any]]:
    after = request.qint("after", 0, 0, 2**62)
    with _connect(request) as connection:
        return 200, desk.chat_detail(connection, request.match.group(1), after)


@aira_route("POST", f"{BASE}/chats/{ID}/reply")
def chats_reply(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, desk.agent_chat_reply(
            connection,
            request.match.group(1),
            request.payload.get("body"),
            author=_actor(request),
        )


@aira_route("POST", f"{BASE}/chats/{ID}/end")
def chats_end(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, desk.end_chat(connection, request.match.group(1))


@aira_route("POST", f"{BASE}/chats/{ID}/case")
def chats_case(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        case = desk.chat_to_case(connection, request.match.group(1), origin=request)
    request.audit("service.chat_case", "case", case["id"])
    return 201, {"case": case}


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


def _help_page(connection: Any) -> dict[str, Any]:
    chat = public.link_for(connection, APP, "chat", CHAT_REF)
    return {
        "categories": [
            {"id": c["id"], "name": c["name"]} for c in desk.list_categories(connection)
        ],
        "articles": [
            desk.public_article(a)
            for a in desk.list_articles(connection, published_only=True)
        ],
        "chat_path": chat["path"] if chat and desk.chat_online(connection) else "",
        "offers": [
            {
                "id": o["id"],
                "title": o["title"],
                "description": o["description"],
                "price_cents": o["price_cents"],
            }
            for o in desk.list_offers(connection, active_only=True)
        ],
    }


@public.page(APP, "help_center")
def help_center_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return _help_page(connection)


@public.action(APP, "help_center", "search")
def help_search(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    rows = desk.list_articles(
        connection, published_only=True, q=str(payload.get("q") or "")
    )
    return 200, {"articles": [desk.public_article(a) for a in rows[:30]]}


@public.action(APP, "help_center", "article")
def help_article(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, {"article": desk.read_article(connection, str(payload.get("id") or ""))}


@public.action(APP, "help_center", "vote")
def help_vote(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, desk.vote(
        connection,
        str(payload.get("id") or ""),
        str(visitor.get("client") or ""),
        kit.flag(payload.get("helpful")),
    )


def _contact(
    connection: Any, payload: dict[str, Any], visitor: dict[str, Any], channel: str
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    if str(payload.get("website") or ""):
        return 201, {
            "reference": "",
            "path": "",
        }  # a bot filled the hidden field: accept quietly, keep nothing
    raw = {
        "subject": payload.get("subject") or "A question",
        "message": payload.get("message"),
        "name": payload.get("name"),
        "email": payload.get("email"),
        "phone": payload.get("phone"),
    }
    if not kit.text(raw["message"], "your message", max_len=10000, multiline=True):
        raise ValueError("Enter your message.")
    case = desk.create_case(connection, raw, channel=channel, origin=visitor)
    link = public.link_for(connection, APP, "case", case["id"]) or {}
    return 201, {"reference": case["reference"], "path": link.get("path", "")}


@public.query(APP, "help_center", "answers")
def help_answers(
    connection: Any, link: dict[str, Any], params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    # Asked while a visitor writes to the business: answers that might already help (read only).
    store.ensure(connection)
    return 200, {
        "articles": desk.suggest_articles(connection, str(params.get("q") or "")[:2000])
    }


@public.action(APP, "help_center", "request")
def help_request(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """A visitor asks for one of the business's paid services."""
    store.ensure(connection)
    if str(payload.get("website") or ""):
        return 201, {"reference": "", "path": ""}  # a bot filled the hidden field
    case = desk.request_offer(
        connection,
        {
            "offer_id": payload.get("offer_id"),
            "name": payload.get("name"),
            "email": payload.get("email"),
            "phone": payload.get("phone"),
            "message": payload.get("message"),
        },
        origin=visitor,
    )
    found = public.link_for(connection, APP, "case", case["id"]) or {}
    return 201, {"reference": case["reference"], "path": found.get("path", "")}


@public.action(APP, "help_center", "contact")
def help_contact(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    return _contact(connection, payload, visitor, "web")


@public.page(APP, "chat")
def chat_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    rules = store.settings(connection)
    return {
        "online": desk.chat_online(connection),
        "offline_message": rules["offline_message"],
    }


@public.action(APP, "chat", "start")
def chat_start(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    started = desk.start_chat(connection, payload)
    polled = desk.visitor_poll(
        connection, {"chat_id": started["chat_id"], "secret": started["secret"]}
    )
    return 201, {**started, "messages": polled["messages"], "cursor": polled["cursor"]}


@public.action(APP, "chat", "send")
def chat_send(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, desk.visitor_send(connection, payload)


@public.action(APP, "chat", "poll")
def chat_poll(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, desk.visitor_poll(connection, payload)


@public.query(APP, "chat", "messages")
def chat_messages(
    connection: Any, link: dict[str, Any], params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    # What the open chat window polls every few seconds (the page-view limit, not the action one).
    store.ensure(connection)
    return 200, desk.visitor_poll(connection, params)


@public.action(APP, "chat", "contact")
def chat_contact(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    return _contact(connection, payload, visitor, "chat")


def _case_page(connection: Any, case_id: str) -> dict[str, Any]:
    own = store.get_case(connection, case_id)
    case = desk.kept_case(connection, own)
    if case["id"] != own["id"] and not desk.same_customer(own, case):
        # Merged into another customer's case (before merges checked the customer, or the kept case's details
        # were changed later): never show this customer someone else's conversation or charges.
        return {
            "reference": own["reference"],
            "subject": own["subject"],
            "status": "closed",
            "created_at": own["created_at"],
            "thread": [],
            "can_reply": False,
            "can_rate": False,
            "rated": None,
            "note": desk.COMBINED,
            "charges": [],
            "due_cents": 0,
        }
    thread = [
        {
            "from": "you" if m["author_kind"] == "customer" else "us",
            "name": ""
            if m["author_kind"] == "customer"
            else (kit.business(connection).get("name") or "Support"),
            "body": m["body"],
            "attachments": m["attachments"],
            "at": m["created_at"],
        }
        for m in store.messages(connection, case["id"], public_only=True)
        if m["author_kind"] in ("customer", "agent")
    ]
    return {
        "reference": case["reference"],
        "subject": case["subject"],
        "status": case["status"],
        "created_at": case["created_at"],
        "thread": thread,
        "can_reply": case["status"] != "closed",
        "can_rate": case["status"] in ("solved", "closed")
        and case["csat_score"] is None,
        "rated": case["csat_score"],
        **desk.public_charges(connection, case["id"]),
    }


@public.page(APP, "case")
def case_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return _case_page(connection, link["ref_id"])


@public.action(APP, "case", "reply")
def case_reply(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    desk.customer_reply(connection, link["ref_id"], payload)
    return 200, _case_page(connection, link["ref_id"])


@public.action(APP, "case", "rate")
def case_rate(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    desk.rate(connection, link["ref_id"], payload)
    return 200, _case_page(connection, link["ref_id"])


# ---------------------------------------------------------------------------
# background and the Business hub
# ---------------------------------------------------------------------------


@background.task(APP)
def _background(connection: Any) -> dict[str, int]:
    """Overdue flags, satisfaction requests and auto-closing, also when nobody has Aira open."""
    return desk.tick(connection)


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    return desk.checklist(connection)


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    return desk.summary(connection)
