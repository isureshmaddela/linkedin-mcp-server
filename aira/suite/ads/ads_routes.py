"""Routes for Ads (``/api/ads/*``): the advertiser's campaigns, ads, uploads, reports and balance, the Business hub's
checklist and numbers, and on this computer the owner's admin page (settings, review, payouts, closing months).

An advertiser is the account (the organization hosted, the local account here): its data sits in the platform-wide
Videos database under the account's ``home``. The admin routes are the computer owner's; the hosted build refuses
them (``HOSTED_RULES``) because the platform console does that work (``aira_cloud/routes/creators.py``).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit
from aira.suite.ads import service
from aira.suite.ads.store import APP
from aira.suite.videos import db, media
from aira.suite.videos import service as videos

BASE = "/api/ads"
LOW_BALANCE_DAYS = 3  # the hub warns when the balance lasts fewer days than this at the last week's spending
ID = "([0-9a-f]{32})"

# The hosted build answers these from the platform console instead (aira_cloud/policy.py reads this).
HOSTED_RULES = {
    "admin_get": "deny",
    "admin_settings": "deny",
    "admin_review": "deny",
    "admin_payout": "deny",
    "admin_close_month": "deny",
    "admin_clawback": "deny",
    "admin_payment": "deny",
    "estimate": "read",  # a forecast: changes nothing, so auditors may run it too
}


def _advertiser(connection: Any) -> str:
    return str(kit.business(connection).get("name") or "") or "Your business"


class GrownUpOnly(PermissionError):
    """A child's or a teen's household profile asked for Ads (the screen offers to switch person)."""

    code = "grown_up_only"


def _check_person(request: Request, connection: Any) -> None:
    """Ads spends the account's prepaid money, so it is run by an adult whose profile is unlocked: a PIN-locked
    profile is asked for its PIN (``profile_locked``, like everywhere else), and a child's or a teen's profile is
    refused even though the account is the same."""
    profile = videos.identity(request, connection)["profile"]
    if profile.get("age_group") in ("child", "teen"):
        raise GrownUpOnly(
            "Ads spends your household's money, so only a grown-up's profile can use it. Switch to a grown-up's profile."
        )


@contextmanager
def _open(request: Request) -> Iterator[tuple[Any, str, str]]:
    """(videos connection, the advertiser's home, its name from the business profile), for an unlocked adult."""
    home = videos.home_of(request)
    with closing(request.connect()) as connection:
        kit.ensure_core(connection)
        _check_person(request, connection)
        name = _advertiser(connection)
        if kit.get_setting(connection, APP, "home", "") != home:
            kit.set_setting(
                connection, APP, "home", home
            )  # the hub's numbers find the advertiser by it
            connection.commit()
    with closing(db.connect()) as sc:
        yield sc, home, name


def _hosted(request: Request) -> bool:
    return bool((getattr(request, "user", None) or {}).get("org_id"))


# ---------------------------------------------------------------------------
# overview, campaigns and ads
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, name):
        service.account(sc, home, name)
        return 200, {
            **service.overview(sc, home),
            "advertiser": name,
            "hosted": _hosted(request),
            "settings": service.settings(sc),
            "slots": service.SLOT_LABELS,
            "ctas": list(service.CTAS),
            "categories": list(videos.CATEGORIES),
            "limits": {
                "chunk_bytes": media.CHUNK_BYTES,
                "topup_min_cents": service.MIN_TOPUP_CENTS,
                "topup_max_cents": service.MAX_TOPUP_CENTS,
            },
        }


@aira_route("GET", f"{BASE}/campaigns")
def campaigns(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as (sc, home, _name):
        if request.q("format") == "csv":
            return Raw(
                200,
                service.campaigns_csv(sc, home),
                "text/csv; charset=utf-8",
                filename="ads-campaigns.csv",
            )
        return 200, {"campaigns": service.campaigns(sc, home)}


@aira_route("GET", f"{BASE}/creatives")
def creatives(request: Request) -> tuple[int, dict[str, Any]]:
    """The advertiser's ads with their campaign's name (no media): the search across the top looks through them."""
    with _open(request) as (sc, home, _name):
        return 200, {"creatives": service.creatives_index(sc, home)}


@aira_route("POST", f"{BASE}/estimate")
def estimate(request: Request) -> tuple[int, dict[str, Any]]:
    """What a campaign can expect each day for its bid, budget and audience (``campaign_id`` for an existing one).

    The form asks while a person types, so half-typed targeting ("I" on the way to "IN") is not an error: the answer
    says what to fix in ``problem`` instead of failing the request."""
    payload = dict(request.payload)
    campaign_id = str(payload.pop("campaign_id", "") or "")
    with _open(request) as (sc, home, _name):
        balance = int(service.account(sc, home).get("balance_micros") or 0)
        try:
            found = service.estimate(sc, home, payload, campaign_id)
        except ValueError as error:
            return 200, {
                "estimate": None,
                "problem": str(error),
                "balance_micros": balance,
            }
        return 200, {"estimate": found, "problem": "", "balance_micros": balance}


@aira_route("POST", f"{BASE}/campaigns")
def campaign_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, name):
        found = service.create_campaign(sc, home, name, request.payload)
    with closing(request.connect()) as connection:
        kit.log(
            connection,
            APP,
            "campaign",
            f"Campaign {found['name']} created",
            ref_type="campaign",
            ref_id=found["id"],
        )
        connection.commit()
    request.audit("ads.campaign_create", "campaign", found["id"])
    return 201, {"campaign": found}


