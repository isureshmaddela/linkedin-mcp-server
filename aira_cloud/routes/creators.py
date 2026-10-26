"""Videos and Ads on the hosted platform: moderation, copyright, ad review, creators' money, and ad top-ups.

Videos is one catalog for the whole platform and its ads move money between organizations (an advertiser in one
pays a creator in another), so this work is Aira's, done by platform operators in the console:

* **Videos moderation** (support and platform admins, ``platform.moderation.manage``): reports, hiding and
  restoring, suspending a channel, copyright notices (take down, reject, restore after a counter-notice, uphold).
* **Creators & ads**: the totals (gross ad revenue, creator earnings, what Aira keeps), the ad review queue
  (``platform.moderation.manage``), and the money (``platform.billing.manage``, platform admins): the creators'
  revenue share and minimum payout, payouts marked paid with a reference, closing a month, clawbacks.

Every change is in the platform audit log (``videos.*``, ``ads.*``). An organization tops up its own ad balance
with ``POST /api/org/ads/topup`` (billed through its subscription; see Billing.charge_ads_topup).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from aira_cloud.errors import ValidationFailed
from aira_cloud.webio import Req, cloud_route

ID = r"([0-9a-f]{32})"


def _videos() -> closing[sqlite3.Connection]:
    from aira.suite.videos import db as videos_db

    return closing(videos_db.connect())


def _by(req: Req) -> str:
    return f"aira:{req.principal.email}"


def _status(req: Req, allowed: tuple[str, ...], default: str) -> str:
    status = req.q("status", default)
    if status not in allowed:
        raise ValidationFailed(f"status is one of {', '.join(allowed)}.")
    return status


# ----------------------------------------------------------------------------- Videos moderation


@cloud_route("GET", r"/api/platform/videos/reports", auth="operator")
def videos_reports(req: Req):  # noqa: ANN201
    from aira.suite.videos import service as videos

    req.app.authz.require(req.principal, "platform.moderation.manage")
    status = _status(req, ("open", "resolved", "dismissed"), "open")
    with _videos() as sc:
        return 200, {"reports": videos.reports(sc, status=status)}


@cloud_route("POST", rf"/api/platform/videos/reports/{ID}", auth="operator")
def videos_moderate(req: Req):  # noqa: ANN201
    from aira.suite.videos import service as videos

    req.app.authz.require(req.principal, "platform.moderation.manage")
    action = str(req.body().get("action", ""))
    with _videos() as sc:
        result = videos.moderate(sc, _by(req), req.group(), action)
    req.app.platform_audit.record(
        "videos.moderate",
        operator=req.principal,
        target_type="videos_report",
        target_id=req.group(),
        detail={"action": result["action"]},
    )
    return 200, result


@cloud_route("POST", rf"/api/platform/videos/videos/{ID}", auth="operator")
def videos_video_state(req: Req):  # noqa: ANN201
    """Hide a video for everyone, or restore it (a moderator's own decision, without a report)."""
    from aira.suite.videos import service as videos

    req.app.authz.require(req.principal, "platform.moderation.manage")
    action = str(req.body().get("action", ""))
    if action not in {"hide", "restore"}:
        raise ValidationFailed("action is hide or restore.")
    reason = str(req.body().get("reason") or "")[:200]
    with _videos() as sc:
        if not sc.execute(
            "SELECT 1 FROM vi_videos WHERE id = ? AND deleted_at IS NULL",
            (req.group(),),
        ).fetchone():
            return 404, {"error": "That video was not found.", "code": "not_found"}
        videos.set_hidden(
            sc, "video", req.group(), action == "hide", reason or "Reviewed by Aira"
        )
        sc.commit()
    req.app.platform_audit.record(
        f"videos.{action}",
        operator=req.principal,
        target_type="video",
        target_id=req.group(),
        detail={"reason": reason},
    )
    return 200, {"hidden": action == "hide"}


@cloud_route("POST", rf"/api/platform/videos/channels/{ID}/suspend", auth="operator")
def videos_suspend(req: Req):  # noqa: ANN201
    from aira.suite.videos import service as videos

    req.app.authz.require(req.principal, "platform.moderation.manage")
    on = bool(req.body().get("suspended", True))
    with _videos() as sc:
        if not sc.execute(
            "SELECT 1 FROM vi_channels WHERE id = ?", (req.group(),)
        ).fetchone():
            return 404, {"error": "That channel was not found.", "code": "not_found"}
        videos.set_suspended(
            sc, req.group(), on, str(req.body().get("reason") or "Reviewed by Aira")
        )
    req.app.platform_audit.record(
        "videos.suspend" if on else "videos.unsuspend",
        operator=req.principal,
        target_type="videos_channel",
        target_id=req.group(),
    )
    return 200, {"suspended": on}


@cloud_route("GET", r"/api/platform/videos/copyright", auth="operator")
def videos_notices(req: Req):  # noqa: ANN201
    from aira.suite.videos import service as videos

    req.app.authz.require(req.principal, "platform.moderation.manage")
    status = req.q("status", "")
    if status and status not in videos.NOTICE_STATUSES:
        raise ValidationFailed(f"status is one of {', '.join(videos.NOTICE_STATUSES)}.")
    with _videos() as sc:
        return 200, {"notices": videos.notices(sc, status)}


@cloud_route("POST", rf"/api/platform/videos/copyright/{ID}", auth="operator")
def videos_notice_decide(req: Req):  # noqa: ANN201
    from aira.suite.videos import service as videos

    req.app.authz.require(req.principal, "platform.moderation.manage")
    body = req.body()
    with _videos() as sc:
        notice = videos.decide_notice(
            sc,
            req.group(),
            str(body.get("action", "")),
            _by(req),
            str(body.get("note") or ""),
        )
    req.app.platform_audit.record(
        f"videos.copyright_{notice['status']}",
        operator=req.principal,
        target_type="videos_notice",
        target_id=req.group(),
        detail={"video_id": notice["video"]["id"]},
    )
    return 200, {"notice": notice}


@cloud_route("POST", r"/api/videos/copyright", auth="public")
def videos_copyright_notice(req: Req):  # noqa: ANN201
    """The public copyright notice form (anyone, no account). Answered here rather than by the shared route, which
    would need an organization bound to the request; a notice belongs to the platform."""
    from aira.suite.videos import service as videos

    with _videos() as sc:
        return 201, videos.file_notice(sc, req.body(), req.request.client_ip)


# ----------------------------------------------------------------------------- Creators & ads


@cloud_route("GET", r"/api/platform/ads/overview", auth="operator")
def ads_overview(req: Req):  # noqa: ANN201
    """The books and both queues (support reads them; platform admins change the money)."""
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.moderation.manage")
    with _videos() as sc:
        return 200, {
            "totals": ads.totals(sc),
            "payouts": ads.payouts(sc, "requested"),
            "paid": ads.payouts(sc, "paid")[:50],
            "can_manage_money": req.app.authz.can(
                req.principal, "platform.billing.manage"
            ),
        }


@cloud_route("GET", r"/api/platform/ads/review", auth="operator")
def ads_review_queue(req: Req):  # noqa: ANN201
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.moderation.manage")
    status = _status(req, ("pending", "approved", "rejected"), "pending")
    with _videos() as sc:
        return 200, {
            "creatives": ads.review_queue(
                sc, status, media_prefix="/api/platform/ads/media"
            )
        }


@cloud_route("GET", rf"/api/platform/ads/media/{ID}", auth="operator")
def ads_media(req: Req):  # noqa: ANN201
    """An ad's video or picture for the reviewers (signed like every media address)."""
    from aira.suite.videos import media
    from aira_cloud.webio import HttpResponse

    req.app.authz.require(req.principal, "platform.moderation.manage")
    with _videos() as sc:
        media.verify(sc, req.group(), req.q("e"), req.q("s"))
        found = media.file_row(sc, req.group())
    status, body, headers = media.read_range(
        found, req.request.headers.get("range", "")
    )
    return HttpResponse(status, [("Content-Type", found["mime"]), *headers], body)


@cloud_route("POST", rf"/api/platform/ads/creatives/{ID}/review", auth="operator")
def ads_review(req: Req):  # noqa: ANN201
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.moderation.manage")
    body = req.body()
    with _videos() as sc:
        result = ads.review(
            sc,
            req.group(),
            str(body.get("decision", "")),
            str(body.get("note") or ""),
            _by(req),
        )
    req.app.platform_audit.record(
        f"ads.review_{result['review_status']}",
        operator=req.principal,
        target_type="ad_creative",
        target_id=req.group(),
        detail={"note": str(body.get("note") or "")[:200]},
    )
    return 200, result


@cloud_route("PUT", r"/api/platform/ads/settings", auth="operator")
def ads_settings(req: Req):  # noqa: ANN201
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.billing.manage")
    with _videos() as sc:
        result = ads.save_settings(sc, req.body())
    req.app.platform_audit.record(
        "ads.settings",
        operator=req.principal,
        target_type="ads",
        target_id="settings",
        detail=result,
    )
    return 200, {"settings": result["after"]}


@cloud_route("POST", rf"/api/platform/ads/payouts/{ID}", auth="operator")
def ads_payout(req: Req):  # noqa: ANN201
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.billing.manage")
    body = req.body()
    with _videos() as sc:
        result = ads.decide_payout(
            sc,
            req.group(),
            str(body.get("action", "")),
            str(body.get("reference") or ""),
            str(body.get("note") or ""),
            _by(req),
        )
    req.app.platform_audit.record(
        f"ads.payout_{result['status']}",
        operator=req.principal,
        target_type="ad_payout",
        target_id=req.group(),
        detail={
            "amount_micros": result["amount_micros"],
            "reference": result["reference"],
        },
    )
    return 200, {"payout": result}


@cloud_route("POST", r"/api/platform/ads/close-month", auth="operator")
def ads_close_month(req: Req):  # noqa: ANN201
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.billing.manage")
    with _videos() as sc:
        result = ads.close_month(sc, str(req.body().get("month") or ""), _by(req))
    req.app.platform_audit.record(
        "ads.month_close",
        operator=req.principal,
        target_type="ads_month",
        target_id=result["month"],
        detail=result,
    )
    return 200, result


@cloud_route("POST", r"/api/platform/ads/clawback", auth="operator")
def ads_clawback(req: Req):  # noqa: ANN201
    from aira.suite.ads import service as ads

    req.app.authz.require(req.principal, "platform.billing.manage")
    body = req.body()
    with _videos() as sc:
        result = ads.clawback(
            sc,
            str(body.get("channel_id") or ""),
            str(body.get("month") or ""),
            str(body.get("reason") or ""),
            _by(req),
        )
    req.app.platform_audit.record(
        "ads.clawback",
        operator=req.principal,
        target_type="videos_channel",
        target_id=str(body.get("channel_id") or ""),
        detail={k: v for k, v in result.items() if k != "by"}
        | {"month": str(body.get("month") or "")},
    )
    return 200, result


# ----------------------------------------------------------------------------- an organization's ad balance


@cloud_route("POST", r"/api/org/ads/topup", writes=True)
def org_ads_topup(req: Req):  # noqa: ANN201
    """Add money to the organization's ad balance; billed with its subscription (owners and billing contacts)."""
    amount: Any = req.body().get("amount_cents")
    return 200, req.app.billing.charge_ads_topup(req.principal, amount)
