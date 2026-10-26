"""Routes for Marketing (``/api/marketing/*``), its public pages and its Business hub numbers.

Public pages: ``go`` (a tracked link: counts the click, the page sends the visitor on), ``form`` (a lead-capture
form), ``bio`` (the link-in-bio page, one per business) and ``unsubscribe`` (one per subscriber, linked from
every newsletter). Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.marketing import service, store
from aira.suite.marketing.store import APP

BASE = "/api/marketing"
ID = "([0-9a-f]{32})"
BIO_REF = "bio"


def _connect(request: Request) -> Any:
    connection = request.connect()
    store.ensure(connection)
    return connection


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...], filename: str) -> Raw:
    flat = [
        {
            **row,
            **{
                key: ", ".join(value)
                for key, value in row.items()
                if isinstance(value, list)
            },
        }
        for row in rows
    ]
    return Raw(
        200, kit.csv_bytes(flat, columns), "text/csv; charset=utf-8", filename=filename
    )


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        data = service.overview(connection)
        data["checklist"] = service.checklist(connection)
        data["bio_link"] = public.link_for(connection, APP, "bio", BIO_REF)
        data["campaigns"] = [
            {"id": c["id"], "name": c["name"]}
            for c in service.list_campaigns(connection)
        ]
    return 200, data


# ---------------------------------------------------------------------------
# posts
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/posts")
def posts_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        posts = service.list_posts(
            connection,
            start=request.q("from"),
            end=request.q("to"),
            status=request.q("status"),
            channel=request.q("channel"),
            campaign_id=request.q("campaign_id"),
        )
    return 200, {
        "posts": posts,
        "channels": [{"id": k, **v} for k, v in service.CHANNELS.items()],
    }


@aira_route("POST", f"{BASE}/posts")
def posts_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        post = service.create_post(connection, request.payload)
    request.audit("marketing.post_create", "post", post["id"])
    return 201, {"post": post}


@aira_route("PATCH", f"{BASE}/posts/{ID}")
def posts_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "post": service.update_post(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/posts/{ID}")
def posts_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.delete(connection, "mk_posts", request.match.group(1), "post")
        connection.commit()
    request.audit("marketing.post_delete", "post", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/posts/{ID}/publish")
def posts_publish(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "post": service.publish_post(
                connection, request.match.group(1), request.payload.get("url")
            )
        }


@aira_route("POST", f"{BASE}/posts/{ID}/repurpose")
def posts_repurpose(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        created = service.repurpose(
            connection,
            request.match.group(1),
            list(request.payload.get("channels") or []),
        )
    return 201, {"posts": created}


@aira_route("POST", f"{BASE}/draft")
def draft(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"draft": service.draft_post(connection, request.payload)}


@aira_route("POST", f"{BASE}/plan")
def plan(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, service.content_plan(connection, request.payload)


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/campaigns")
def campaigns_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"campaigns": service.list_campaigns(connection)}


@aira_route("POST", f"{BASE}/campaigns")
def campaigns_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        campaign = service.create_campaign(connection, request.payload)
    request.audit("marketing.campaign_create", "campaign", campaign["id"])
    return 201, {"campaign": campaign}


@aira_route("PATCH", f"{BASE}/campaigns/{ID}")
def campaigns_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "campaign": service.update_campaign(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/campaigns/{ID}")
def campaigns_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        service.delete_campaign(connection, request.match.group(1))
    request.audit("marketing.campaign_delete", "campaign", request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# tracked links
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/links")
def links_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"links": service.list_links(connection)}


@aira_route("POST", f"{BASE}/links")
def links_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        link = service.create_link(connection, request.payload)
    request.audit("marketing.link_create", "link", link["id"])
    return 201, {"link": link}


@aira_route("PATCH", f"{BASE}/links/{ID}")
def links_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "link": service.update_link(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/links/{ID}")
def links_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        service.delete_link(connection, request.match.group(1))
    request.audit("marketing.link_delete", "link", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/links/{ID}/clicks")
def links_clicks(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        link = service.get_link(connection, request.match.group(1))
        days = service.clicks_by_day(
            connection, "link", link["id"], request.qint("days", 30, 7, 90)
        )
    return 200, {"link": link, "days": days}


@aira_route("POST", f"{BASE}/links/{ID}/share")
def links_share(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        link = service.get_link(connection, request.match.group(1))
        made = public.create_link(
            connection, APP, "go", link["id"], title=link["title"] or link["slug"]
        )
    return 200, {"public": made}


@aira_route("DELETE", f"{BASE}/links/{ID}/share")
def links_unshare(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        service.get_link(connection, request.match.group(1))
        public.revoke(connection, APP, "go", request.match.group(1))
    return 200, {"public": None}


# ---------------------------------------------------------------------------
# forms and submissions
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/forms")
def forms_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"forms": service.list_forms(connection)}


@aira_route("POST", f"{BASE}/forms")
def forms_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        form = service.create_form(connection, request.payload)
    request.audit("marketing.form_create", "form", form["id"])
    return 201, {"form": form}


@aira_route("PATCH", f"{BASE}/forms/{ID}")
def forms_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "form": service.update_form(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/forms/{ID}")
def forms_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        service.delete_form(connection, request.match.group(1))
    request.audit("marketing.form_delete", "form", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/forms/{ID}/link")
def forms_share(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        form = service.get_form(connection, request.match.group(1))
        made = public.create_link(
            connection, APP, "form", form["id"], title=form["title"]
        )
        kit.log(
            connection,
            APP,
            "share",
            f"Form {form['title']} shared",
            ref_type="form",
            ref_id=form["id"],
        )
        connection.commit()
    return 200, {"public": made}


@aira_route("DELETE", f"{BASE}/forms/{ID}/link")
def forms_unshare(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        service.get_form(connection, request.match.group(1))
        public.revoke(connection, APP, "form", request.match.group(1))
    return 200, {"public": None}


@aira_route("GET", f"{BASE}/submissions")
def submissions_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _connect(request) as connection:
        rows = service.list_submissions(
            connection,
            form_id=request.q("form_id"),
            status=request.q("status"),
            limit=5000,
        )
    if request.q("format") == "csv":
        return _csv(rows, service.SUBMISSION_COLUMNS, "leads.csv")
    return 200, {
        "submissions": rows[:500],
        "statuses": list(service.SUBMISSION_STATUSES),
    }


@aira_route("PATCH", f"{BASE}/submissions/{ID}")
def submissions_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "submission": service.update_submission(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/submissions/{ID}")
def submissions_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.delete(connection, "mk_submissions", request.match.group(1), "submission")
        connection.commit()
    request.audit("marketing.submission_delete", "submission", request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# subscribers
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/subscribers")
def subscribers_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _connect(request) as connection:
        rows = service.list_subscribers(
            connection,
            status=request.q("status"),
            tag=request.q("tag"),
            query=request.q("q"),
            limit=20000,
        )
        counts = {
            "subscribed": store.count(
                connection,
                "SELECT COUNT(*) FROM mk_subscribers WHERE status = 'subscribed'",
            ),
            "unsubscribed": store.count(
                connection,
                "SELECT COUNT(*) FROM mk_subscribers WHERE status = 'unsubscribed'",
            ),
        }
    if request.q("format") == "csv":
        return _csv(rows, service.SUBSCRIBER_COLUMNS, "subscribers.csv")
    tags = sorted({tag for row in rows for tag in row["tags"]})
    return 200, {"subscribers": rows[:1000], "counts": counts, "tags": tags}


@aira_route("POST", f"{BASE}/subscribers")
def subscribers_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        address = kit.email(
            request.payload.get("email"), "an email address", required=True
        )
        existing = kit.fetch_one(
            connection, "SELECT status FROM mk_subscribers WHERE email = ?", (address,)
        )
        if existing and existing["status"] == "unsubscribed":
            raise ValueError(
                "This address unsubscribed. Only the person can subscribe again, through one of your forms."
            )
        subscriber = service.add_subscriber(
            connection, request.payload, source="manual"
        )
    return 201, {"subscriber": subscriber}


@aira_route("PATCH", f"{BASE}/subscribers/{ID}")
def subscribers_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "subscriber": service.update_subscriber(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/subscribers/{ID}")
def subscribers_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.delete(connection, "mk_subscribers", request.match.group(1), "subscriber")
        public.revoke(connection, APP, "unsubscribe", request.match.group(1))
        connection.commit()
    request.audit("marketing.subscriber_delete", "subscriber", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/subscribers/import")
def subscribers_import(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        result = service.import_subscribers(
            connection, str(request.payload.get("csv") or "")
        )
    request.audit("marketing.subscriber_import", "subscriber", "", result)
    return 200, result


# ---------------------------------------------------------------------------
# the bio page
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/bio")
def bio_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "profile": service.bio_profile(connection),
            "links": service.bio_links(connection),
            "public": public.link_for(connection, APP, "bio", BIO_REF),
            "forms": [
                {"id": f["id"], "title": f["title"], "shared": bool(f["public"])}
                for f in service.list_forms(connection)
            ],
        }


@aira_route("POST", f"{BASE}/bio")
def bio_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {"profile": service.save_bio(connection, request.payload)}


@aira_route("POST", f"{BASE}/bio/links")
def bio_links_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 201, {"link": service.add_bio_link(connection, request.payload)}


@aira_route("PATCH", f"{BASE}/bio/links/{ID}")
def bio_links_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "link": service.update_bio_link(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/bio/links/{ID}")
def bio_links_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.delete(connection, "mk_bio_links", request.match.group(1), "link")
        connection.commit()
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/bio/links/{ID}/move")
def bio_links_move(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "links": service.move_bio_link(
                connection,
                request.match.group(1),
                str(request.payload.get("direction") or ""),
            )
        }


@aira_route("POST", f"{BASE}/bio/share")
def bio_share(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        made = public.create_link(connection, APP, "bio", BIO_REF, title="Links")
    return 200, {"public": made}


@aira_route("DELETE", f"{BASE}/bio/share")
def bio_unshare(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        public.revoke(connection, APP, "bio", BIO_REF)
    return 200, {"public": None}


# ---------------------------------------------------------------------------
# newsletters
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/newsletters")
def newsletters_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "newsletters": service.list_newsletters(connection),
            "cap": service.NEWSLETTER_CAP,
        }


@aira_route("POST", f"{BASE}/newsletters")
def newsletters_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 201, {
            "newsletter": service.create_newsletter(connection, request.payload)
        }


@aira_route("PATCH", f"{BASE}/newsletters/{ID}")
def newsletters_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        return 200, {
            "newsletter": service.update_newsletter(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/newsletters/{ID}")
def newsletters_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        store.delete(connection, "mk_newsletters", request.match.group(1), "newsletter")
        connection.execute(
            "DELETE FROM mk_newsletter_recipients WHERE newsletter_id = ?",
            (request.match.group(1),),
        )
        connection.commit()
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/newsletters/{ID}/queue")
def newsletters_queue(request: Request) -> tuple[int, dict[str, Any]]:
    with _connect(request) as connection:
        result = service.queue_newsletter(
            connection,
            request.match.group(1),
            confirm=request.payload.get("confirm"),
            source=request,
        )
    request.audit(
        "marketing.newsletter_queue",
        "newsletter",
        request.match.group(1),
        {"queued": result["queued"]},
    )
    return 200, result


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, "go")
def go_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    """A visit to a tracked link: the click is counted and the page sends the visitor on."""
    store.ensure(connection)
    row = kit.fetch_one(
        connection, "SELECT * FROM mk_links WHERE id = ?", (link["ref_id"],)
    )
    if not row or not str(row["destination"]).lower().startswith("https://"):
        raise LookupError("This link is no longer available.")
    service.record_click(connection, "link", row["id"])
    return {"url": service.final_url(row), "title": row["title"] or row["slug"]}


@public.page(APP, "form")
def form_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    form = service.get_form(connection, link["ref_id"])
    connection.execute(
        "UPDATE mk_forms SET views = views + 1 WHERE id = ?", (form["id"],)
    )
    return service.public_form(form)


@public.action(APP, "form", "submit")
def form_submit(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 201, service.submit(connection, link["ref_id"], payload)


@public.page(APP, "bio")
def bio_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.public_bio(connection)


@public.action(APP, "bio", "click")
def bio_click(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    link_id = str(payload.get("link_id") or "")
    row = kit.fetch_one(
        connection, "SELECT * FROM mk_bio_links WHERE id = ?", (link_id,)
    )
    if not row:
        raise LookupError("That link was not found.")
    service.record_click(connection, "bio", row["id"])
    connection.commit()
    return 200, {"url": row["url"]}


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


@public.page(APP, "unsubscribe")
def unsubscribe_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    subscriber = store.get(connection, "mk_subscribers", link["ref_id"], "subscriber")
    return {
        "email": _mask(subscriber["email"]),
        "subscribed": subscriber["status"] == "subscribed",
    }


@public.action(APP, "unsubscribe", "unsubscribe")
def unsubscribe_action(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    subscriber = service.unsubscribe(connection, link["ref_id"])
    return 200, {"subscribed": subscriber["status"] == "subscribed"}


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    store.ensure(connection)
    return service.checklist(connection)


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    store.ensure(connection)
    data = service.overview(connection)
    return {
        "cards": [
            {
                "label": "Subscribers",
                "value": data["subscribers"],
                "hint": f"+{data['new_subscribers_month']} this month",
            },
            {
                "label": "Leads this month",
                "value": data["leads_month"],
                "tone": "warn" if data["new_leads"] else "",
            },
            {"label": "Clicks this month", "value": data["clicks_month"]},
        ]
    }
