"""Routes for Social (``/api/social/*``), its public profile page and its Today card and pulse.

Every route starts from the person using Aira (:func:`aira.suite.life.current`, in the account's database) and
then works in the platform-wide social database (:mod:`aira.suite.social.db`). Path parameters are ids only;
handles, tags and searches go in the query. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any

import json
import sqlite3
import time

from aira.airaapi.http import Raw, Request, aira_route
from aira.notify import events as notify_events
from aira.suite import kit, life, public
from aira.suite.social import db, market, photos, service, store
from aira.suite.social import dating_routes as _dating_routes  # noqa: F401 - registers Dating's routes (/api/social/dating/*)
from aira.suite.social.store import APP

BASE = "/api/social"
ID = "([0-9a-f]{32})"
TOKEN = f"({public.TOKEN_PATTERN})"


def _account_db(request: Request) -> sqlite3.Connection:
    """The account's database. A brand-new account is migrated by the first request that connects: another
    request starting at that moment can see "database schema has changed" while it checks the version (the
    first Social screen asks several things at once), so that one waits a moment and connects again."""
    for attempt in range(4):
        try:
            return request.connect()
        except sqlite3.OperationalError as error:
            if "schema has changed" not in str(error) or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


@contextmanager
def _identity(request: Request) -> Iterator[tuple[Any, dict[str, Any]]]:
    """(social connection, who is asking): the person comes from the account's database."""
    connection = _account_db(request)
    try:
        ident = service.identity(request, connection)
    finally:
        connection.close()
    with closing(db.connect()) as sc:
        yield sc, ident


@contextmanager
def _open(request: Request) -> Iterator[tuple[Any, dict[str, Any]]]:
    """(social connection, the person's Social account); SetupRequired (404 ``social_setup``) without one."""
    with _identity(request) as (sc, ident):
        yield sc, service.me(sc, ident)


def _account_or_none(sc: Any, ident: dict[str, Any]) -> dict[str, Any] | None:
    return service.me(sc, ident) if service.account_by_key(sc, ident["key"]) else None


def _id(request: Request, group: int = 1) -> str:
    return request.match.group(group)


# ---------------------------------------------------------------------------
# my account
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/me")
def me_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        me = _account_or_none(sc, ident)
        if me is None:
            return 200, {
                "account": None,
                "suggested_handle": service.suggest_handle(
                    sc, ident["profile"].get("name", "")
                ),
                "name": ident["profile"].get("name", ""),
                "avatar": ident["profile"].get("emoji") or "🙂",
                "age_group": ident["profile"].get("age_group") or "adult",
                "moderator": ident["moderator"],
            }
        return 200, {
            "account": service.account_out(me),
            "moderator": ident["moderator"],
            "badge": {
                "messages": service.unread_messages(sc, me),
                "notifications": service.unread_notifications(sc, me),
            },
        }


@aira_route("POST", f"{BASE}/me")
def me_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        account = service.account_out(service.create_account(sc, ident, request.payload))
    return 201, {"account": account}


@aira_route("PATCH", f"{BASE}/me")
def me_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "account": service.account_out(service.update_account(sc, me, request.payload))
        }


@aira_route("GET", f"{BASE}/me/share")
def me_share_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (_sc, me):
        with closing(_account_db(request)) as connection:
            return 200, {"link": public.link_for(connection, APP, "profile", me["id"])}


