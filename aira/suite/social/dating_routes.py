"""Routes for Dating in Social (``/api/social/dating/*``), loaded with :mod:`social_routes`.

Every route works for the person asking, as :mod:`social_routes` does (their Social account, from the account's
database), and :mod:`aira.suite.social.dating` holds the rules: adults only (children and teens are refused by
every route), opt-in, and nothing of anyone's Social account in the answers. People are named by their dating
profile's id, matches by the match's id; the chat of a match goes through Social's chat system.

In the hosted build these are life routes (``life.read`` for GET, ``life.manage`` otherwise), derived by
``monosage_cloud.policy.suite_rules``; each member reaches only their own dating, an organization's owner and
admins have no view of their members' dating, and nothing here goes into the organization's audit log.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.notify import events as notify_events
from aira.suite.social import dating, service, store

BASE = "/api/social/dating"
ID = "([0-9a-f]{32})"
# Hosted: every Dating route is the member's own private business, never written to the organization's audit
# log (monosage_cloud.policy.suite_rules; GET stays life.read, the others life.manage).
HOSTED_RULES = {
    name: "private"
    for name in (
        "dating_me",
        "dating_save",
        "dating_pause",
        "dating_delete",
        "dating_export",
        "dating_badge",
        "dating_discover",
        "dating_likes",
        "dating_undo",
        "dating_profile",
        "dating_swipe",
        "dating_block",
        "dating_report",
        "dating_matches",
        "dating_match",
        "dating_match_seen",
        "dating_unmatch",
        "dating_messages",
        "dating_send",
        "dating_read",
    )
}


@contextmanager
def _open(request: Request) -> Iterator[tuple[Any, dict[str, Any], dict[str, Any]]]:
    """(social connection, who is asking, their Social account), refused for anyone under 18."""
    from aira.suite.social import social_routes

    with social_routes._identity(request) as (sc, ident):
        me = service.me(sc, ident)
        dating.gate(sc, ident, me)
        yield sc, ident, me


def _id(request: Request, group: int = 1) -> str:
    return request.match.group(group)


@aira_route("GET", f"{BASE}/me")
def dating_me(request: Request) -> tuple[int, dict[str, Any]]:
    """The person's dating profile (None until they turn Dating on), the choices it is made from and the counts."""
    with _open(request) as (sc, ident, me):
        found = dating.mine(sc, ident, me)
        known = dating.profile_birthday(ident)
        return 200, {
            "profile": dating.own_view(found) if found else None,
            "options": dating.options(),
            "limits": dating.limits(sc, me),
            "badge": dating.badge(sc, ident, me),
            "hosted": bool(ident.get("hosted")),
            "suggested": {
                "first_name": str(me.get("display_name") or "").split(" ")[0][:30],
                "birthday_known": bool(known),
                "age": dating.age_of(known) if known else None,
            },
        }


@aira_route("PUT", f"{BASE}/me")
def dating_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, {
            "profile": dating.own_view(dating.save(sc, ident, me, request.payload))
        }


@aira_route("POST", f"{BASE}/me/pause")
def dating_pause(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        paused = request.payload.get("paused", True)
        return 200, {
            "profile": dating.own_view(dating.pause(sc, ident, me, bool(paused)))
        }


@aira_route("DELETE", f"{BASE}/me")
def dating_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        dating.delete(sc, ident, me)
    return 200, {"profile": None}


@aira_route("GET", f"{BASE}/me/export")
def dating_export(request: Request) -> Raw:
    """The person's own dating data, as a file they keep."""
    with _open(request) as (sc, ident, me):
        data = dating.export(sc, ident, me)
    return Raw(
        200,
        json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
        "application/json; charset=utf-8",
        filename="dating.json",
    )


@aira_route("GET", f"{BASE}/badge")
def dating_badge(request: Request) -> tuple[int, dict[str, Any]]:
    """New matches, unread dating messages and likes: 0 for anyone without Dating (children and teens too)."""
    from aira.suite.social import social_routes

    with social_routes._identity(request) as (sc, ident):
        found = service.account_by_key(sc, ident["key"])
        if not found:
            return 200, {"matches": 0, "messages": 0, "likes": 0}
        return 200, dating.badge(sc, ident, service.me(sc, ident))


@aira_route("GET", f"{BASE}/discover")
def dating_discover(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        mode = "likes" if request.q("mode") == "likes" else ""
        return 200, dating.discover(sc, ident, me, mode=mode)


@aira_route("GET", f"{BASE}/likes")
def dating_likes(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, dating.likes_you(sc, ident, me)


@aira_route("POST", f"{BASE}/undo")
def dating_undo(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, dating.undo(sc, ident, me)


@aira_route("GET", f"{BASE}/profiles/{ID}")
def dating_profile(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, {"profile": dating.view(sc, ident, me, _id(request))}


@aira_route("POST", f"{BASE}/profiles/{ID}/swipe")
def dating_swipe(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"action": "like" | "pass" | "star"}``; a like back answers with the match."""
    with _open(request) as (sc, ident, me):
        result = dating.swipe(
            sc, ident, me, _id(request), str(request.payload.get("action") or "")
        )
        if result.get("match"):
            notify_events.dating_match(sc, me, result["match"]["id"])
        return 200, result


@aira_route("POST", f"{BASE}/profiles/{ID}/block")
def dating_block(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, dating.block(sc, ident, me, _id(request))


@aira_route("POST", f"{BASE}/profiles/{ID}/report")
def dating_report(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 201, dating.report(sc, ident, me, _id(request), request.payload)


@aira_route("GET", f"{BASE}/matches")
def dating_matches(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, dating.matches(sc, ident, me)


@aira_route("GET", f"{BASE}/matches/{ID}")
def dating_match(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, {"match": dating.match_detail(sc, ident, me, _id(request))}


@aira_route("POST", f"{BASE}/matches/{ID}/seen")
def dating_match_seen(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        dating.seen(sc, ident, me, _id(request))
    return 200, {"seen": True}


@aira_route("DELETE", f"{BASE}/matches/{ID}")
def dating_unmatch(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        dating.unmatch(sc, ident, me, _id(request))
    return 200, {"unmatched": True}


@aira_route("GET", f"{BASE}/matches/{ID}/messages")
def dating_messages(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        return 200, dating.chat_messages(
            sc, ident, me, _id(request), since=store.since_value(request.q("since"))
        )


@aira_route("POST", f"{BASE}/matches/{ID}/messages")
def dating_send(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        sent = dating.chat_send(sc, ident, me, _id(request), request.payload)
        notify_events.dating_message(
            sc, me, _id(request)
        )  # live pages; the match is told (no names)
        return 201, sent


@aira_route("POST", f"{BASE}/matches/{ID}/read")
def dating_read(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, ident, me):
        dating.chat_read(sc, ident, me, _id(request))
        notify_events.dating_changed(sc, _id(request))
    return 200, {"read": True}
