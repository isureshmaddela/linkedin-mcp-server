"""HTTP routes for Aira Events (``/api/events/*``), its public pages and its Business hub entries.

Organisers manage events, ticket types, orders, check-in, promo codes, the waitlist and sponsors. Customers use two
public pages: ``event`` (buy tickets, join the waitlist) and ``tickets`` (their codes, "I have paid").
Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.events import service, store
from aira.suite.events.store import APP

BASE = "/api/events"
ID = "([0-9a-f]{32})"

# Non-GET routes that only read (the hosted build lets members who may read the app call them).
HOSTED_RULES = {"quote_order": "read"}


def _actor(request: Request) -> str:
    return str((request.user or {}).get("email") or "")


def _event_payload(request: Request, connection: Any, event_id: str) -> dict[str, Any]:
    return {
        "event": service.event_view(connection, store.get_event(connection, event_id))
    }


# ---------------------------------------------------------------------------
# overview and events
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        data = service.overview(connection)
        data["checklist"] = _checklist(connection)
        data["activity"] = kit.activity(connection, app=APP, limit=15)
        data["categories"] = list(store.CATEGORIES)
    return 200, data


@aira_route("GET", f"{BASE}/events")
def events_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "events": service.list_events(
                connection, scope=request.q("scope"), q=request.q("q")
            ),
            "categories": list(store.CATEGORIES),
        }


@aira_route("POST", f"{BASE}/events")
def events_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        event = service.create_event(connection, request.payload, actor=_actor(request))
        for position, ticket in enumerate(request.payload.get("ticket_types") or []):
            if isinstance(ticket, dict):
                service.add_ticket_type(
                    connection,
                    event["id"],
                    {**ticket, "position": ticket.get("position", position)},
                )
        data = _event_payload(request, connection, event["id"])
    request.audit("events.event_create", "event", event["id"])
    return 201, data


@aira_route("GET", f"{BASE}/events/{ID}")
def events_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, _event_payload(request, connection, request.match.group(1))


@aira_route("PATCH", f"{BASE}/events/{ID}")
def events_update(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        service.update_event(connection, event_id, request.payload)
        data = _event_payload(request, connection, event_id)
    request.audit("events.event_update", "event", event_id)
    return 200, data


@aira_route("DELETE", f"{BASE}/events/{ID}")
def events_delete(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_event(connection, event_id)
    request.audit("events.event_delete", "event", event_id)
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/events/{ID}/status")
def events_status(request: Request) -> tuple[int, dict[str, Any]]:
    """Publish, unpublish (draft) or end an event; cancelling has its own route because it tells ticket holders."""
    event_id = request.match.group(1)
    status = kit.choice(
        request.payload.get("status"), "the status", ("draft", "published", "ended")
    )
    with request.connect() as connection:
        store.ensure(connection)
        service.set_status(connection, event_id, status, actor=_actor(request))
        if status == "published":
            event = store.get_event(connection, event_id)
            public.create_link(connection, APP, "event", event_id, title=event["title"])
        data = _event_payload(request, connection, event_id)
    request.audit(f"events.event_{status}", "event", event_id)
    return 200, data


@aira_route("POST", f"{BASE}/events/{ID}/cancel")
def events_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        result = service.cancel_event(
            connection,
            event_id,
            message=kit.text(
                request.payload.get("message"),
                "the message",
                max_len=5000,
                multiline=True,
            ),
            via=request,
            actor=_actor(request),
        )
        data = {
            **_event_payload(request, connection, event_id),
            "notified": result["notified"],
        }
    request.audit(
        "events.event_cancel", "event", event_id, {"notified": result["notified"]}
    )
    return 200, data


@aira_route("POST", f"{BASE}/events/{ID}/duplicate")
def events_duplicate(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        copy = service.duplicate_event(
            connection,
            request.match.group(1),
            days_later=request.payload.get("days_later", 7),
        )
        data = _event_payload(request, connection, copy["id"])
    request.audit("events.event_duplicate", "event", copy["id"])
    return 201, data


@aira_route("POST", f"{BASE}/events/{ID}/draft-description")
def events_draft_description(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        event = store.get_event(connection, request.match.group(1))
        return 200, service.draft_description(event, kit.business(connection)["name"])


@aira_route("POST", f"{BASE}/events/{ID}/link")
def events_link(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        event = store.get_event(connection, request.match.group(1))
        return 200, {
            "link": public.create_link(
                connection, APP, "event", event["id"], title=event["title"]
            )
        }


@aira_route("DELETE", f"{BASE}/events/{ID}/link")
def events_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        event = store.get_event(connection, request.match.group(1))
        return 200, {"revoked": public.revoke(connection, APP, "event", event["id"])}


@aira_route("POST", f"{BASE}/events/{ID}/announce")
def events_announce(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        if kit.flag(request.payload.get("thanks")):
            count = service.thank_attendees(
                connection,
                event_id,
                message=kit.text(
                    request.payload.get("body"),
                    "the message",
                    max_len=10000,
                    multiline=True,
                ),
                via=request,
            )
        else:
            count = service.announce(
                connection,
                event_id,
                subject=str(request.payload.get("subject") or ""),
                body=str(request.payload.get("body") or ""),
                via=request,
            )
    request.audit("events.announce", "event", event_id, {"prepared": count})
    return 200, {"prepared": count}


@aira_route("GET", f"{BASE}/events/{ID}/attendees.csv")
def events_attendees_csv(request: Request) -> Raw:
    with request.connect() as connection:
        store.ensure(connection)
        event = store.get_event(connection, request.match.group(1))
        rows = service.attendees_rows(connection, event["id"])
    for row in rows:
        row["price"] = f"{int(row.pop('price_cents') or 0) / 100:.2f}"
    columns = (
        "code",
        "holder_name",
        "ticket_type",
        "status",
        "checked_in_at",
        "price",
        "reference",
        "buyer_name",
        "buyer_email",
        "buyer_phone",
        "order_status",
        "ordered_at",
    )
    return Raw(
        200,
        kit.csv_bytes(rows, columns),
        "text/csv; charset=utf-8",
        filename=f"attendees-{kit.slug(event['title'])}.csv",
    )


# ---------------------------------------------------------------------------
# ticket types
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/events/{ID}/ticket-types")
def ticket_types_create(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        position = len(store.ticket_types(connection, event_id))
        service.add_ticket_type(
            connection, event_id, {"position": position, **request.payload}
        )
        return 201, _event_payload(request, connection, event_id)


@aira_route("PATCH", f"{BASE}/ticket-types/{ID}")
def ticket_types_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        ticket = service.update_ticket_type(
            connection, request.match.group(1), request.payload
        )
        return 200, _event_payload(request, connection, ticket["event_id"])


@aira_route("DELETE", f"{BASE}/ticket-types/{ID}")
def ticket_types_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        ticket = store.get_ticket_type(connection, request.match.group(1))
        result = service.delete_ticket_type(connection, ticket["id"])
        return 200, {
            **result,
            **_event_payload(request, connection, ticket["event_id"]),
        }


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/orders")
def orders_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        orders = service.list_orders(
            connection,
            event_id=request.q("event_id"),
            status=request.q("status"),
            q=request.q("q"),
        )
    return 200, {"orders": orders, "statuses": list(store.ORDER_STATUSES)}


@aira_route("GET", f"{BASE}/orders/{ID}")
def orders_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        order = store.get_order(connection, request.match.group(1))
        return 200, {
            "order": order,
            "event": store.get_event(connection, order["event_id"]),
            "tickets": store.tickets_of_order(connection, order["id"]),
            "link": public.link_for(connection, APP, "tickets", order["id"]),
        }


@aira_route("POST", f"{BASE}/orders/quote")
def quote_order(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with request.connect() as connection:
        store.ensure(connection)
        event = store.get_event(
            connection,
            kit.text(payload.get("event_id"), "the event", max_len=32, required=True),
        )
        return 200, service.quote(
            connection,
            event,
            payload.get("items"),
            str(payload.get("promo_code") or ""),
            staff=True,
        )


@aira_route("POST", f"{BASE}/orders")
def orders_create(request: Request) -> tuple[int, dict[str, Any]]:
    """An order taken by the organiser (at the door, by phone) or complimentary tickets (``comp``)."""
    payload = request.payload
    comp = kit.flag(payload.get("comp"))
    with request.connect() as connection:
        store.ensure(connection)
        event_id = kit.text(
            payload.get("event_id"), "the event", max_len=32, required=True
        )
        result = service.place_order(
            connection, event_id, payload, source="manual", comp=comp, via=request
        )
        order = result["order"]
        if kit.flag(payload.get("paid")) and order["status"] == "pending_payment":
            order = service.mark_paid(
                connection, order["id"], via=request, actor=_actor(request)
            )
    request.audit("events.order_create", "order", order["id"], {"comp": comp})
    return 201, {"order": order, "link": result["tickets_link"]}


@aira_route("POST", f"{BASE}/orders/{ID}/paid")
def orders_paid(request: Request) -> tuple[int, dict[str, Any]]:
    order_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        order = service.mark_paid(
            connection, order_id, via=request, actor=_actor(request)
        )
    request.audit("events.order_paid", "order", order_id)
    return 200, {"order": order}


@aira_route("POST", f"{BASE}/orders/{ID}/cancel")
def orders_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    order_id = request.match.group(1)
    refund = kit.flag(request.payload.get("refund"))
    with request.connect() as connection:
        store.ensure(connection)
        order = service.cancel_order(
            connection, order_id, refund=refund, actor=_actor(request)
        )
    request.audit(
        "events.order_refund" if refund else "events.order_cancel", "order", order_id
    )
    return 200, {"order": order}


@aira_route("POST", f"{BASE}/orders/{ID}/resend")
def orders_resend(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        link = service.resend_tickets(connection, request.match.group(1), via=request)
    return 200, {"queued": True, "link": link}


# ---------------------------------------------------------------------------
# check-in
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/events/{ID}/checkin")
def checkin_list(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        event = store.get_event(connection, event_id)
        counts = store.event_counts(connection, event_id)
        attendees = service.find_attendees(
            connection,
            event_id,
            request.q("q"),
            limit=request.qint("limit", 200, 1, 2000),
        )
    return 200, {
        "event": {
            "id": event["id"],
            "title": event["title"],
            "starts_at": event["starts_at"],
            "capacity": event["capacity"],
        },
        "counts": counts,
        "attendees": attendees,
    }


@aira_route("POST", f"{BASE}/events/{ID}/checkin")
def checkin(request: Request) -> tuple[int, dict[str, Any]]:
    event_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        store.get_event(connection, event_id)
        result = service.check_in(
            connection,
            event_id,
            str(request.payload.get("code") or ""),
            actor=_actor(request),
        )
        result["counts"] = store.event_counts(connection, event_id)
    return 200, result


@aira_route("POST", f"{BASE}/tickets/{ID}/undo-checkin")
def checkin_undo(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        ticket = service.undo_check_in(connection, request.match.group(1))
        return 200, {
            "ticket": ticket,
            "counts": store.event_counts(connection, ticket["event_id"]),
        }


# ---------------------------------------------------------------------------
# promo codes, waitlist, sponsors
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/promos")
def promos_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        rows = kit.fetch_all(
            connection,
            """SELECT p.*, e.title AS event_title, tt.name AS ticket_type_name FROM ev_promos p
            LEFT JOIN ev_events e ON e.id = p.event_id LEFT JOIN ev_ticket_types tt ON tt.id = p.ticket_type_id
            ORDER BY p.created_at DESC""",
        )
    return 200, {"promos": rows}


@aira_route("POST", f"{BASE}/promos")
def promos_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 201, {"promo": service.create_promo(connection, request.payload)}


@aira_route("PATCH", f"{BASE}/promos/{ID}")
def promos_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "promo": service.update_promo(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/promos/{ID}")
def promos_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        promo = kit.require_row(
            connection, "ev_promos", request.match.group(1), "promo code"
        )
        if promo["used"]:
            kit.update_row(connection, "ev_promos", promo["id"], {"active": 0})
        else:
            connection.execute("DELETE FROM ev_promos WHERE id = ?", (promo["id"],))
        connection.commit()
    return 200, {"deleted": not promo["used"], "deactivated": bool(promo["used"])}


@aira_route("GET", f"{BASE}/waitlist")
def waitlist_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        params: list[Any] = []
        where = "WHERE w.status IN ('waiting', 'offered')"
        if request.q("event_id"):
            where += " AND w.event_id = ?"
            params.append(request.q("event_id"))
        rows = kit.fetch_all(
            connection,
            f"SELECT w.*, e.title AS event_title FROM ev_waitlist w JOIN ev_events e ON e.id = w.event_id {where} ORDER BY w.created_at",  # noqa: S608 - fixed clauses
            params,
        )
    return 200, {"waitlist": rows}


@aira_route("POST", f"{BASE}/waitlist/{ID}/offer")
def waitlist_offer(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "entry": service.offer_waitlist(
                connection, request.match.group(1), via=request
            )
        }


@aira_route("POST", f"{BASE}/waitlist/{ID}/status")
def waitlist_status(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "entry": service.set_waitlist_status(
                connection,
                request.match.group(1),
                str(request.payload.get("status") or ""),
            )
        }


@aira_route("GET", f"{BASE}/sponsors")
def sponsors_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        rows = kit.fetch_all(
            connection,
            "SELECT s.*, e.title AS event_title FROM ev_sponsors s LEFT JOIN ev_events e ON e.id = s.event_id ORDER BY s.created_at DESC",
        )
    pipeline = {
        status: sum(int(r["amount_cents"]) for r in rows if r["status"] == status)
        for status in store.SPONSOR_STATUSES
    }
    return 200, {
        "sponsors": rows,
        "pipeline": pipeline,
        "statuses": list(store.SPONSOR_STATUSES),
    }


@aira_route("POST", f"{BASE}/sponsors")
def sponsors_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 201, {"sponsor": service.save_sponsor(connection, request.payload)}


@aira_route("PATCH", f"{BASE}/sponsors/{ID}")
def sponsors_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "sponsor": service.save_sponsor(
                connection, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/sponsors/{ID}")
def sponsors_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_sponsor(connection, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, "event")
def event_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.public_event(connection, store.get_event(connection, link["ref_id"]))


@public.action(APP, "event", "quote")
def event_quote(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    event = store.get_event(connection, link["ref_id"])
    priced = service.quote(
        connection,
        event,
        payload.get("items"),
        str(payload.get("promo_code") or "").strip(),
    )
    return 200, {
        k: priced[k]
        for k in ("lines", "subtotal_cents", "discount_cents", "total_cents", "tickets")
    }


@public.action(APP, "event", "order")
def event_order(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    result = service.place_order(
        connection, link["ref_id"], payload, source="public", via=visitor
    )
    order = result["order"]
    return 201, {
        **service.public_order(connection, order),
        "tickets_path": result["tickets_link"]["path"],
    }


@public.action(APP, "event", "join_waitlist")
def event_waitlist(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    event = store.get_event(connection, link["ref_id"])
    service.join_waitlist(connection, event, payload)
    return 201, {"joined": True}


@public.page(APP, "tickets")
def tickets_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.public_order(connection, store.get_order(connection, link["ref_id"]))


@public.action(APP, "tickets", "notify_paid")
def tickets_paid(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    order = service.claim_paid(connection, link["ref_id"])
    return 200, service.public_order(connection, order)


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    store.ensure(connection)
    profile = kit.business(connection)
    shared = kit.count(
        connection,
        "SELECT COUNT(*) FROM suite_public_links WHERE app = ? AND kind = 'event' AND revoked_at IS NULL",
        (APP,),
    )
    return [
        kit.step(
            "profile",
            "Add your business details",
            bool(profile["name"]),
            "Name and contact on your event pages",
        ),
        kit.step(
            "event",
            "Create your first event",
            kit.count(connection, "SELECT COUNT(*) FROM ev_events") > 0,
            "Title, venue and time",
        ),
        kit.step(
            "tickets",
            "Add a ticket type",
            kit.count(connection, "SELECT COUNT(*) FROM ev_ticket_types") > 0,
            "Free or paid, with a quantity",
        ),
        kit.step(
            "publish",
            "Publish and share the event page",
            bool(shared)
            and kit.count(
                connection, "SELECT COUNT(*) FROM ev_events WHERE status = 'published'"
            )
            > 0,
            "Post the link where your audience is",
        ),
        kit.step(
            "order",
            "Get your first order",
            kit.count(connection, "SELECT COUNT(*) FROM ev_orders") > 0,
            "Share the link with friends first",
        ),
        kit.step(
            "checkin",
            "Check in your first guest",
            kit.count(
                connection,
                "SELECT COUNT(*) FROM ev_tickets WHERE status = 'checked_in'",
            )
            > 0,
            "Use Check-in at the door",
        ),
    ]


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    store.ensure(connection)
    cards = service.overview(connection)["cards"]
    return {"cards": [cards[0], cards[2], cards[4]]}