@aira_route("POST", f"{BASE}/me/share")
def me_share(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (_sc, me):
        if me["age_group"] in ("child", "teen"):
            raise PermissionError(
                "Young people's profiles are not shared outside Monosage."
            )
        if me["visibility"] != "public":
            raise PermissionError("Make your profile public first.")
        with closing(_account_db(request)) as connection:
            link = public.create_link(
                connection, APP, "profile", me["id"], title=me["display_name"]
            )
    request.audit("social.profile_share", "account", me["id"])
    return 201, {"link": link}


@aira_route("DELETE", f"{BASE}/me/share")
def me_unshare(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (_sc, me):
        with closing(_account_db(request)) as connection:
            public.revoke(connection, APP, "profile", me["id"])
    return 200, {"link": None}


@aira_route("GET", f"{BASE}/me/export")
def me_export(request: Request) -> Raw:
    """Everything this person wrote or set on Social, as a file they keep (never other people's words)."""
    with _open(request) as (sc, me):
        data = service.export_account(sc, me["id"])
    request.audit("social.export", "account", me["id"])
    return Raw(
        200,
        json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
        "application/json; charset=utf-8",
        filename=f"social-{me['handle']}.json",
    )


@aira_route("GET", f"{BASE}/badge")
def badge(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        me = _account_or_none(sc, ident)
        if me is None:
            return 200, {"messages": 0, "notifications": 0}
        return 200, {
            "messages": service.unread_messages(sc, me),
            "notifications": service.unread_notifications(sc, me),
        }


# ---------------------------------------------------------------------------
# people, friends, follows and blocks
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.search(sc, me, request.q("q"))


@aira_route("GET", f"{BASE}/people/{ID}")
def person_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        target = service.visible_account(sc, me, _id(request))
        view = service.profile_view(sc, me, target)
        full = (
            view["relation"] in ("self", "friends") or target["visibility"] == "public"
        )
        return 200, {
            "person": {**view, "videos_channel": _videos_channel(target) if full else ""},
            "posts": service.person_posts(sc, me, target) if full else [],
            "highlights": service.highlights(sc, me, target) if full else [],
        }


def _videos_channel(target: dict[str, Any]) -> str:
    """The person's channel in Videos (their reels live there), or "" (no channel, or Videos is not set up)."""
    if target.get("age_group") == "child":
        return ""  # children's channels are not shown to others
    try:
        from aira.suite.videos import db as videos_db

        with closing(videos_db.connect()) as vsc:
            found = store.row(
                vsc,
                "SELECT id FROM vi_channels WHERE person_key = ? AND suspended_at IS NULL",
                (target["person_key"],),
            )
    except (RuntimeError, sqlite3.Error, KeyError):
        return ""
    return found["id"] if found else ""


@aira_route("GET", f"{BASE}/people/{ID}/grid")
def person_grid(request: Request) -> tuple[int, dict[str, Any]]:
    """``?kind=photos`` their photos, ``?kind=tagged`` posts that mention them, as grid squares."""
    with _open(request) as (sc, me):
        target = service.visible_account(sc, me, _id(request))
        view = service.profile_view(sc, me, target)
        if not view["full"]:
            return 200, {"tiles": []}
        return 200, {"tiles": service.person_grid(sc, me, target, request.q("kind") or "photos")}


@aira_route("GET", f"{BASE}/people/{ID}/follows")
def person_follows(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        target = service.visible_account(sc, me, _id(request))
        return 200, service.follow_lists(sc, me, target)


@aira_route("POST", f"{BASE}/people/{ID}/close")
def person_close(request: Request) -> tuple[int, dict[str, Any]]:
    """Put a friend on your close friends list."""
    with _open(request) as (sc, me):
        return 200, service.set_close_friend(sc, me, _id(request), True)


@aira_route("DELETE", f"{BASE}/people/{ID}/close")
def person_unclose(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.set_close_friend(sc, me, _id(request), False)


@aira_route("GET", f"{BASE}/close-friends")
def close_friends(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"people": service.close_friends(sc, me)}


@aira_route("POST", f"{BASE}/people/{ID}/follow")
def person_follow(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.follow(sc, me, "account", _id(request), True)


@aira_route("DELETE", f"{BASE}/people/{ID}/follow")
def person_unfollow(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.follow(sc, me, "account", _id(request), False)


@aira_route("POST", f"{BASE}/people/{ID}/block")
def person_block(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        result = service.block(sc, me, _id(request))
    request.audit("social.block", "account", _id(request))
    return 200, result


@aira_route("DELETE", f"{BASE}/people/{ID}/block")
def person_unblock(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.unblock(sc, me, _id(request))


@aira_route("GET", f"{BASE}/blocks")
def blocks(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"blocked": service.blocks(sc, me)}


@aira_route("GET", f"{BASE}/friends")
def friends(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.friends_overview(sc, me)


@aira_route("POST", f"{BASE}/friends/{ID}")
def friend_request(request: Request) -> tuple[int, dict[str, Any]]:
    """Ask to be friends, or accept their request (Today's "Accept" button)."""
    with _open(request) as (sc, me):
        return 200, service.request_friend(sc, me, _id(request))


@aira_route("DELETE", f"{BASE}/friends/{ID}")
def friend_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.remove_friend(sc, me, _id(request))


@aira_route("GET", f"{BASE}/guardian")
def guardian_list(request: Request) -> tuple[int, dict[str, Any]]:
    """The household's children's new friends waiting for a grown-up's yes."""
    with _open(request) as (sc, me):
        return 200, {"requests": service.guardian_requests(sc, me)}


@aira_route("POST", f"{BASE}/guardian/{ID}/{ID}")
def guardian_answer(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"approve": true}`` sends (or accepts) the child's friend request; false says no. Today's "Say yes"."""
    with _open(request) as (sc, me):
        result = service.answer_guardian(
            sc,
            me,
            _id(request),
            _id(request, 2),
            kit.flag(request.payload.get("approve", True)),
        )
    request.audit("social.guardian", "account", _id(request))
    return 200, result


# ---------------------------------------------------------------------------
# feed, posts and comments
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/feed")
def feed(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.feed(
            sc,
            me,
            since=store.since_value(request.q("since")),
            before=store.since_value(request.q("before")),
            limit=request.qint("limit", 30, 1, 100),
        )


@aira_route("GET", f"{BASE}/tags")
def tags(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"posts": service.tag_posts(sc, me, request.q("tag"))}


@aira_route("POST", f"{BASE}/posts")
def post_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, {"post": service.create_post(sc, me, request.payload)}


@aira_route("GET", f"{BASE}/posts/{ID}")
def post_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        post = service.visible_post(sc, me, _id(request))
        return 200, {
            "post": service.post_view(sc, me, post),
            "comments": service.comments(sc, me, post["id"]),
        }


@aira_route("PATCH", f"{BASE}/posts/{ID}")
def post_edit(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"post": service.edit_post(sc, me, _id(request), request.payload)}


@aira_route("DELETE", f"{BASE}/posts/{ID}")
def post_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_post(sc, me, _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/posts/{ID}/react")
def post_react(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        emoji = request.payload.get("emoji")
        return 200, {
            "post": service.react(sc, me, _id(request), str(emoji) if emoji else None)
        }


@aira_route("GET", f"{BASE}/posts/{ID}/comments")
def comments_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"comments": service.comments(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/posts/{ID}/comments")
def comment_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, service.comment(sc, me, _id(request), request.payload)


@aira_route("DELETE", f"{BASE}/comments/{ID}")
def comment_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_comment(sc, me, _id(request))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# stories
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/stories")
def stories(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"people": service.stories(sc, me)}


@aira_route("POST", f"{BASE}/stories")
def story_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, service.create_story(sc, me, request.payload)


@aira_route("POST", f"{BASE}/stories/{ID}/seen")
def story_seen(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.see_story(sc, me, _id(request))
    return 200, {"seen": True}


@aira_route("DELETE", f"{BASE}/stories/{ID}")
def story_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_story(sc, me, _id(request))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# chats
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/chats")
def chats(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.chats(sc, me)


@aira_route("POST", f"{BASE}/chats")
def chat_create(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"person_id"}`` opens (or reopens) a one-to-one chat; ``{"title", "member_ids"}`` makes a group chat."""
    with _open(request) as (sc, me):
        payload = request.payload
        if payload.get("person_id"):
            return 201, {"chat": service.open_direct(sc, me, str(payload["person_id"]))}
        return 201, {"chat": service.create_group_chat(sc, me, payload)}


@aira_route("GET", f"{BASE}/chats/{ID}/messages")
def chat_messages(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.messages(
            sc,
            me,
            _id(request),
            since=store.since_value(request.q("since")),
            limit=request.qint("limit", 80, 1, 200),
        )


@aira_route("POST", f"{BASE}/chats/{ID}/messages")
def chat_send(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        sent = service.send(sc, me, _id(request), request.payload)
        notify_events.chat_message(sc, me, _id(request))  # live pages, and the others are told
        return 201, sent


@aira_route("POST", f"{BASE}/chats/{ID}/read")
def chat_read(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.mark_read(sc, me, _id(request))
        notify_events.chat_changed(sc, _id(request))  # read receipts on the others' open chat
    return 200, {"read": True}


@aira_route("POST", f"{BASE}/chats/{ID}/mute")
def chat_mute(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        muted = bool(request.payload.get("muted", True))
        service.mute(sc, me, _id(request), muted)
    return 200, {"muted": muted}


@aira_route("POST", f"{BASE}/chats/{ID}/leave")
def chat_leave(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.leave(sc, me, _id(request))
    return 200, {"left": True}


@aira_route("POST", f"{BASE}/chats/{ID}/members")
def chat_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "chat": service.add_member(
                sc, me, _id(request), str(request.payload.get("person_id") or "")
            )
        }


@aira_route("DELETE", f"{BASE}/chats/{ID}/members/{ID}")
def chat_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "chat": service.remove_member(sc, me, _id(request), _id(request, 2))
        }


@aira_route("DELETE", f"{BASE}/messages/{ID}")
def message_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_message(sc, me, _id(request))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# groups and pages
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/groups")
def groups(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.groups(sc, me, request.q("q"))


@aira_route("POST", f"{BASE}/groups")
def group_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, {"group": service.create_group(sc, me, request.payload)}


@aira_route("GET", f"{BASE}/groups/{ID}")
def group_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "group": service.group_view(
                sc, me, service.group_row(sc, _id(request)), with_posts=True
            )
        }


@aira_route("PATCH", f"{BASE}/groups/{ID}")
def group_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "group": service.update_group(sc, me, _id(request), request.payload)
        }


@aira_route("DELETE", f"{BASE}/groups/{ID}")
def group_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_group(sc, me, _id(request))
    request.audit("social.group_delete", "group", _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/groups/{ID}/join")
def group_join(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"group": service.join_group(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/groups/{ID}/leave")
def group_leave(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"group": service.leave_group(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/groups/{ID}/requests/{ID}")
def group_answer(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        approve = bool(request.payload.get("approve", True))
        return 200, {
            "group": service.approve_member(
                sc, me, _id(request), _id(request, 2), approve
            )
        }


@aira_route("GET", f"{BASE}/pages")
def pages(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.pages(sc, me, request.q("q"))


@aira_route("POST", f"{BASE}/pages")
def page_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, {"page": service.create_page(sc, me, request.payload)}


@aira_route("GET", f"{BASE}/pages/{ID}")
def page_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "page": service.page_view(
                sc, me, service.page(sc, _id(request)), with_posts=True
            )
        }


@aira_route("PATCH", f"{BASE}/pages/{ID}")
def page_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"page": service.update_page(sc, me, _id(request), request.payload)}


@aira_route("DELETE", f"{BASE}/pages/{ID}")
def page_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_page(sc, me, _id(request))
    request.audit("social.page_delete", "page", _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/pages/{ID}/follow")
def page_follow(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.follow(sc, me, "page", _id(request), True)


@aira_route("DELETE", f"{BASE}/pages/{ID}/follow")
def page_unfollow(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.follow(sc, me, "page", _id(request), False)


# ---------------------------------------------------------------------------
# marketplace (market.py): selling records the sale in the seller's own revenue ledger
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/market")
def market_browse(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, market.browse(
            sc,
            me,
            q=request.q("q"),
            category=request.q("category"),
            max_price=request.q("max_price"),
            sort=request.q("sort") or "newest",
        )


@aira_route("GET", f"{BASE}/market/mine")
def market_mine(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, market.selling(sc, me)


@aira_route("GET", f"{BASE}/market/saved")
def market_saved(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"listings": market.saved(sc, me)}


def _selling_as(
    request: Request,
) -> tuple[str, dict[str, Any] | None]:
    """(the account's currency, the business this person may sell for or None): see market.may_sell_for_business."""
    with closing(_account_db(request)) as connection:
        profile = life.current(request, connection)
        business = kit.business(connection)
    return str(business["currency"]), market.business_for(profile, business)


@aira_route("GET", f"{BASE}/market/setup")
def market_setup(request: Request) -> tuple[int, dict[str, Any]]:
    """What the "Sell something" form offers: currencies, and the business only to someone who runs it."""
    currency, business = _selling_as(request)
    with _open(request) as (sc, me):
        market._seller_only(me)
    return 200, {
        "currency": currency,
        "currencies": list(kit.CURRENCIES),
        "business": None
        if business is None
        else {
            "name": business["name"] or "the business",
            "has_payment_link": bool(business["payment_link"]),
        },
    }


@aira_route("POST", f"{BASE}/market")
def market_create(request: Request) -> tuple[int, dict[str, Any]]:
    """A new listing: the seller's own, or (``for_business``) the business's when they run it."""
    currency, business = _selling_as(request)
    with _open(request) as (sc, me):
        listing = market.create(
            sc, me, request.payload, currency=currency, business=business
        )
    request.audit("social.listing_create", "listing", listing["id"])
    return 201, {"listing": listing}


@aira_route("GET", f"{BASE}/market/{ID}")
def market_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        listing = market.visible(sc, me, _id(request))
        market.seen(sc, me, listing)
        return 200, {"listing": market.view(sc, me, listing)}


@aira_route("PATCH", f"{BASE}/market/{ID}")
def market_update(request: Request) -> tuple[int, dict[str, Any]]:
    _, business = _selling_as(request)
    with _open(request) as (sc, me):
        listing = market.update(
            sc, me, _id(request), request.payload, business=business
        )
    _book_sale(request, listing)
    return 200, {"listing": listing}


def _book_sale(request: Request, listing: dict[str, Any]) -> None:
    """A sale for the business goes in its revenue ledger and activity feed (Business hub); anything else
    (available again, a personal sale, a giveaway, no longer for the business) takes the line back out."""
    with closing(_account_db(request)) as connection:
        if (
            listing["status"] == "sold"
            and listing["for_business"]
            and listing["sold_cents"] > 0
        ):
            kit.record_revenue(
                connection,
                APP,
                source_type="listing",
                source_id=listing["id"],
                amount_cents=listing["sold_cents"],
                currency_code=listing["currency"],
                label=f"Sold: {listing['title']}",
                customer=listing.get("buyer") or "",
            )
            kit.log(
                connection,
                APP,
                "sale",
                f"Sold {listing['title']} on the Marketplace",
                ref_type="listing",
                ref_id=listing["id"],
                amount_cents=listing["sold_cents"],
            )
        else:
            kit.void_revenue(connection, APP, "listing", listing["id"])
        connection.commit()


@aira_route("POST", f"{BASE}/market/{ID}/status")
def market_status(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"status": "available" | "reserved" | "sold", "buyer"?, "sold_cents" | "sold_price"?}``."""
    with _open(request) as (sc, me):
        listing = market.set_status(sc, me, _id(request), request.payload)
    _book_sale(request, listing)
    request.audit(f"social.listing_{listing['status']}", "listing", listing["id"])
    return 200, {"listing": listing}


@aira_route("DELETE", f"{BASE}/market/{ID}")
def market_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        listing = market.delete(sc, me, _id(request))
    with closing(_account_db(request)) as connection:
        public.revoke(connection, APP, "listing", listing["id"])
        if listing["status"] != "sold":
            kit.void_revenue(connection, APP, "listing", listing["id"])
        connection.commit()
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/market/{ID}/save")
def market_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, market.save(sc, me, _id(request), True)


@aira_route("DELETE", f"{BASE}/market/{ID}/save")
def market_unsave(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, market.save(sc, me, _id(request), False)


@aira_route("POST", f"{BASE}/market/{ID}/ask")
def market_ask(request: Request) -> tuple[int, dict[str, Any]]:
    """Message the seller about this listing (``{"text"?}``); answers the chat to open."""
    with _open(request) as (sc, me):
        return 201, {"chat": market.ask_seller(sc, me, _id(request), request.payload)}


@aira_route("GET", f"{BASE}/market/{ID}/share")
def market_share_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        listing = market.mine(sc, me, _id(request))
    with closing(_account_db(request)) as connection:
        return 200, {"link": public.link_for(connection, APP, "listing", listing["id"])}


@aira_route("POST", f"{BASE}/market/{ID}/share")
def market_share(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        listing = market.mine(sc, me, _id(request))
        if listing["audience"] != "public":
            raise PermissionError(
                "Only a listing for everyone can be shared outside Monosage."
            )
    with closing(_account_db(request)) as connection:
        link = public.create_link(
            connection, APP, "listing", listing["id"], title=listing["title"]
        )
    request.audit("social.listing_share", "listing", listing["id"])
    return 201, {"link": link}


@aira_route("DELETE", f"{BASE}/market/{ID}/share")
def market_unshare(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        listing = market.mine(sc, me, _id(request))
    with closing(_account_db(request)) as connection:
        public.revoke(connection, APP, "listing", listing["id"])
    return 200, {"link": None}


@public.page(APP, "listing")
def listing_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    with closing(db.connect()) as sc:
        page = market.public_page(
            sc, str(link.get("ref_id") or ""), str(link.get("token") or "")
        )
    if not (page.get("listing") or {}).get("for_business"):
        page["brand"] = (
            False  # a person's own listing: not the business's name, contacts or payment link
        )
    return page


@public.action(APP, "listing", "interested")
def listing_interested(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    with closing(db.connect()) as sc:
        return 201, market.interested(sc, str(link.get("ref_id") or ""), payload)


# ---------------------------------------------------------------------------
# photos from the device (photos.py): uploaded once, attached by ``media:<id>``, shown by signed addresses
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/photos")
def photo_upload(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"data": base64}``: a JPEG, PNG or WebP up to 8 MB; its metadata (where, when, the camera) is removed."""
    with _open(request) as (sc, me):
        service._active(me)
        service.rate(sc, me, "photo")
        sc.commit()
        return 201, {"photo": photos.save(sc, me, request.payload.get("data"))}


_PHOTO_HEADERS = (
    ("Cache-Control", "private, max-age=21600"),
    ("Content-Disposition", "inline"),
    ("X-Content-Type-Options", "nosniff"),
    ("Content-Security-Policy", "default-src 'none'; sandbox"),
)


@aira_route("GET", f"{BASE}/photos/{ID}")
def photo_get(request: Request) -> Raw:
    """A photo by the signed address a view gave someone allowed to see it (an <img> sends no profile header)."""
    with closing(db.connect()) as sc:
        photos.verify(_id(request), request.q("e"), request.q("s"), sc)
        body, mime = photos.read(sc, _id(request))
    return Raw(200, body, mime, headers=_PHOTO_HEADERS)


@aira_route("GET", f"/api/public/{TOKEN}/social-photo/{ID}", auth="public_link")
def public_photo(request: Request) -> Raw:
    """A photo on a shared profile or listing page: only one that page shows."""
    with closing(request.connect()) as connection:
        link = public.resolve(connection, request.match.group(1))
    if link["app"] != APP or link["kind"] not in ("profile", "listing"):
        raise LookupError("This link is no longer available.")
    photo_id = _id(request, 2)
    ref = f"media:{photo_id}"
    with closing(db.connect()) as sc:
        ref_id = str(link.get("ref_id") or "")
        if link["kind"] == "profile":
            shown = service.public_profile_photo(sc, ref_id, photo_id)
        else:
            page = market.public_page(sc, ref_id)
            listing = store.row(
                sc, "SELECT photos, seller_id FROM so_listings WHERE id = ?", (ref_id,)
            )
            seller = store.row(
                sc,
                "SELECT avatar FROM so_accounts WHERE id = ?",
                ((listing or {}).get("seller_id", ""),),
            )
            shown = bool(page.get("available") and listing) and (
                ref in store.loads((listing or {}).get("photos"), [])
                or (seller or {}).get("avatar") == ref
            )
        if not shown:
            raise LookupError("That photo was not found.")
        body, mime = photos.read(sc, photo_id)
    return Raw(200, body, mime, headers=_PHOTO_HEADERS)


# ---------------------------------------------------------------------------
# more on posts: pins, polls, saved posts and collections, comment likes, memories, Explore
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/posts/{ID}/pin")
def post_pin(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"pinned": true|false}``: keep up to three of your posts at the top of your profile."""
    with _open(request) as (sc, me):
        on = kit.flag(request.payload.get("pinned", True))
        return 200, {"post": service.pin_post(sc, me, _id(request), on)}


@aira_route("POST", f"{BASE}/posts/{ID}/vote")
def post_vote(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"choice": 0..3}``: one vote per person in the post's poll."""
    with _open(request) as (sc, me):
        return 200, {
            "post": service.vote(sc, me, _id(request), request.payload.get("choice"))
        }


@aira_route("POST", f"{BASE}/posts/{ID}/save")
def post_save(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"collection_id"?}`` saves the post (into a collection, or All saved); ``{"saved": false}`` unsaves it."""
    with _open(request) as (sc, me):
        return 200, service.save_post(sc, me, _id(request), request.payload)


@aira_route("POST", f"{BASE}/comments/{ID}/like")
def comment_like(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        on = kit.flag(request.payload.get("liked", True))
        return 200, service.like_comment(sc, me, _id(request), on)


@aira_route("GET", f"{BASE}/saved")
def saved(request: Request) -> tuple[int, dict[str, Any]]:
    """Saved posts (``?collection=<id>``: one collection's), and the collections."""
    with _open(request) as (sc, me):
        return 200, {
            "posts": service.saved_posts(sc, me, request.q("collection")),
            "collections": service.collections(sc, me),
        }


@aira_route("GET", f"{BASE}/collections")
def collections(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"collections": service.collections(sc, me)}


@aira_route("POST", f"{BASE}/collections")
def collection_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, {"collection": service.create_collection(sc, me, request.payload)}


@aira_route("PATCH", f"{BASE}/collections/{ID}")
def collection_rename(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "collection": service.rename_collection(
                sc, me, _id(request), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/collections/{ID}")
def collection_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_collection(sc, me, _id(request))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/memories")
def memories(request: Request) -> tuple[int, dict[str, Any]]:
    """On this day: your posts from earlier years (``?day=MM-DD``, today in your time zone)."""
    with _open(request) as (sc, me):
        return 200, {"posts": service.memories(sc, me, request.q("day"))}


@aira_route("GET", f"{BASE}/explore")
def explore(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.explore(sc, me)


@aira_route("GET", f"{BASE}/explore/search")
def explore_search(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.explore_search(sc, me, request.q("q"))


# ---------------------------------------------------------------------------
# more on stories: who saw them, reactions, replies, and highlights
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/stories/mine")
def stories_mine(request: Request) -> tuple[int, dict[str, Any]]:
    """Your stories of the last year, to choose for a highlight."""
    with _open(request) as (sc, me):
        return 200, {"stories": service.my_stories(sc, me)}


@aira_route("GET", f"{BASE}/stories/{ID}/viewers")
def story_viewers(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"viewers": service.story_viewers(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/stories/{ID}/react")
def story_react(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.react_story(
            sc, me, _id(request), request.payload.get("emoji")
        )


@aira_route("POST", f"{BASE}/stories/{ID}/reply")
def story_reply(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"text"}``: a private message to the story's author."""
    with _open(request) as (sc, me):
        return 201, service.reply_story(sc, me, _id(request), request.payload)


@aira_route("POST", f"{BASE}/highlights")
def highlight_create(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"title", "story_ids"}``: keep these stories on your profile."""
    with _open(request) as (sc, me):
        return 201, {"highlight": service.save_highlight(sc, me, request.payload)}


@aira_route("PATCH", f"{BASE}/highlights/{ID}")
def highlight_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "highlight": service.save_highlight(sc, me, request.payload, _id(request))
        }


@aira_route("DELETE", f"{BASE}/highlights/{ID}")
def highlight_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_highlight(sc, me, _id(request))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# notifications, reports and moderation
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/notifications")
def notifications(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.notifications(sc, me)


@aira_route("POST", f"{BASE}/notifications/{ID}/read")
def notification_read(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"unread": service.read_notification(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/notifications/read")
def notifications_read(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"read": service.read_notifications(sc, me), "unread": 0}


@aira_route("POST", f"{BASE}/reports")
def report(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        result = service.report(sc, me, request.payload)
    request.audit(
        "social.report",
        str(request.payload.get("target_type") or ""),
        str(request.payload.get("target_id") or ""),
    )
    return 201, result


def _moderator(ident: dict[str, Any]) -> str:
    """The household owner moderates on a single-user server. Hosted, moderation is the platform operators'
    (they call :func:`service.reports` / :func:`service.moderate` with no ``home`` from the operator console)."""
    if not ident["moderator"]:
        raise PermissionError(
            "Only the household's owner moderates Social on this computer."
        )
    return ident["home"]


@aira_route("GET", f"{BASE}/moderation")
def moderation(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        home = _moderator(ident)
        status = request.q("status") or "open"
        return 200, {
            "reports": service.reports(sc, status=status, home=home),
            "words": service.blocklist(sc),
        }


@aira_route("POST", f"{BASE}/moderation/{ID}")
def moderation_act(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        home = _moderator(ident)
        me = service.me(sc, ident)
        result = service.moderate(
            sc,
            f"@{me['handle']}",
            _id(request),
            str(request.payload.get("action") or ""),
            home=home,
        )
    request.audit(f"social.moderate_{result['action']}", "report", _id(request))
    return 200, result


@aira_route("PUT", f"{BASE}/moderation/words")
def moderation_words(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        _moderator(ident)
        words = request.payload.get("words")
        if not isinstance(words, list):
            raise ValueError("Send the words as a list.")
        return 200, {"words": service.save_blocklist(sc, words)}


# ---------------------------------------------------------------------------
# the public profile page
# ---------------------------------------------------------------------------


@public.page(APP, "profile")
def profile_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    with closing(db.connect()) as sc:
        return {
            **service.public_profile(
                sc, str(link.get("ref_id") or ""), str(link.get("token") or "")
            ),
            "brand": False,
        }


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    with closing(db.connect()) as sc:
        return service.today_cards(sc, me)


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    with closing(db.connect()) as sc:
        return service.pulse(sc, me, since)
