"""Routes for Family (``/api/family/*``), its public board page and its Today cards and pulse.

Every route starts from the person using Aira (:func:`aira.suite.life.current`). Loaded by
:func:`aira.suite.load_apps`.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import life, public
from aira.suite.family import service, store
from aira.suite.family.store import APP

BASE = "/api/family"
ID = "([0-9a-f]{32})"


def _connect(request: Request) -> sqlite3.Connection:
    """The account's database. A brand-new account is migrated by the first request that connects; another
    request starting at that moment (the first Family screen asks several things at once) can see "database
    schema has changed", so that one waits a moment and connects again (as Social does)."""
    for attempt in range(4):
        try:
            return request.connect()
        except sqlite3.OperationalError as error:
            if "schema has changed" not in str(error) or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = _connect(request)
    try:
        me = life.current(request, connection)
        store.ensure(connection)
    except Exception:
        connection.close()
        raise
    return connection, me


# ---------------------------------------------------------------------------
# overview and feed
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        data = service.overview(connection, me)
        data["board_link"] = (
            public.link_for(connection, APP, "board", service.BOARD_REF)
            if data["is_adult"]
            else None
        )
        return 200, data


@aira_route("GET", f"{BASE}/posts")
def posts_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.list_posts(
            connection,
            me,
            since=request.q("since"),
            limit=request.qint("limit", 50, 1, 200),
        )


@aira_route("POST", f"{BASE}/posts")
def posts_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        post = service.create_post(connection, me, request.payload)
    return 201, {"post": post}


