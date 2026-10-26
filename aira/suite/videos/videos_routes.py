"""Routes for Videos (``/api/videos/*``): watching, uploading, the creator's studio and earnings, moderation,
copyright notices, the ads shown on videos, the public video page, Today and pulse.

Every route but the media and click routes starts from the person using Aira (:func:`aira.suite.life.current`, in
the account's database) and then works in the platform-wide Videos database (:mod:`aira.suite.videos.db`). A
``<video>`` or ``<img>`` cannot send the person's headers, so media addresses are signed instead
(:func:`aira.suite.videos.media.signed_url`). Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, life, public
from aira.suite.ads import service as ads
from aira.suite.ads.store import CENT
from aira.suite.videos import db, media, service, store
from aira.suite.videos.store import APP

BASE = "/api/videos"
ID = "([0-9a-f]{32})"
TOKEN = f"({public.TOKEN_PATTERN})"


@contextmanager
def _identity(request: Request) -> Iterator[tuple[Any, dict[str, Any]]]:
    """(videos connection, who is asking): the person comes from the account's database."""
    connection = request.connect()
    try:
        ident = service.identity(request, connection)
        if kit.get_setting(connection, APP, "home", "") != ident["home"]:
            kit.set_setting(
                connection, APP, "home", ident["home"]
            )  # the background task's account
            connection.commit()
    finally:
        connection.close()
    with closing(db.connect()) as sc:
        yield sc, ident


@contextmanager
def _open(request: Request) -> Iterator[tuple[Any, dict[str, Any]]]:
    """(videos connection, the person's channel with the ratings they may watch)."""
    with _identity(request) as (sc, ident):
        yield sc, service.viewer(sc, ident)


def _id(request: Request, group: int = 1) -> str:
    return request.match.group(group)


def _flag(request: Request, default: bool = True) -> bool:
    value = request.payload.get("on", default)
    return kit.flag(value) if not isinstance(value, bool) else value


# ---------------------------------------------------------------------------
# me
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/me")
def me_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        me = service.viewer(sc, ident)
        return 200, {
            "channel": {**me, "ratings": list(me["ratings"]), "person_key": None},
            "moderator": ident["owner"],
            "operator": ident["operator"],
            "hosted": ident["hosted"],
            "unread": service.unread(sc, me),
            "categories": list(service.CATEGORIES),
            "genres": list(service.GENRES),
            "kinds": list(service.KINDS),
            "report_reasons": list(service.REPORT_REASONS),
            "limits": {
                "chunk_bytes": media.CHUNK_BYTES,
                "video_bytes": media.max_bytes("video"),
                "image_bytes": media.max_bytes("image"),
                "captions_bytes": media.max_bytes("captions"),
                "reel_seconds": service.REEL_SECONDS,
            },
            "playback": media.PLAYBACK,
        }


@aira_route("PATCH", f"{BASE}/me")
def me_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        updated = service.update_channel(sc, me, request.payload)
        return 200, {"channel": {**updated, "person_key": None}}


