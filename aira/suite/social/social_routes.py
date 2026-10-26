"""Routes for Social (``/api/social/*``), its public profile page and its Today card and pulse.

Every route starts from the person using Aira (:func:`aira.suite.life.current`, in the account's database) and
then works in the platform-wide social database (:mod:`aira.suite.social.db`). Path parameters are ids only;
handles, tags and searches go in the query. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any

from aira.airaapi.http import Request, aira_route
from aira.suite import life, public
from aira.suite.social import db, service, store
from aira.suite.social.store import APP

BASE = "/api/social"
ID = "([0-9a-f]{32})"


@contextmanager
def _identity(request: Request) -> Iterator[tuple[Any, dict[str, Any]]]:
    """(social connection, who is asking): the person comes from the account's database."""
    connection = request.connect()
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
            "account": me,
            "moderator": ident["moderator"],
            "badge": {
                "messages": service.unread_messages(sc, me),
                "notifications": service.unread_notifications(sc, me),
            },
        }


@aira_route("POST", f"{BASE}/me")
def me_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        account = service.create_account(sc, ident, request.payload)
    return 201, {"account": account}


@aira_route("PATCH", f"{BASE}/me")
def me_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"account": service.update_account(sc, me, request.payload)}


@aira_route("GET", f"{BASE}/me/share")
def me_share_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (_sc, me):
        with closing(request.connect()) as connection:
            return 200, {"link": public.link_for(connection, APP, "profile", me["id"])}


@aira_route("POST", f"{BASE}/me/share")
def me_share(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (_sc, me):
        if me["age_group"] in ("child", "teen"):
            raise PermissionError(
                "Young people's profiles are not shared outside Aira."
            )
        if me["visibility"] != "public":
            raise PermissionError("Make your profile public first.")
        with closing(request.connect()) as connection:
            link = public.create_link(
                connection, APP, "profile", me["id"], title=me["display_name"]
            )
    request.audit("social.profile_share", "account", me["id"])
    return 201, {"link": link}


@aira_route("DELETE", f"{BASE}/me/share")
def me_unshare(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (_sc, me):
        with closing(request.connect()) as connection:
            public.revoke(connection, APP, "profile", me["id"])
    return 200, {"link": None}


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
            "person": view,
            "posts": service.person_posts(sc, me, target) if full else [],
        }


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
        return 201, service.send(sc, me, _id(request), request.payload)


@aira_route("POST", f"{BASE}/chats/{ID}/read")
def chat_read(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.mark_read(sc, me, _id(request))
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


@aira_route("POST", f"{BASE}/pages/{ID}/follow")
def page_follow(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.follow(sc, me, "page", _id(request), True)


@aira_route("DELETE", f"{BASE}/pages/{ID}/follow")
def page_unfollow(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.follow(sc, me, "page", _id(request), False)


# ---------------------------------------------------------------------------
# notifications, reports and moderation
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/notifications")
def notifications(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.notifications(sc, me)


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
        return service.public_profile(sc, str(link.get("ref_id") or ""))


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