@aira_route("POST", f"{BASE}/posts/seen")
def posts_seen(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        moment = store.mark_seen(connection, me["id"], "feed")
        connection.commit()
        return 200, {"seen_at": moment, "unread": 0}


@aira_route("GET", f"{BASE}/posts/pending")
def posts_pending(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"posts": service.pending_posts(connection, me)}


@aira_route("PATCH", f"{BASE}/posts/{ID}")
def posts_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "post": service.update_post(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/posts/{ID}")
def posts_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_post(connection, me, request.match.group(1))
    request.audit("family.post_remove", "post", request.match.group(1))
    return 200, {"removed": True}


@aira_route("POST", f"{BASE}/posts/{ID}/pin")
def posts_pin(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        pinned = request.payload.get("pinned", True)
        return 200, {
            "post": service.pin_post(
                connection, me, request.match.group(1), bool(pinned)
            )
        }


@aira_route("POST", f"{BASE}/posts/{ID}/approve")
def posts_approve(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        post = service.approve_post(connection, me, request.match.group(1))
    request.audit("family.post_approve", "post", request.match.group(1))
    return 200, {"post": post}


@aira_route("POST", f"{BASE}/posts/{ID}/react")
def posts_react(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.react(
            connection,
            request.match.group(1),
            me["id"],
            str(request.payload.get("emoji") or ""),
        )


@aira_route("POST", f"{BASE}/posts/{ID}/comments")
def posts_comment(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {
            "comment": service.add_comment(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/comments/{ID}")
def comments_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_comment(connection, me, request.match.group(1))
        return 200, {"removed": True}


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/rooms")
def rooms_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "rooms": service.list_rooms(connection, me),
            "people": list(store.people(connection).values()),
        }


@aira_route("POST", f"{BASE}/rooms/direct")
def rooms_direct(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        room = service.direct_room(
            connection, me, str(request.payload.get("profile_id") or "")
        )
        return 200, {"room": {"id": room["id"], "kind": room["kind"]}}


@aira_route("GET", f"{BASE}/rooms/{ID}/messages")
def rooms_messages(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.messages(
            connection,
            me,
            request.match.group(1),
            since=request.q("since"),
            limit=request.qint("limit", 100, 1, 300),
        )


@aira_route("POST", f"{BASE}/rooms/{ID}/messages")
def rooms_send(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {
            "message": service.send_message(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/rooms/{ID}/read")
def rooms_read(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.mark_read(connection, me, request.match.group(1))


@aira_route("DELETE", f"{BASE}/messages/{ID}")
def messages_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_message(connection, me, request.match.group(1))
        return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# calendar and dates
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/events")
def events_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "events": service.list_events(
                connection, me, start=request.q("from"), end=request.q("to")
            )
        }


@aira_route("GET", f"{BASE}/events.ics")
def events_ics(request: Request) -> Raw:
    """The family calendar (the events you are invited to) as a file a phone's calendar imports."""
    connection, me = _open(request)
    with connection:
        body = service.calendar_ics(
            connection, me, title=f"Family calendar ({me['name']})"
        )
    return Raw(
        200, body, "text/calendar; charset=utf-8", filename="family-calendar.ics"
    )


@aira_route("POST", f"{BASE}/events")
def events_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"event": service.create_event(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/events/{ID}")
def events_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "event": service.update_event(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/events/{ID}")
def events_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.cancel_event(connection, me, request.match.group(1))
        return 200, {"cancelled": True}


@aira_route("POST", f"{BASE}/events/{ID}/rsvp")
def events_rsvp(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "event": service.rsvp(
                connection,
                me,
                request.match.group(1),
                str(request.payload.get("answer") or ""),
            )
        }


@aira_route("GET", f"{BASE}/dates")
def dates_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "dates": service.list_dates(connection),
            "wishes": service.wish_counts(connection),
        }


@aira_route("POST", f"{BASE}/dates")
def dates_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"date": service.add_date(connection, me, request.payload)}


@aira_route("DELETE", f"{BASE}/dates/{ID}")
def dates_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_date(connection, me, request.match.group(1))
        return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/wishes")
def wishes(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"post": service.wish(connection, me, request.payload)}


# ---------------------------------------------------------------------------
# wish lists (gift ideas; "I'll get this" stays secret from the person it is for)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/wishlists")
def wishlists(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"lists": service.wish_lists(connection, me)}


@aira_route("POST", f"{BASE}/wishlists")
def wishlist_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"wish": service.add_wish(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/wishlists/{ID}")
def wishlist_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "wish": service.update_wish(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/wishlists/{ID}")
def wishlist_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_wish(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/wishlists/{ID}/claim")
def wishlist_claim(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "wish": service.claim_wish(connection, me, request.match.group(1), True)
        }


@aira_route("DELETE", f"{BASE}/wishlists/{ID}/claim")
def wishlist_unclaim(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "wish": service.claim_wish(connection, me, request.match.group(1), False)
        }


# ---------------------------------------------------------------------------
# the board (a public link for relatives without an account)
# ---------------------------------------------------------------------------


def _adult(me: dict[str, Any]) -> None:
    if not (life.is_adult(me) or me.get("owner")):
        raise PermissionError("Ask an adult to manage the family board.")


@aira_route("GET", f"{BASE}/board")
def board_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        _adult(me)
        return 200, {
            "settings": service.board_settings(connection),
            "link": public.link_for(connection, APP, "board", service.BOARD_REF),
            "pending": service.pending_posts(connection, me),
        }


@aira_route("POST", f"{BASE}/board")
def board_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "settings": service.save_board_settings(connection, me, request.payload)
        }


@aira_route("POST", f"{BASE}/board/link")
def board_link(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        _adult(me)
        link = public.create_link(
            connection,
            APP,
            "board",
            service.BOARD_REF,
            title=service.board_settings(connection)["title"],
        )
    request.audit("family.board_link", "board", service.BOARD_REF)
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/board/link")
def board_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        _adult(me)
        public.revoke(connection, APP, "board", service.BOARD_REF)
    request.audit("family.board_unlink", "board", service.BOARD_REF)
    return 200, {"link": None}


@public.page(APP, "board")
def board_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return {
        **service.board_page(connection),
        "brand": False,
    }  # the family's page, not the business's


@public.action(APP, "board", "post")
def board_post(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 201, service.board_post(connection, payload, visitor)


@public.action(APP, "board", "claim")
def board_claim(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, service.board_claim(connection, payload, visitor)


@public.action(APP, "board", "react")
def board_react(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, service.board_react(connection, payload, visitor)


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    return service.today_cards(connection, me)


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    return service.pulse(connection, me, since)
