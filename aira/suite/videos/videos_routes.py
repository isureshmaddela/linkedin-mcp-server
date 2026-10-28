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
from aira.suite.videos import creator, db, deals, media, members, service, store
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
            # the upload form offers "Only for my members" only when people can join
            "members_plan_on": service.adult(me)
            and service.member_plan_on(sc, me["id"]),
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
        data = service.home(sc, me, request.q("category"))
        data["continue"] = (
            [] if request.q("category") else service.continue_watching(sc, me, 8)
        )
        return 200, data


@aira_route("GET", f"{BASE}/continue")
def continue_watching(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"videos": service.continue_watching(sc, me)}


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
        data = service.channel_page(sc, me, _id(request))
        data["support"] = creator.support_for_viewer(sc, me, _id(request))
        data["membership"] = members.for_viewer(sc, me, _id(request))
        return 200, data


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
        return 200, {
            "video": service.video_view(sc, me, video, full=True),
            "support": creator.support_for_viewer(sc, me, video["channel_id"]),
            "membership": members.for_viewer(sc, me, video["channel_id"]),
        }


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
        media.sweep_orphans(sc)
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


@aira_route("POST", f"{BASE}/videos/check")
def video_check(request: Request) -> tuple[int, dict[str, Any]]:
    """The upload form's details checked before the file goes up (400 with the reason, like creating it)."""
    with _open(request) as (sc, me):
        return 200, service.check_video(sc, me, request.payload)


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
        if (
            video["visibility"] == "private"
            or video["rating"] == "mature"
            or video["members_only"]
            or video.get("scheduled")
        ):
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
def studio(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """The creator's own videos (``q`` searches, ``show`` filters; ``format=csv`` for a spreadsheet)."""
    with _open(request) as (sc, me):
        if request.q("format") == "csv":
            return Raw(
                200,
                creator.videos_csv(sc, me, request.q("q"), request.q("show")),
                "text/csv; charset=utf-8",
                filename="your-videos.csv",
            )
        return 200, {
            "videos": service.my_videos(sc, me, request.q("q"), request.q("show")),
            "total": int(
                store.scalar(
                    sc,
                    "SELECT COUNT(*) FROM vi_videos WHERE channel_id = ? AND deleted_at IS NULL",
                    (me["id"],),
                )
                or 0
            ),
            "filters": list(service.MY_FILTERS),
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
            raise PermissionError("Children's videos are not shared outside Monosage.")
        if video["visibility"] == "private":
            raise PermissionError("Make the video public or unlisted first.")
        if video["rating"] == "mature":
            raise PermissionError("Videos rated mature are not shared on public pages.")
        if video.get("members_only"):
            raise PermissionError("Videos for members are not shared on public pages.")
        if service.scheduled(video):
            raise PermissionError("Share the video once it is out.")
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
        if service.locked(sc, me, video):
            return 200, {"ad": None}
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


# The Business hub is the account's: every profile of the household can open it, children included. So only the
# household owner's earnings join it (another adult's earnings are their own, like a member's in the hosted build),
# and its lines never name a member, a supporter or a brand: those stay on the creator's own Earnings page.
GENERIC_LABELS = {
    "creator_tip": ("Support from a viewer (Videos)", "A viewer"),
    "creator_membership": ("Channel membership (Videos)", "A member"),
    "creator_deal": ("Brand deal (Videos)", "A brand"),
}
SOURCE_TABLES = {
    "creator_tip": "vi_tips",
    "creator_membership": "vi_member_payments",
    "creator_deal": "vi_deals",
}


def _owner_channel(connection: Any, sc: Any) -> str:
    """The household owner's channel id ("" when they have none yet)."""
    try:
        found = kit.fetch_one(
            connection,
            "SELECT id, member_ref FROM life_profiles WHERE owner = 1 AND archived_at IS NULL ORDER BY created_at LIMIT 1",
        )
    except Exception:  # noqa: BLE001 - an account without profiles yet
        return ""
    if not found:
        return ""
    channel = store.row(
        sc,
        "SELECT id FROM vi_channels WHERE person_key = ?",
        (service.person_key(found),),
    )
    return channel["id"] if channel else ""


def _tidy_revenue(connection: Any, sc: Any, owner_channel: str) -> None:
    """Lines recorded before these rules: names come out of the labels; another person's earnings stop counting."""
    for source_type, (label, customer) in GENERIC_LABELS.items():
        connection.execute(
            "UPDATE suite_revenue SET label = ?, customer = ? WHERE app = ? AND source_type = ? AND label != ?",
            (label, customer, APP, source_type, label),
        )
    for line in kit.fetch_all(
        connection,
        "SELECT source_type, source_id FROM suite_revenue WHERE app = ? AND voided_at IS NULL AND source_type IN (?, ?, ?)",
        (APP, *SOURCE_TABLES),
    ):
        table = SOURCE_TABLES[line["source_type"]]
        found = store.row(
            sc,
            f"SELECT channel_id FROM {table} WHERE id = ?",  # noqa: S608 - our own table names
            (line["source_id"],),
        )
        if not found or found["channel_id"] != owner_channel:
            kit.void_revenue(connection, APP, line["source_type"], line["source_id"])


def _sync_revenue(request: Request, sc: Any, home: str) -> None:
    """On a person's own computer the household owner's creator payouts are money the account received: they join
    the Business hub's revenue. (Hosted, a member's payout is their own, not the organization's.)"""
    if not home.startswith("local:"):
        return
    with closing(request.connect()) as connection:
        record_payouts(connection, sc, home)


def record_payouts(connection: Any, sc: Any, home: str) -> int:
    kit.ensure_core(connection)
    owner_channel = _owner_channel(connection, sc)
    _tidy_revenue(connection, sc, owner_channel)
    paid = []
    for item in ads.paid_payouts(sc, home):
        if item["channel_id"] != owner_channel:
            kit.void_revenue(connection, APP, "creator_payout", item["id"])
            continue
        paid.append(item)
        kit.record_revenue(
            connection,
            APP,
            source_type="creator_payout",
            source_id=item["id"],
            amount_cents=int(item["amount_micros"]) // CENT,
            currency_code="USD",
            label="Creator payout from Videos",
            customer="Monosage Ads",
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
# analytics and support from viewers (aira/suite/videos/creator.py)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/analytics")
def analytics(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as (sc, me):
        days = request.q("days") or "28"
        if request.q("format") == "csv":
            return Raw(
                200,
                creator.analytics_csv(sc, me, days),
                "text/csv; charset=utf-8",
                filename=f"videos-analytics-{creator._period(days)}-days.csv",
            )
        return 200, creator.analytics(sc, me, days)


@aira_route("GET", f"{BASE}/support")
def support_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {
            "settings": creator.support_settings(sc, me),
            **creator.received_tips(sc, me, request.q("status")),
        }


@aira_route("PUT", f"{BASE}/support")
def support_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        settings = creator.save_support(sc, me, request.payload)
    request.audit(
        "videos.support_settings", "channel", me["id"], {"enabled": settings["enabled"]}
    )
    return 200, {"settings": settings}


def _record_tip(request: Request, home: str, tip: dict[str, Any]) -> None:
    """Support that arrived is the household owner's money (see :data:`GENERIC_LABELS`)."""
    _record_money(
        request,
        home,
        "creator_tip",
        tip["id"],
        {"amount_cents": tip["amount_cents"], "currency": tip["currency"]}
        if tip["status"] == "received"
        else None,
    )


@aira_route("POST", f"{BASE}/support/{ID}")
def support_decide(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        tip = creator.decide_tip(
            sc, me, _id(request), str(request.payload.get("action") or "")
        )
    _record_tip(request, me["home"], tip)
    request.audit(
        f"videos.support_{tip['status']}",
        "tip",
        tip["id"],
        {"amount_cents": tip["amount_cents"]},
    )
    return 200, {"tip": tip}


@aira_route("POST", f"{BASE}/channels/{ID}/support")
def support_pledge(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        result = creator.pledge(sc, me, _id(request), request.payload)
    request.audit(
        "videos.support_pledge",
        "tip",
        result["tip"]["id"],
        {"amount_cents": result["tip"]["amount_cents"]},
    )
    return 201, result


@aira_route("GET", f"{BASE}/support/given")
def support_given(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"tips": creator.my_tips(sc, me)}


@aira_route("DELETE", f"{BASE}/support/given/{ID}")
def support_withdraw(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"tip": creator.cancel_tip(sc, me, _id(request))}


# ---------------------------------------------------------------------------
# all income, memberships and brand deals (creator.income, members.py, deals.py)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/income")
def income(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """Every way the channel earned (ads, support, memberships, brand deals) by month, a log, and this month's
    outlook; ``format=csv`` for the log as a spreadsheet."""
    with _open(request) as (sc, me):
        _sync_revenue(request, sc, me["home"])
        if request.q("format") == "csv":
            return Raw(
                200,
                creator.income_csv(sc, me),
                "text/csv; charset=utf-8",
                filename="videos-income.csv",
            )
        return 200, creator.income(sc, me)


def _record_money(
    request: Request,
    home: str,
    source_type: str,
    item_id: str,
    money: dict[str, Any] | None,
) -> None:
    """On a person's own computer money the household owner received as a creator joins the Business hub's
    revenue, under a label that names nobody (``money`` has amount_cents, currency and, optionally, ``at``; None
    takes it out again). Another profile's earnings stay their own; hosted, a member's earnings are their own, not
    the organization's (like support and creator payouts)."""
    if not home.startswith("local:"):
        return
    with closing(request.connect()) as connection:
        owner = False
        if money:
            try:
                owner = bool(life.current(request, connection).get("owner"))
            except Exception:  # noqa: BLE001 - no profile: not the owner
                owner = False
        if money and owner:
            label, customer = GENERIC_LABELS[source_type]
            kit.record_revenue(
                connection,
                APP,
                source_type=source_type,
                source_id=item_id,
                amount_cents=int(money["amount_cents"]),
                currency_code=money["currency"],
                label=label,
                customer=customer,
                at=money.get("at") or "",
            )
            if money.get("at"):
                # recording a line again keeps its first date: a deal whose paid day was changed moves with it
                connection.execute(
                    "UPDATE suite_revenue SET at = ? WHERE app = ? AND source_type = ? AND source_id = ?",
                    (money["at"], APP, source_type, item_id),
                )
        else:
            kit.void_revenue(connection, APP, source_type, item_id)
        connection.commit()


@aira_route("GET", f"{BASE}/memberships")
def memberships(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, members.creator_view(sc, me, request.q("status"))


@aira_route("PUT", f"{BASE}/memberships")
def memberships_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        plan = members.save_plan(sc, me, request.payload)
    request.audit(
        "videos.membership_plan",
        "channel",
        me["id"],
        {"enabled": plan["enabled"], "price_cents": plan["price_cents"]},
    )
    return 200, {"plan": plan}


@aira_route("GET", f"{BASE}/memberships/mine")
def memberships_mine(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"memberships": members.my_memberships(sc, me)}


@aira_route("POST", f"{BASE}/memberships/{ID}")
def memberships_decide(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        payment = members.decide(
            sc, me, _id(request), str(request.payload.get("action") or "")
        )
    _record_money(
        request,
        me["home"],
        "creator_membership",
        payment["id"],
        {"amount_cents": payment["amount_cents"], "currency": payment["currency"]}
        if payment["status"] == "received"
        else None,
    )
    request.audit(
        f"videos.membership_{payment['status']}",
        "membership_payment",
        payment["id"],
        {"amount_cents": payment["amount_cents"]},
    )
    return 200, {"payment": payment}


@aira_route("DELETE", f"{BASE}/memberships/payments/{ID}")
def memberships_withdraw(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        return 200, {"payment": members.withdraw(sc, me, _id(request))}


@aira_route("POST", f"{BASE}/channels/{ID}/join")
def channel_join(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        result = members.join(sc, me, _id(request), request.payload)
    request.audit(
        "videos.membership_join",
        "membership_payment",
        result["payment"]["id"],
        {"amount_cents": result["payment"]["amount_cents"]},
    )
    return 201, result


@aira_route("GET", f"{BASE}/deals")
def deals_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as (sc, me):
        if request.q("format") == "csv":
            return Raw(
                200,
                deals.as_csv(sc, me, request.q("status"), request.q("q")),
                "text/csv; charset=utf-8",
                filename="brand-deals.csv",
            )
        return 200, deals.listing(sc, me, request.q("status"), request.q("q"))


def _hub_moment(value: str | None) -> str:
    """A Videos stamp (with microseconds) the way the Business hub writes its moments (to the second, with Z)."""
    return str(value or "")[:19] + "Z" if value else ""


def _deal_money(request: Request, home: str, deal: dict[str, Any]) -> None:
    """A paid deal is in the account's revenue; any other status (or a deleted deal) is not."""
    _record_money(
        request,
        home,
        "creator_deal",
        deal["id"],
        {
            "amount_cents": deal["amount_cents"],
            "currency": deal["currency"],
            "at": _hub_moment(deal["paid_at"]),
        }
        if deal["status"] == "paid"
        else None,
    )


@aira_route("POST", f"{BASE}/deals")
def deals_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        deal = deals.create(sc, me, request.payload)
    if deal["status"] == "paid":
        _deal_money(request, me["home"], deal)
    request.audit("videos.deal_create", "deal", deal["id"])
    return 201, {"deal": deal}


@aira_route("PATCH", f"{BASE}/deals/{ID}")
def deals_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        deal, before = deals.update(sc, me, _id(request), request.payload)
    if "paid" in (before, deal["status"]):
        _deal_money(request, me["home"], deal)
    request.audit("videos.deal_update", "deal", deal["id"], {"status": deal["status"]})
    return 200, {"deal": deal}


@aira_route("DELETE", f"{BASE}/deals/{ID}")
def deals_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, me):
        found = deals.delete(sc, me, _id(request))
    if found["status"] == "paid":
        _record_money(request, me["home"], "creator_deal", found["id"], None)
    request.audit("videos.deal_delete", "deal", found["id"])
    return 200, {"deleted": True}


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


def _money_lines(sc: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    """For a creator: members waiting for their payment to be confirmed, brand deals due."""
    lines: list[dict[str, Any]] = []
    for deal in deals.due_lines(sc, me):
        today = deal["due_on"] == store.day()
        lines.append(
            {
                "text": f"Brand deal with {deal['brand']} is due {'today' if today else 'and late'}",
                "sub": "Earnings > Brand deals",
            }
        )
    joining = members.waiting_count(sc, me["id"])
    if joining:
        lines.append(
            {
                "text": "A new member is paying to join your channel"
                if joining == 1
                else f"{joining} members are paying to join your channel",
                "sub": "Mark it received under Earnings > Memberships when the money arrives.",
            }
        )
    return lines


def _renew_lines(sc: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    """For a member: memberships that end in the next few days."""
    return [
        {
            "text": f"Your membership of {item['name']} ends on {str(item['paid_until'])[:10]}",
            "sub": "Renew it on their channel to keep watching their videos for members.",
        }
        for item in members.ending_soon(sc, me)
    ]


@life.today(APP)
def _today(connection: Any, profile: dict[str, Any]) -> list[dict[str, Any]]:
    with closing(db.connect()) as sc:
        me = _person_channel(sc, profile)
        if not me or me.get("suspended_at"):
            return []
        me["ratings"] = service.allowed_ratings(me, profile.get("prefs"))
        lines = service.today_lines(sc, me)
        started = service.continue_watching(sc, me, 1)
        if started:
            lines.insert(
                0,
                {
                    "text": f"Carry on watching: {started[0]['title']}",
                    "sub": f"You stopped at {service.clock(started[0]['position'])}",
                },
            )
        if service.adult(me):
            lines[:0] = _money_lines(sc, me)
            lines.extend(_renew_lines(sc, me))
        waiting = creator.waiting_count(sc, me["id"]) if service.adult(me) else 0
        if waiting:
            lines.insert(
                0,
                {
                    "text": "Support is on its way to you"
                    if waiting == 1
                    else f"{waiting} supporters are sending you money",
                    "sub": "Mark it received on the Earnings page when it arrives.",
                },
            )
        if me.get("monetized"):
            data = ads.earnings(sc, me)
            lines.insert(
                0,
                {
                    "text": f"Earned today: {ads.dollars(data['today_micros'])}",
                    "sub": f"This month: {ads.dollars(data['month_micros'])} (estimated)",
                },
            )
            payable = int(data["balances"]["payable_micros"])
            if payable >= int(data["settings"]["min_payout_micros"]) and not any(
                item["status"] == "requested" for item in data["payouts"]
            ):
                lines.insert(
                    1,
                    {
                        "text": f"{ads.dollars(payable)} is ready to be paid to you",
                        "sub": "Ask to be paid on the Earnings page.",
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
        result["orphan_files"] = media.sweep_orphans(sc)
        home = str(kit.get_setting(connection, APP, "home", "") or "")
        if home.startswith("local:"):
            result["payouts"] = record_payouts(connection, sc, home)
    return result
