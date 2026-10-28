"""The catalog page of Courses & Events: one public page with every published course and every upcoming event,
each linking to its own page. Customers browse it; partners share it with their code (``?ref=CODE``), which the
page passes on to every link.

Only what a customer may see is returned: titles, prices, dates, ratings and whether there is room. A course or an
event appears once its own page link exists (publishing makes it).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit, public
from aira.suite.academy import store as academy_store
from aira.suite.events import service as events_service
from aira.suite.events import store as events_store

APP = "academy"
KIND = "catalog"
REF = "catalog"  # one catalog per business


def link(connection: sqlite3.Connection) -> dict[str, Any] | None:
    return public.link_for(connection, APP, KIND, REF)


def create(connection: sqlite3.Connection) -> dict[str, Any]:
    name = kit.business(connection)["name"]
    return public.create_link(
        connection,
        APP,
        KIND,
        REF,
        title=f"{name} courses and events" if name else "Courses and events",
    )


def _price(state: dict[str, Any]) -> dict[str, Any]:
    """What an event card says about price, from the ticket types on sale right now only.

    ``from_cents`` is the cheapest paid ticket on sale (0 when only free tickets are on sale, ``None`` when nothing
    is on sale); ``free_option`` says a free ticket is on sale next to paid ones; ``sales`` is ``open``, ``soon``
    (a ticket type's sales start later), ``sold_out`` or ``closed``.
    """
    selling = [t for t in state["types"] if t["on_sale"]]
    paid = [int(t["price_cents"]) for t in selling if int(t["price_cents"]) > 0]
    free = any(int(t["price_cents"]) == 0 for t in selling)
    if selling:
        sales = "open"
    elif state["sold_out"]:
        sales = "sold_out"
    elif any(t["reason"] == "Sales have not started" for t in state["types"]):
        sales = "soon"
    else:
        sales = "closed"
    return {
        "from_cents": min(paid) if paid else (0 if free else None),
        "free_option": bool(paid) and free,
        "sales": sales,
    }


def page(connection: sqlite3.Connection) -> dict[str, Any]:
    academy_store.ensure(connection)
    events_store.ensure(connection)
    courses = []
    for course in kit.fetch_all(
        connection,
        "SELECT * FROM ac_courses WHERE status = 'published' ORDER BY published_at DESC, title COLLATE NOCASE",
    ):
        found = public.link_for(connection, "academy", "course", course["id"])
        if not found:
            continue
        lessons = kit.fetch_all(
            connection,
            "SELECT minutes FROM ac_lessons WHERE course_id = ?",
            (course["id"],),
        )
        seats_left = None
        if course["capacity"]:
            seats_left = max(
                0,
                int(course["capacity"])
                - academy_store.seats_taken(connection, course["id"]),
            )
        courses.append(
            {
                "title": course["title"],
                "subtitle": course["subtitle"],
                "level": course["level"],
                "price_cents": int(course["price_cents"]),
                "cover_url": course["cover_url"],
                "lessons": len(lessons),
                "minutes": sum(int(row["minutes"] or 0) for row in lessons),
                "rating": academy_store.rating(connection, course["id"]),
                "full": seats_left == 0,
                "path": found["path"],
            }
        )
    events = []
    for event in kit.fetch_all(
        connection,
        "SELECT * FROM ev_events WHERE status = 'published' AND ends_at > ? ORDER BY starts_at LIMIT 100",
        (kit.now(),),
    ):
        found = public.link_for(connection, "events", "event", event["id"])
        if not found:
            continue
        state = events_service.availability(connection, event)
        events.append(
            {
                "title": event["title"],
                "category": event["category"],
                "venue_name": event["venue_name"],
                "online": bool(event["online_url"]),
                "starts_at": event["starts_at"],
                "cover_url": event["cover_url"],
                **_price(state),
                "sold_out": state["sold_out"],
                "on_sale": state["on_sale"],
                "path": found["path"],
            }
        )
    return {
        "courses": courses,
        "events": events,
        "timezone": kit.business(connection)["timezone"],
    }
