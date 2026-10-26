"""News routes (``/api/news/*``), its Today card, pulse and background refresh.

Every route works for the person asking (:func:`aira.suite.life.current`); :mod:`aira.suite.news.service` holds the
rules. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Request, aira_route
from aira.suite import background, kit, life
from aira.suite.news import service, store

APP = store.APP
BASE = "/api/news"
ID = "([0-9a-f]{32})"
# POST routes that only read (hosted rules: a reader may call them).
HOSTED_RULES = {"refresh": "read", "article_read": "read"}


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    try:
        store.ensure(connection)
        me = life.current(request, connection)
    except BaseException:
        connection.close()  # the picker, or a failure: do not leave the connection behind
        raise
    return connection, me


@aira_route("GET", f"{BASE}/feed")
def feed(request: Request) -> tuple[int, dict[str, Any]]:
    """A tab's articles: ``topic`` = top, a section code, foryou, source:<id> or all; ``since`` for live updates."""
    since = kit.moment(request.q("since"), "since") if request.q("since") else ""
    connection, me = _open(request)
    with connection:
        data = service.articles(
            connection,
            me,
            topic=request.q("topic", "top") or "top",
            since=since,
            limit=request.qint("limit", 40, 1, 100),
        )
        if not since:
            service.mark_opened(
                connection, me
            )  # the person looked: the badge starts again from now
        return 200, data


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.settings_view(connection, me)


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.save_settings(connection, me, request.payload)


@aira_route("POST", f"{BASE}/keywords")
def keywords_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"keyword": service.add_keyword(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/keywords/{ID}")
def keywords_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "keyword": service.update_keyword(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/keywords/{ID}")
def keywords_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_keyword(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/sources")
def sources_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        source = service.add_source(connection, me, request.payload)
    request.audit("news.source_add", "source", source["id"])
    return 201, {"source": source}


@aira_route("DELETE", f"{BASE}/sources/{ID}")
def sources_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_source(connection, me, request.match.group(1))
        return 200, {"removed": True}


@aira_route("POST", f"{BASE}/articles/{ID}/save")
def article_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"article": service.save(connection, me, request.match.group(1))}


@aira_route("DELETE", f"{BASE}/articles/{ID}/save")
def article_unsave(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"article": service.unsave(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/articles/{ID}/read")
def article_read(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "article": service.mark_read(connection, me, request.match.group(1))
        }


@aira_route("GET", f"{BASE}/saved")
def saved_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"articles": service.saved(connection, me)}


@aira_route("POST", f"{BASE}/refresh")
def refresh(request: Request) -> tuple[int, dict[str, Any]]:
    """Fetch everything the person follows now (each feed at most once per minute)."""
    connection, me = _open(request)
    with connection:
        results = []
        now = kit.utcnow()
        for row in service.feeds_for(connection, me, "all"):
            recent = (
                row.get("last_fetched")
                and (now - kit.parse_time(row["last_fetched"])).total_seconds() < 60
            )
            results.append(service.refresh(connection, row, force=not recent))
        failed = sum(1 for r in results if not r["ok"])
        return 200, {
            "feeds": len(results),
            "added": sum(r.get("added", 0) for r in results),
            "error": service.REFRESH_ERROR if failed else "",
        }


@aira_route("GET", f"{BASE}/briefing")
def briefing(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.briefing(connection, me)


# ---------------------------------------------------------------------------
# Today, pulse and background
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    data = service.today_lines(connection, me)
    lines = [
        {
            "text": a["title"],
            "sub": " · ".join(part for part in (a["source"], a["ago"]) if part),
        }
        for a in data["articles"]
    ]
    if lines:
        lines[-1] = {
            **lines[-1],
            "sub": f"{lines[-1]['sub']}. Open News to listen to them.",
        }
    return [
        life.card(
            APP,
            "Today's headlines",
            lines=lines,
            empty=data["error"] or "No headlines yet.",
        )
    ]


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    return {
        "badge": service.badge(connection, me),
        "alerts": service.keyword_alerts(connection, me),
    }


@background.task(APP)
def _background(connection: Any) -> dict[str, Any]:
    """Keep every followed feed fresh, so the news is ready when someone opens it."""
    store.ensure(connection)
    return service.background_refresh(connection)