# ---------------------------------------------------------------------------
# finding and watching
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/home")
def home(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.home(sc, me, request.q("category"))


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.search(sc, me, request.q("q"), request.q("kind"))


@aira_route("GET", f"{BASE}/reels")
def reels(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        start = request.q("start")
        return 200, service.reels(sc, me, start if store.HEX.fullmatch(start) else "")


@aira_route("GET", f"{BASE}/films")
def films(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.films(sc, me, request.q("genre"))


@aira_route("GET", f"{BASE}/channels/{ID}")
def channel_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.channel_page(sc, me, _id(request))


@aira_route("POST", f"{BASE}/channels/{ID}/subscribe")
def channel_subscribe(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.subscribe(sc, me, _id(request), _flag(request))


@aira_route("GET", f"{BASE}/subscriptions")
def subscriptions(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"channels": service.subscriptions(sc, me)}


@aira_route("GET", f"{BASE}/videos/{ID}")
def video_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        video = service.visible_video(sc, me, _id(request))
        return 200, {"video": service.video_view(sc, me, video, full=True)}


@aira_route("POST", f"{BASE}/videos/{ID}/progress")
def video_progress(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.progress(sc, me, _id(request), request.payload)


@aira_route("POST", f"{BASE}/videos/{ID}/like")
def video_like(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.like(sc, me, _id(request), _flag(request))


@aira_route("POST", f"{BASE}/videos/{ID}/later")
def video_later(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, service.later(sc, me, _id(request), _flag(request))


@aira_route("GET", f"{BASE}/videos/{ID}/comments")
def comments_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"comments": service.comments(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/videos/{ID}/comments")
def comment_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, service.comment(sc, me, _id(request), request.payload)


@aira_route("DELETE", f"{BASE}/comments/{ID}")
def comment_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_comment(sc, me, _id(request))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/history")
def history(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"videos": service.history(sc, me)}


@aira_route("DELETE", f"{BASE}/history")
def history_clear(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.clear_history(sc, me)
    return 200, {"cleared": True}


@aira_route("DELETE", f"{BASE}/history/{ID}")
def history_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.clear_history(sc, me, _id(request))
    return 200, {"cleared": True}


@aira_route("GET", f"{BASE}/later")
def later_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"videos": service.later_list(sc, me)}


@aira_route("GET", f"{BASE}/playlists")
def playlists(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"playlists": service.playlists(sc, me)}


@aira_route("POST", f"{BASE}/playlists")
def playlist_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, {"playlist": service.create_playlist(sc, me, request.payload)}


@aira_route("GET", f"{BASE}/playlists/{ID}")
def playlist_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"playlist": service.playlist(sc, me, _id(request))}


@aira_route("PATCH", f"{BASE}/playlists/{ID}")
def playlist_rename(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "playlist": service.rename_playlist(sc, me, _id(request), request.payload)
        }


@aira_route("DELETE", f"{BASE}/playlists/{ID}")
def playlist_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_playlist(sc, me, _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/playlists/{ID}/videos/{ID}")
def playlist_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "playlist": service.playlist_item(
                sc, me, _id(request), _id(request, 2), True
            )
        }


@aira_route("DELETE", f"{BASE}/playlists/{ID}/videos/{ID}")
def playlist_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "playlist": service.playlist_item(
                sc, me, _id(request), _id(request, 2), False
            )
        }


# ---------------------------------------------------------------------------
# uploading (chunked, resumable) and the studio
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/uploads")
def upload_start(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service._active(me)
        payload = request.payload
        upload = media.start(
            sc,
            me["id"],
            me["home"],
            str(payload.get("kind") or ""),
            str(payload.get("filename") or ""),
            payload.get("size"),
        )
    return 201, {"upload": upload}


@aira_route("GET", f"{BASE}/uploads/{ID}")
def upload_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"upload": media.upload(sc, _id(request), me["id"])}


@aira_route("POST", f"{BASE}/uploads/{ID}/chunk")
def upload_chunk(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        payload = request.payload
        return 200, {
            "upload": media.append(
                sc, _id(request), me["id"], payload.get("offset"), payload.get("data")
            )
        }


@aira_route("POST", f"{BASE}/uploads/{ID}/complete")
def upload_complete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 201, {"file": media.complete(sc, _id(request), me["id"])}


@aira_route("DELETE", f"{BASE}/uploads/{ID}")
def upload_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        media.cancel(sc, _id(request), me["id"])
    return 200, {"cancelled": True}


@aira_route("POST", f"{BASE}/videos")
def video_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        video = service.create_video(sc, me, request.payload)
    request.audit("videos.upload", "video", video["id"])
    return 201, {"video": video}


@aira_route("PATCH", f"{BASE}/videos/{ID}")
def video_edit(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        video = service.edit_video(sc, me, _id(request), request.payload)
        if video["visibility"] == "private" or video["rating"] == "mature":
            with closing(request.connect()) as connection:
                public.revoke(connection, APP, "video", video["id"])
    return 200, {"video": video}


@aira_route("DELETE", f"{BASE}/videos/{ID}")
def video_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.delete_video(sc, me, _id(request))
    with closing(request.connect()) as connection:
        public.revoke(connection, APP, "video", _id(request))
    request.audit("videos.delete", "video", _id(request))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/studio")
def studio(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "videos": service.my_videos(sc, me),
            "notices": service.my_notices(sc, me),
            "strikes": service.strikes(sc, me["id"]),
            "subscribers": service.subscriber_count(sc, me["id"]),
        }


@aira_route("GET", f"{BASE}/videos/{ID}/link")
def video_link_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.own_video(sc, me, _id(request))
    with closing(request.connect()) as connection:
        return 200, {"link": public.link_for(connection, APP, "video", _id(request))}


@aira_route("POST", f"{BASE}/videos/{ID}/link")
def video_link_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        video = service.own_video(sc, me, _id(request))
        if me["age_group"] == "child":
            raise PermissionError("Children's videos are not shared outside Aira.")
        if video["visibility"] == "private":
            raise PermissionError("Make the video public or unlisted first.")
        if video["rating"] == "mature":
            raise PermissionError("Videos rated mature are not shared on public pages.")
        if service.blocked_reason(sc, video):
            raise PermissionError("This video cannot be shared right now.")
    with closing(request.connect()) as connection:
        link = public.create_link(
            connection, APP, "video", video["id"], title=video["title"]
        )
    request.audit("videos.share", "video", video["id"])
    return 201, {"link": link}


@aira_route("DELETE", f"{BASE}/videos/{ID}/link")
def video_link_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        service.own_video(sc, me, _id(request))
    with closing(request.connect()) as connection:
        public.revoke(connection, APP, "video", _id(request))
    return 200, {"link": None}


@aira_route("GET", f"{BASE}/media/{ID}")
def media_get(request: Request) -> Raw:
    """A video, picture or captions file by its signed address, in HTTP ranges (seeking works)."""
    with closing(db.connect()) as sc:
        media.verify(sc, _id(request), request.q("e"), request.q("s"))
        found = media.file_row(sc, _id(request))
    status, body, headers = media.read_range(found, request.header("Range"))
    return Raw(status, body, found["mime"], headers=headers)


# ---------------------------------------------------------------------------
# notifications, reports, copyright
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
        "videos.report",
        str(request.payload.get("target_type") or ""),
        str(request.payload.get("target_id") or ""),
    )
    return 201, result


@aira_route("POST", f"{BASE}/copyright", auth="none")
def copyright_notice(request: Request) -> tuple[int, dict[str, Any]]:
    """Anyone (no account) asks for a video to come down because it copies their work."""
    with closing(db.connect()) as sc:
        return 201, service.file_notice(sc, request.payload, request.client)


@aira_route("GET", f"{BASE}/notices")
def my_notices(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "notices": service.my_notices(sc, me),
            "strikes": service.strikes(sc, me["id"]),
        }


@aira_route("POST", f"{BASE}/notices/{ID}/counter")
def counter_notice(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        notice = service.counter_notice(sc, me, _id(request), request.payload)
    request.audit("videos.counter_notice", "notice", _id(request))
    return 200, {"notice": notice}


# ---------------------------------------------------------------------------
# moderation on this computer (hosted, the platform's operators moderate from the console)
# ---------------------------------------------------------------------------


def _moderator(ident: dict[str, Any]) -> str | None:
    """The household owner moderates reports about their household; the computer's owner (its first account)
    moderates every report and handles copyright notices. Hosted, both are the platform's operators'."""
    if not ident["owner"]:
        raise PermissionError(
            "Only the household's owner moderates Videos on this computer."
        )
    return None if ident["operator"] else ident["home"]


def _operator(ident: dict[str, Any]) -> None:
    if not ident["operator"]:
        raise PermissionError(
            "Only the owner of this computer's first account handles this."
        )


@aira_route("GET", f"{BASE}/moderation")
def moderation(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        home = _moderator(ident)
        return 200, {
            "reports": service.reports(
                sc, status=request.q("status") or "open", home=home
            ),
            "everyone": home is None,
            "notices": service.notices(sc) if home is None else [],
        }


@aira_route("POST", f"{BASE}/moderation/{ID}")
def moderation_act(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        home = _moderator(ident)
        me = service.me(sc, ident)
        result = service.moderate(
            sc,
            f"owner:{me['name']}",
            _id(request),
            str(request.payload.get("action") or ""),
            home=home,
        )
    request.audit(f"videos.moderate_{result['action']}", "report", _id(request))
    return 200, result


@aira_route("POST", f"{BASE}/moderation/notices/{ID}")
def moderation_notice(request: Request) -> tuple[int, dict[str, Any]]:
    with _identity(request) as (sc, ident):
        _operator(ident)
        me = service.me(sc, ident)
        notice = service.decide_notice(
            sc,
            _id(request),
            str(request.payload.get("action") or ""),
            f"owner:{me['name']}",
            str(request.payload.get("note") or ""),
        )
    request.audit(f"videos.copyright_{notice['status']}", "notice", _id(request))
    return 200, {"notice": notice}


# ---------------------------------------------------------------------------
# ads on videos (served to viewers; the advertiser's side is /api/ads)
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/ads/serve")
def ads_serve(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        video = service.visible_video(
            sc, me, str(request.payload.get("video_id") or "")
        )
        return 200, {
            "ad": ads.serve(sc, me, video, str(request.payload.get("slot") or ""))
        }


@aira_route("POST", f"{BASE}/ads/{ID}/seen")
def ads_seen(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, ads.seen(sc, me, _id(request))


@aira_route("GET", f"{BASE}/ads/go/{ID}")
def ads_go(request: Request) -> Raw:
    """The ad's signed click address: count the click, then send the viewer to the advertiser's page."""
    with closing(db.connect()) as sc:
        target = ads.click(sc, _id(request), request.q("s"))
    return Raw(302, b"", "text/plain; charset=utf-8", headers=(("Location", target),))


# ---------------------------------------------------------------------------
# earnings (the creator's side of ads)
# ---------------------------------------------------------------------------


def _sync_revenue(request: Request, sc: Any, home: str) -> None:
    """On a person's own computer a creator payout is money the account received: it joins the Business hub's
    revenue. (Hosted, a member's payout is their own, not the organization's.)"""
    if not home.startswith("local:"):
        return
    with closing(request.connect()) as connection:
        record_payouts(connection, sc, home)


def record_payouts(connection: Any, sc: Any, home: str) -> int:
    paid = ads.paid_payouts(sc, home)
    for item in paid:
        kit.record_revenue(
            connection,
            APP,
            source_type="creator_payout",
            source_id=item["id"],
            amount_cents=int(item["amount_micros"]) // CENT,
            currency_code="USD",
            label="Creator payout from Videos",
            customer="Aira Ads",
            at=str(item["decided_at"] or "")[:19] + "Z" if item["decided_at"] else "",
        )
    connection.commit()
    return len(paid)


@aira_route("GET", f"{BASE}/earnings")
def earnings(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        _sync_revenue(request, sc, me["home"])
        return 200, ads.earnings(sc, me)


@aira_route("POST", f"{BASE}/earnings/terms")
def earnings_terms(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        ads.accept_terms(sc, me)
        request.audit("videos.creator_terms", "channel", me["id"])
        return 200, ads.earnings(sc, service.viewer_of(service.channel(sc, me["id"])))


@aira_route("POST", f"{BASE}/earnings/monetize")
def earnings_monetize(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        ads.set_monetized(sc, me, _flag(request))
        request.audit("videos.monetize", "channel", me["id"], {"on": _flag(request)})
        return 200, ads.earnings(sc, service.viewer_of(service.channel(sc, me["id"])))


@aira_route("POST", f"{BASE}/earnings/payouts")
def earnings_payout(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        payout = ads.request_payout(sc, me, request.payload)
    request.audit(
        "videos.payout_request",
        "payout",
        payout["id"],
        {"amount_micros": payout["amount_micros"]},
    )
    return 201, {"payout": payout}


# ---------------------------------------------------------------------------
# the public video page (anyone with the link watches, no account)
# ---------------------------------------------------------------------------


@public.page(APP, "video")
def video_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    with closing(db.connect()) as sc:
        return service.public_video(
            sc, str(link.get("ref_id") or ""), str(link.get("token") or "")
        )


@public.action(APP, "video", "watched")
def video_page_watched(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """A visitor watched long enough: count their view (once a day per visitor)."""
    with closing(db.connect()) as sc:
        try:
            video = service._video_row(sc, str(link.get("ref_id") or ""))
        except LookupError:
            return 404, {"error": "This video is no longer shared."}
        watched = service._number(
            payload.get("watched"), "the time watched", 0, 24 * 3600
        )
        need = min(
            service.VIEW_SECONDS, service.VIEW_SHARE * float(video["duration"] or 100)
        )
        counted = False
        if watched >= need and not service.blocked_reason(sc, video):
            counted = service.count_view(
                sc,
                video,
                service.visitor_key(
                    str(visitor.get("client") or ""),
                    str(visitor.get("user_agent") or ""),
                ),
            )
            sc.commit()
        return 200, {"counted": counted}


@aira_route("GET", f"/api/public/{TOKEN}/media/{ID}", auth="public_link")
def public_media(request: Request) -> Raw:
    """The shared video's own files (the video, its picture, its captions) for a visitor of its public link."""
    with closing(request.connect()) as connection:
        link = public.resolve(connection, request.match.group(1))
    if link["app"] != APP or link["kind"] != "video":
        raise LookupError("This link is no longer available.")
    with closing(db.connect()) as sc:
        page = service.public_video(sc, str(link["ref_id"]), request.match.group(1))
        if not page.get("available"):
            raise LookupError("This video is not available.")
        video = service._video_row(sc, str(link["ref_id"]))
        file_id = _id(request, 2)
        if file_id not in (
            video["file_id"],
            video["thumb_file_id"],
            video["captions_file_id"],
        ):
            raise LookupError("That file was not found.")
        found = media.file_row(sc, file_id)
    status, body, headers = media.read_range(found, request.header("Range"))
    return Raw(status, body, found["mime"], headers=headers)


# ---------------------------------------------------------------------------
# Today, pulse and background work
# ---------------------------------------------------------------------------


def _person_channel(sc: Any, profile: dict[str, Any]) -> dict[str, Any] | None:
    found = store.row(
        sc,
        "SELECT * FROM vi_channels WHERE person_key = ?",
        (service.person_key(profile),),
    )
    return service.viewer_of(service._channel(found) or {}) if found else None


@life.today(APP)
def _today(connection: Any, profile: dict[str, Any]) -> list[dict[str, Any]]:
    with closing(db.connect()) as sc:
        me = _person_channel(sc, profile)
        if not me or me.get("suspended_at"):
            return []
        me["ratings"] = service.allowed_ratings(me, profile.get("prefs"))
        lines = service.today_lines(sc, me)
        if me.get("monetized"):
            data = ads.earnings(sc, me)
            lines.insert(
                0,
                {
                    "text": f"Earned today: {ads.dollars(data['today_micros'])}",
                    "sub": f"This month: {ads.dollars(data['month_micros'])} (estimated)",
                },
            )
        if not lines:
            return []
        return [
            life.card(
                APP, "Videos", lines=lines, empty="Nothing new from your channels."
            )
        ]


@life.pulse(APP)
def _pulse(connection: Any, profile: dict[str, Any], since: str) -> dict[str, Any]:
    with closing(db.connect()) as sc:
        found = store.row(
            sc,
            "SELECT id FROM vi_channels WHERE person_key = ?",
            (service.person_key(profile),),
        )
        if not found:
            return {"badge": 0, "alerts": []}
        return {"badge": min(99, service.unread(sc, found)), "alerts": []}


@background.task(APP)
def _tick(connection: Any) -> dict[str, Any]:
    """Close finished ad months and end campaigns past their dates (platform-wide, idempotent); on this computer,
    bring creator payouts into the account's revenue."""
    with closing(db.connect()) as sc:
        result = ads.tick(sc)
        home = str(kit.get_setting(connection, APP, "home", "") or "")
        if home.startswith("local:"):
            result["payouts"] = record_payouts(connection, sc, home)
    return result