@aira_route("GET", f"{BASE}/campaigns/{ID}")
def campaign_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        return 200, {"campaign": service.campaign(sc, home, request.match.group(1))}


@aira_route("PATCH", f"{BASE}/campaigns/{ID}")
def campaign_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        found = service.update_campaign(
            sc, home, request.match.group(1), request.payload
        )
    request.audit("ads.campaign_update", "campaign", found["id"])
    return 200, {"campaign": found}


@aira_route("POST", f"{BASE}/campaigns/{ID}/status")
def campaign_status(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        found = service.set_status(
            sc, home, request.match.group(1), str(request.payload.get("status") or "")
        )
    request.audit(f"ads.campaign_{found['status']}", "campaign", found["id"])
    return 200, {"campaign": found}


@aira_route("POST", f"{BASE}/campaigns/{ID}/duplicate")
def campaign_duplicate(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        found = service.duplicate_campaign(sc, home, request.match.group(1))
    request.audit("ads.campaign_duplicate", "campaign", found["id"])
    return 201, {"campaign": found}


@aira_route("GET", f"{BASE}/campaigns/{ID}/sales")
def sales_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        return 200, {"sales": service.sales(sc, home, request.match.group(1))}


@aira_route("POST", f"{BASE}/campaigns/{ID}/sales")
def sale_add(request: Request) -> tuple[int, dict[str, Any]]:
    by = str((getattr(request, "user", None) or {}).get("email") or "")
    with _open(request) as (sc, home, _name):
        sale = service.add_sale(sc, home, request.match.group(1), request.payload, by)
        found = service.campaign(sc, home, sale["campaign_id"])
    with closing(request.connect()) as connection:
        kit.log(
            connection,
            APP,
            "sale",
            f"{sale['orders']} sale{'s' if sale['orders'] != 1 else ''} worth "
            f"{service.dollars(sale['value_micros'])} from the campaign {found['name']}",
            ref_type="campaign",
            ref_id=found["id"],
        )
        connection.commit()
    request.audit("ads.sale_add", "campaign", found["id"])
    return 201, {"sale": sale, "campaign": found}


@aira_route("DELETE", f"{BASE}/sales/{ID}")
def sale_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        service.delete_sale(sc, home, request.match.group(1))
    request.audit("ads.sale_delete", "sale", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("DELETE", f"{BASE}/campaigns/{ID}")
def campaign_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        service.delete_campaign(sc, home, request.match.group(1))
    request.audit("ads.campaign_delete", "campaign", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/campaigns/{ID}/creatives")
def creative_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        found = service.create_creative(
            sc, home, request.match.group(1), request.payload
        )
    request.audit("ads.creative_create", "creative", found["id"])
    return 201, {"creative": found}


@aira_route("PATCH", f"{BASE}/creatives/{ID}")
def creative_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        found = service.update_creative(
            sc, home, request.match.group(1), request.payload
        )
    request.audit("ads.creative_update", "creative", found["id"])
    return 200, {"creative": found}


@aira_route("DELETE", f"{BASE}/creatives/{ID}")
def creative_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        service.archive_creative(sc, home, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# uploads for ads (the same resumable parts as Videos, kept under the advertiser)
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/uploads")
def upload_start(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        kind = str(request.payload.get("kind") or "")
        if kind not in ("video", "image"):
            raise ValueError("An ad is a video or a picture.")
        upload = media.start(
            sc,
            service.owner_key(home),
            home,
            kind,
            str(request.payload.get("filename") or ""),
            request.payload.get("size"),
        )
    return 201, {"upload": upload}


@aira_route("GET", f"{BASE}/uploads/{ID}")
def upload_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        return 200, {
            "upload": media.upload(sc, request.match.group(1), service.owner_key(home))
        }


def _wrong_file(sc: Any, upload_id: str, owner: str, payload: Any) -> str:
    """Why the first part of an upload is not the kind of file it was started as ('' when it is, or cannot be told
    yet). Asked before the part is written so the browser hears it at once instead of retrying the part."""
    if str(payload.get("offset")) != "0":
        return ""
    try:
        head = base64.b64decode(str(payload.get("data") or "")[:88], validate=True)
    except (ValueError, binascii.Error):
        return ""  # writing the part says what is wrong with it
    try:
        media.sniff(head[:64], media.upload(sc, upload_id, owner)["kind"])
    except ValueError as error:
        return str(error)
    return ""


@aira_route("POST", f"{BASE}/uploads/{ID}/chunk")
def upload_chunk(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        payload = request.payload
        wrong = _wrong_file(
            sc, request.match.group(1), service.owner_key(home), payload
        )
        if wrong:
            # 422, not 400: the shared uploader retries a 400 (a part that may have been cut short) but stops at once
            # on any other refusal, and a wrong kind of file is never going to work
            media.cancel(sc, request.match.group(1), service.owner_key(home))
            return 422, {"error": wrong, "code": "wrong_file"}
        return 200, {
            "upload": media.append(
                sc,
                request.match.group(1),
                service.owner_key(home),
                payload.get("offset"),
                payload.get("data"),
            )
        }


@aira_route("POST", f"{BASE}/uploads/{ID}/complete")
def upload_complete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        return 201, {
            "file": media.complete(sc, request.match.group(1), service.owner_key(home))
        }


@aira_route("DELETE", f"{BASE}/uploads/{ID}")
def upload_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, _name):
        media.cancel(sc, request.match.group(1), service.owner_key(home))
    return 200, {"cancelled": True}


@aira_route("GET", f"{BASE}/media/{ID}")
def media_get(request: Request) -> Raw:
    """An ad's video or picture by its signed address (for the advertiser's preview and the reviewers)."""
    with closing(db.connect()) as sc:
        media.verify(sc, request.match.group(1), request.q("e"), request.q("s"))
        found = media.file_row(sc, request.match.group(1))
    status, body, headers = media.read_range(found, request.header("Range"))
    return Raw(status, body, found["mime"], headers=headers)


# ---------------------------------------------------------------------------
# reports and the balance
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/reports")
def reports(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as (sc, home, _name):
        campaign_id = request.q("campaign")
        days = request.qint("days", 30, 1, 365)
        if request.q("format") == "csv":
            return Raw(
                200,
                service.report_csv(sc, home, campaign_id, days),
                "text/csv; charset=utf-8",
                filename="ads-report.csv",
            )
        return 200, service.report(sc, home, campaign_id, days)


@aira_route("GET", f"{BASE}/balance")
def balance(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as (sc, home, name):
        acct = service.account(sc, home, name)
        return 200, {
            "balance_micros": int(acct.get("balance_micros") or 0),
            "ledger": service.ledger(sc, home),
            "hosted": _hosted(request),
        }


# ---------------------------------------------------------------------------
# the computer owner's admin page (hosted: the platform console)
# ---------------------------------------------------------------------------


def _operator(request: Request) -> str:
    with closing(request.connect()) as connection:
        ident = videos.identity(request, connection)
    if not ident["operator"]:
        raise PermissionError(
            "Only the owner of this computer's first account looks after ads and payouts."
        )
    return f"owner:{ident['profile'].get('name', '')}"


@aira_route("GET", f"{BASE}/admin")
def admin_get(request: Request) -> tuple[int, dict[str, Any]]:
    _operator(request)
    with closing(db.connect()) as sc:
        return 200, {
            "totals": service.totals(sc),
            "review": service.review_queue(sc),
            "payouts": service.payouts(sc, "requested"),
            "paid": service.payouts(sc, "paid")[:50],
            "advertisers": service.advertisers(sc),
        }


@aira_route("PUT", f"{BASE}/admin/settings")
def admin_settings(request: Request) -> tuple[int, dict[str, Any]]:
    by = _operator(request)
    with closing(db.connect()) as sc:
        result = service.save_settings(sc, request.payload)
    request.audit("ads.settings", "ads", "settings", {**result, "by": by})
    return 200, {"settings": result["after"]}


@aira_route("POST", f"{BASE}/admin/creatives/{ID}/review")
def admin_review(request: Request) -> tuple[int, dict[str, Any]]:
    by = _operator(request)
    with closing(db.connect()) as sc:
        result = service.review(
            sc,
            request.match.group(1),
            str(request.payload.get("decision") or ""),
            str(request.payload.get("note") or ""),
            by,
        )
    request.audit(f"ads.review_{result['review_status']}", "creative", result["id"])
    return 200, result


@aira_route("POST", f"{BASE}/admin/payouts/{ID}")
def admin_payout(request: Request) -> tuple[int, dict[str, Any]]:
    by = _operator(request)
    payload = request.payload
    with closing(db.connect()) as sc:
        result = service.decide_payout(
            sc,
            request.match.group(1),
            str(payload.get("action") or ""),
            str(payload.get("reference") or ""),
            str(payload.get("note") or ""),
            by,
        )
    request.audit(
        f"ads.payout_{result['status']}",
        "payout",
        result["id"],
        {"amount_micros": result["amount_micros"]},
    )
    return 200, {"payout": result}


@aira_route("POST", f"{BASE}/admin/close-month")
def admin_close_month(request: Request) -> tuple[int, dict[str, Any]]:
    by = _operator(request)
    with closing(db.connect()) as sc:
        result = service.close_month(sc, str(request.payload.get("month") or ""), by)
    request.audit("ads.month_close", "month", result["month"], result)
    return 200, result


@aira_route("POST", f"{BASE}/admin/clawback")
def admin_clawback(request: Request) -> tuple[int, dict[str, Any]]:
    by = _operator(request)
    payload = request.payload
    with closing(db.connect()) as sc:
        result = service.clawback(
            sc,
            str(payload.get("channel_id") or ""),
            str(payload.get("month") or ""),
            str(payload.get("reason") or ""),
            by,
        )
    request.audit(
        "ads.clawback", "channel", str(payload.get("channel_id") or ""), result
    )
    return 200, result


@aira_route("POST", f"{BASE}/admin/payments")
def admin_payment(request: Request) -> tuple[int, dict[str, Any]]:
    """The owner records money an advertiser on this computer paid them (cash, a transfer); it becomes balance."""
    by = _operator(request)
    home = str(request.payload.get("home") or "") or videos.home_of(request)
    with closing(db.connect()) as sc:
        if not store_has_account(sc, home) and home != videos.home_of(request):
            raise LookupError("That advertiser was not found.")
        acct = service.record_payment(sc, home, request.payload, by)
    request.audit(
        "ads.payment_recorded",
        "advertiser",
        home,
        {"amount_cents": request.payload.get("amount_cents")},
    )
    return 201, {"balance_micros": int(acct.get("balance_micros") or 0)}


def store_has_account(sc: Any, home: str) -> bool:
    return bool(
        sc.execute("SELECT 1 FROM ad_accounts WHERE home = ?", (home,)).fetchone()
    )


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    profile = kit.business(connection)
    home = str(kit.get_setting(connection, APP, "home", "") or "")
    with closing(db.connect()) as sc:
        has_money = bool(home) and bool(
            sc.execute(
                "SELECT 1 FROM ad_ledger WHERE home = ? AND kind IN ('topup', 'payment')",
                (home,),
            ).fetchone()
        )
        has_campaign = bool(home) and bool(
            sc.execute(
                "SELECT 1 FROM ad_campaigns WHERE home = ? AND deleted_at IS NULL",
                (home,),
            ).fetchone()
        )
        approved = bool(home) and bool(
            sc.execute(
                "SELECT 1 FROM ad_creatives WHERE home = ? AND review_status = 'approved' AND archived_at IS NULL",
                (home,),
            ).fetchone()
        )
    return [
        kit.step(
            "ads_profile",
            "Name your business",
            bool(profile["name"]),
            "Viewers see it on your ads",
        ),
        kit.step(
            "ads_balance", "Add money to your ad balance", has_money, "Ads are prepaid"
        ),
        kit.step(
            "ads_campaign",
            "Create a campaign",
            has_campaign,
            "Budget, bid and who sees it",
        ),
        kit.step(
            "ads_approved",
            "Get an ad approved",
            approved,
            "Every ad is reviewed before it runs",
        ),
    ]


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    home = str(kit.get_setting(connection, APP, "home", "") or "")
    if not home:
        return {"cards": []}
    with closing(db.connect()) as sc:
        data = service.overview(sc, home)
    month = data["month"]
    days = data["balance_days"]
    return {
        "cards": [
            {
                "label": "Ad balance",
                "value": service.dollars(data["balance_micros"]),
                "tone": "warn"
                if data["balance_micros"] <= 0
                or (days is not None and days < LOW_BALANCE_DAYS)
                else "",
                "hint": f"lasts about {days:g} days" if days is not None else "",
            },
            {
                "label": "Ad spend this month",
                "value": service.dollars(month["spend_micros"]),
            },
            {"label": "Ad impressions this month", "value": month["impressions"]},
            {"label": "Ad clicks this month", "value": month["clicks"]},
        ]
    }
