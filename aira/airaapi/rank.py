"""Rank tracker: where a website shows up on Google for the keywords it should win.

A *project* is one website (its domain), the keywords to watch, where to search
from (location, country, language, device) and how often. Each keyword is an
ordinary saved Google search with its own monitor, so scheduling, monthly
budgets, the hosted worker's credit accounting and the History tab all work
the way they do for every other monitor. A monitor hook
(:func:`process_monitor_run`) turns each finished run into a *check*: the
position of the first organic result on the project's domain (or none), the page
that ranked and the three domains above it.

Rank tracking is a service agencies and freelancers sell: a project carries the
client's name and the monthly fee charged, the Earnings tab adds those fees up
against the searches spent, and :func:`report` builds the client report
(CSV or a self-contained HTML page) to send each month.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from aira.airaapi import catalog, service, store
from aira.airaapi.client import SerpApiError

ENGINE = "google"
TAG = "rank-tracker"
MAX_PROJECTS = 20
MAX_KEYWORDS_PER_PROJECT = 25
MAX_KEYWORDS_TOTAL = 60
HISTORY_POINTS = 60
INTERVALS = (0, 1440, 10080)  # by hand only, daily, weekly
DEVICES = ("", "desktop", "mobile", "tablet")
MAX_FEE = 1_000_000
NOTABLE_MOVE = 3
_DOMAIN_RE = re.compile(
    r"^(?=.{3,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$"
)
_CODE_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2,4})?$")


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def normalize_domain(raw: Any) -> str:
    """``https://www.Example.com/shop`` -> ``example.com``; anything else is a ValueError."""
    text = str(raw or "").strip().lower()
    if not text:
        raise ValueError("Enter the website to track, for example example.com.")
    if "://" not in text:
        text = "https://" + text
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        host = ""
    host = host.removeprefix("www.").rstrip(".")
    if not _DOMAIN_RE.match(host):
        raise ValueError(
            "That does not look like a website address. Use a domain such as example.com."
        )
    return host


def host_of(url: Any) -> str:
    try:
        host = urlsplit(str(url or "")).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def on_domain(url: Any, domain: str) -> bool:
    host = host_of(url)
    return bool(host) and (host == domain or host.endswith("." + domain))


def _keywords(raw: Any) -> list[str]:
    items = raw.splitlines() if isinstance(raw, str) else list(raw or [])
    if isinstance(raw, str) and len(items) == 1 and "," in items[0]:
        items = items[0].split(",")
    seen: dict[str, str] = {}
    for item in items:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if len(text) > 200:
            raise ValueError("Keep each keyword under 200 characters.")
        seen.setdefault(text.casefold(), text)
    return list(seen.values())


def _short(payload: Mapping[str, Any], name: str, limit: int) -> str:
    text = " ".join(str(payload.get(name) or "").split())
    if len(text) > limit:
        raise ValueError(
            f"{name.replace('_', ' ').capitalize()} must be {limit} characters or fewer."
        )
    return text


def _code(payload: Mapping[str, Any], name: str, label: str) -> str:
    text = str(payload.get(name) or "").strip().lower()
    if text and not _CODE_RE.match(text):
        raise ValueError(f"{label} must be a short code such as us or en.")
    return text


def _interval(raw: Any) -> int:
    try:
        minutes = int(raw or 0)
    except (TypeError, ValueError):
        raise ValueError(
            "Choose how often to check: by hand, daily or weekly."
        ) from None
    if minutes not in INTERVALS:
        raise ValueError("Choose how often to check: by hand, daily or weekly.")
    return minutes


def _fee(raw: Any) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError):
        raise ValueError("The monthly fee must be a number, for example 150.") from None
    if not 0 <= value <= MAX_FEE:
        raise ValueError("The monthly fee must be between 0 and 1,000,000.")
    return value


def _flag(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _search_params(project: Mapping[str, Any], keyword: str) -> dict[str, Any]:
    params: dict[str, Any] = {"q": keyword}
    for name in ("location", "gl", "hl", "device"):
        if project.get(name):
            params[name] = project[name]
    clean, problems = catalog.validate_params(ENGINE, params)
    if problems:
        raise ValueError(" ".join(problems))
    return clean


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _project_row(connection: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    store.ensure_extras(connection)
    row = connection.execute(
        "SELECT * FROM airaapi_rank_projects WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Rank project not found.")
    return row


def _keyword_rows(connection: sqlite3.Connection, project_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT k.*, m.enabled AS monitor_enabled, m.next_run_at, m.last_change_summary "
        "FROM airaapi_rank_keywords k LEFT JOIN airaapi_monitors m ON m.id = k.monitor_id "
        "WHERE k.project_id = ? ORDER BY k.created_at, k.rowid",
        (project_id,),
    ).fetchall()


def _checks(
    connection: sqlite3.Connection, keyword_id: str, limit: int
) -> list[sqlite3.Row]:
    rows = connection.execute(
        "SELECT * FROM airaapi_rank_checks WHERE keyword_id = ? "
        "ORDER BY checked_at DESC, rowid DESC LIMIT ?",
        (keyword_id, limit),
    ).fetchall()
    return list(reversed(rows))


def _move(latest: int | None, previous: int | None) -> int | None:
    """Places gained since the previous check (positive is better); None when not comparable."""
    if latest is None and previous is None:
        return None
    if latest is not None and previous is not None:
        return previous - latest
    return None


def _keyword_view(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    history = _checks(connection, row["id"], HISTORY_POINTS)
    latest = history[-1] if history else None
    previous = history[-2] if len(history) > 1 else None
    positions = [c["position"] for c in history if c["position"] is not None]
    return {
        "id": row["id"],
        "keyword": row["keyword"],
        "saved_id": row["saved_id"],
        "monitor_id": row["monitor_id"],
        "position": latest["position"] if latest else None,
        "previous": previous["position"] if previous else None,
        "change": _move(
            latest["position"] if latest else None,
            previous["position"] if previous else None,
        ),
        "entered": bool(
            latest
            and previous
            and latest["position"] is not None
            and previous["position"] is None
        ),
        "dropped": bool(
            latest
            and previous
            and latest["position"] is None
            and previous["position"] is not None
        ),
        "best": min(positions) if positions else None,
        "url": latest["url"] if latest else "",
        "title": latest["title"] if latest else "",
        "depth": latest["depth"] if latest else 0,
        "competitors": json.loads(latest["competitors"] or "[]") if latest else [],
        "checked_at": latest["checked_at"] if latest else None,
        "search_id": latest["search_id"] if latest else "",
        "checks": len(history),
        "history": [
            {
                "at": c["checked_at"],
                "position": c["position"],
                "search_id": c["search_id"],
            }
            for c in history
        ],
        "status": row["last_change_summary"] or "",
    }


def _stats(keywords: list[dict[str, Any]]) -> dict[str, Any]:
    checked = [k for k in keywords if k["checked_at"]]
    ranked = [k["position"] for k in checked if k["position"] is not None]
    return {
        "keywords": len(keywords),
        "checked": len(checked),
        "ranked": len(ranked),
        "top3": sum(1 for p in ranked if p <= 3),
        "top10": sum(1 for p in ranked if p <= 10),
        "not_found": len(checked) - len(ranked),
        "average": round(sum(ranked) / len(ranked), 1) if ranked else None,
        "improved": sum(1 for k in checked if (k["change"] or 0) > 0 or k["entered"]),
        "declined": sum(1 for k in checked if (k["change"] or 0) < 0 or k["dropped"]),
    }


def _runs_per_month(
    project: Mapping[str, Any] | sqlite3.Row, keyword_count: int
) -> int:
    minutes = int(project["interval_minutes"] or 0)
    if not minutes:
        return 0
    return keyword_count * -(-30 * 24 * 60 // minutes)


def checks_in_month(connection: sqlite3.Connection, project_id: str, month: str) -> int:
    """Live checks a project ran in ``YYYY-MM`` (each is one search credit).

    Read from the monthly ledger, so removing a keyword or deleting the project does not undo work already done."""
    return store.rank_usage(connection, project_id, month)


def project_view(
    connection: sqlite3.Connection, row: sqlite3.Row, *, detail: bool = True
) -> dict[str, Any]:
    keywords = [
        _keyword_view(connection, k) for k in _keyword_rows(connection, row["id"])
    ]
    last = max((k["checked_at"] for k in keywords if k["checked_at"]), default=None)
    view = {
        "id": row["id"],
        "name": row["name"],
        "domain": row["domain"],
        "client_name": row["client_name"],
        "location": row["location"],
        "gl": row["gl"],
        "hl": row["hl"],
        "device": row["device"],
        "interval_minutes": row["interval_minutes"],
        "monthly_fee": row["monthly_fee"],
        "notify": bool(row["notify"]),
        "created_at": row["created_at"],
        "last_checked_at": last,
        "searches_per_month": _runs_per_month(row, len(keywords)),
        "stats": _stats(keywords),
    }
    if detail:
        view["keywords"] = keywords
    else:
        view["keywords"] = [
            {
                k: item[k]
                for k in (
                    "id",
                    "keyword",
                    "position",
                    "change",
                    "checked_at",
                    "entered",
                    "dropped",
                )
            }
            for item in keywords
        ]
    return view


def list_projects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    store.ensure_extras(connection)
    rows = connection.execute(
        "SELECT * FROM airaapi_rank_projects WHERE deleted_at IS NULL "
        "ORDER BY created_at DESC, rowid DESC"
    ).fetchall()
    return [project_view(connection, row, detail=False) for row in rows]


def get_project(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    return project_view(connection, _project_row(connection, project_id))


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _add_keyword(
    connection: sqlite3.Connection,
    project: Mapping[str, Any],
    keyword: str,
    moment: datetime,
) -> None:
    params = _search_params(project, keyword)
    stamp = store.to_iso(moment)
    saved_id = store.new_id()
    store.insert_saved(
        connection,
        {
            "id": saved_id,
            "name": f"Rank: {project['domain']} · {keyword}"[:120],
            "engine": ENGINE,
            "params_json": json.dumps(params, sort_keys=True, ensure_ascii=False),
            "tags": json.dumps([TAG]),
            "created_at": stamp,
        },
    )
    minutes = int(project["interval_minutes"] or 0)
    monitor_id = store.new_id()
    store.insert_monitor(
        connection,
        {
            "id": monitor_id,
            "saved_id": saved_id,
            # a by-hand project keeps a paused daily monitor so switching to daily later is one update
            "interval_minutes": minutes or 1440,
            "enabled": 1 if minutes else 0,
            "next_run_at": store.to_iso(moment + timedelta(minutes=minutes or 1440)),
            "notify_on_change": 0,  # the hook decides what is worth a notification
            "monthly_budget": 0,
            "pause_on_error": 0,
        },
    )
    connection.execute(
        "INSERT INTO airaapi_rank_keywords (id, project_id, keyword, saved_id, monitor_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (store.new_id(), project["id"], keyword, saved_id, monitor_id, stamp),
    )


def _remove_keyword(
    connection: sqlite3.Connection, row: Mapping[str, Any] | sqlite3.Row
) -> None:
    store.delete_monitor(connection, row["monitor_id"])
    store.delete_saved(connection, row["saved_id"])
    connection.execute(
        "DELETE FROM airaapi_rank_checks WHERE keyword_id = ?", (row["id"],)
    )
    connection.execute("DELETE FROM airaapi_rank_keywords WHERE id = ?", (row["id"],))


def _total_keywords(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) FROM airaapi_rank_keywords").fetchone()
    return int(row[0]) if row else 0


def _check_keyword_room(
    connection: sqlite3.Connection, adding: int, in_project: int, removing: int = 0
) -> None:
    """``adding`` new keywords while ``removing`` others in the same edit must fit the limits."""
    if in_project > MAX_KEYWORDS_PER_PROJECT:
        raise ValueError(
            f"A project can track up to {MAX_KEYWORDS_PER_PROJECT} keywords; split them into two projects."
        )
    if adding and _total_keywords(connection) - removing + adding > MAX_KEYWORDS_TOTAL:
        raise ValueError(
            f"You can track up to {MAX_KEYWORDS_TOTAL} keywords across all projects; remove some first."
        )


def _settings(
    payload: Mapping[str, Any], current: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Validated project fields from ``payload``; with ``current`` only the fields sent change."""
    base = dict(current or {})
    out: dict[str, Any] = {}

    def has(name: str) -> bool:
        return current is None or name in payload

    if has("domain"):
        out["domain"] = normalize_domain(payload.get("domain"))
    if has("name"):
        out["name"] = (
            _short(payload, "name", 120) or out.get("domain") or base.get("domain", "")
        )
    if has("client_name"):
        out["client_name"] = _short(payload, "client_name", 120)
    if has("location"):
        out["location"] = _short(payload, "location", 200)
    if has("gl"):
        out["gl"] = _code(payload, "gl", "Country")
    if has("hl"):
        out["hl"] = _code(payload, "hl", "Language")
    if has("device"):
        device = str(payload.get("device") or "").strip().lower()
        if device not in DEVICES:
            raise ValueError("Device must be desktop, mobile or tablet.")
        out["device"] = device
    if has("interval_minutes"):
        out["interval_minutes"] = _interval(payload.get("interval_minutes"))
    if has("monthly_fee"):
        out["monthly_fee"] = _fee(payload.get("monthly_fee"))
    if has("notify"):
        out["notify"] = 1 if _flag(payload.get("notify", True)) else 0
    return out


def create_project(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    store.ensure_extras(connection)
    moment = now or store.now_utc()
    fields = _settings({"notify": True, **payload}, None)
    keywords = _keywords(payload.get("keywords"))
    if not keywords:
        raise ValueError("Add at least one keyword, one per line.")
    count = connection.execute(
        "SELECT COUNT(*) FROM airaapi_rank_projects WHERE deleted_at IS NULL"
    ).fetchone()[0]
    if count >= MAX_PROJECTS:
        raise ValueError(
            f"You can have up to {MAX_PROJECTS} rank projects; delete one first."
        )
    _check_keyword_room(connection, len(keywords), len(keywords))
    project = {"id": store.new_id(), "created_at": store.to_iso(moment), **fields}
    for keyword in keywords:  # validate every search before writing anything
        _search_params(project, keyword)
    connection.execute(
        "INSERT INTO airaapi_rank_projects (id, name, domain, client_name, location, gl, hl, device, "
        "interval_minutes, monthly_fee, notify, created_at) "
        "VALUES (:id, :name, :domain, :client_name, :location, :gl, :hl, :device, "
        ":interval_minutes, :monthly_fee, :notify, :created_at)",
        project,
    )
    for keyword in keywords:
        _add_keyword(connection, project, keyword, moment)
    connection.commit()
    return get_project(connection, project["id"])


def update_project(
    connection: sqlite3.Connection,
    project_id: str,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or store.now_utc()
    current = dict(_project_row(connection, project_id))
    fields = _settings(payload, current)
    if not fields and "keywords" not in payload:
        raise ValueError("Send the fields to change.")
    merged = {**current, **fields}
    existing = _keyword_rows(connection, project_id)
    search_changed = any(
        name in fields and fields[name] != current[name]
        for name in ("domain", "location", "gl", "hl", "device")
    )
    wanted = (
        _keywords(payload["keywords"])
        if "keywords" in payload
        else [r["keyword"] for r in existing]
    )
    if not wanted:
        raise ValueError("A project needs at least one keyword.")
    have = {r["keyword"].casefold(): r for r in existing}
    adding = [k for k in wanted if k.casefold() not in have]
    keep = {k.casefold() for k in wanted}
    removing = [r for r in existing if r["keyword"].casefold() not in keep]
    # the same keyword with different capitals keeps its history and takes the new spelling
    respelled = {
        have[k.casefold()]["id"]: k
        for k in wanted
        if k.casefold() in have and have[k.casefold()]["keyword"] != k
    }
    _check_keyword_room(connection, len(adding), len(wanted), len(removing))
    for keyword in wanted:
        _search_params(merged, keyword)
    if fields:
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        connection.execute(
            f"UPDATE airaapi_rank_projects SET {assignments} WHERE id = :id",
            {**fields, "id": project_id},
        )
    for row in removing:
        _remove_keyword(connection, row)
    for keyword_id, spelling in respelled.items():
        connection.execute(
            "UPDATE airaapi_rank_keywords SET keyword = ? WHERE id = ?",
            (spelling, keyword_id),
        )
    for keyword in adding:
        _add_keyword(connection, merged, keyword, moment)
    # Settings that change the search or the schedule are written to every keyword's saved search and monitor.
    for row in _keyword_rows(connection, project_id):
        if row["keyword"].casefold() in {k.casefold() for k in adding}:
            continue
        if search_changed or row["id"] in respelled:
            params = _search_params(merged, row["keyword"])
            store.update_saved(
                connection,
                row["saved_id"],
                {
                    "params_json": json.dumps(
                        params, sort_keys=True, ensure_ascii=False
                    ),
                    "name": f"Rank: {merged['domain']} · {row['keyword']}"[:120],
                },
            )
        if (
            "interval_minutes" in fields
            and fields["interval_minutes"] != current["interval_minutes"]
        ):
            minutes = fields["interval_minutes"]
            store.update_monitor(
                connection,
                row["monitor_id"],
                {
                    "interval_minutes": minutes or 1440,
                    "enabled": 1 if minutes else 0,
                    "next_run_at": store.to_iso(
                        moment + timedelta(minutes=minutes or 1440)
                    ),
                },
            )
    if "domain" in fields and fields["domain"] != current["domain"]:
        # positions on the old website mean nothing for the new one
        connection.execute(
            "DELETE FROM airaapi_rank_checks WHERE project_id = ?", (project_id,)
        )
    connection.commit()
    return get_project(connection, project_id)


def delete_project(
    connection: sqlite3.Connection, project_id: str, *, now: datetime | None = None
) -> None:
    """Stop tracking: keywords, monitors, saved searches and positions go.

    The project row stays, hidden, with the time it was deleted, so the Earnings tab still bills the
    months it ran (its fee and the checks in the monthly ledger)."""
    _project_row(connection, project_id)
    for row in _keyword_rows(connection, project_id):
        _remove_keyword(connection, row)
    connection.execute(
        "DELETE FROM airaapi_rank_checks WHERE project_id = ?", (project_id,)
    )
    connection.execute(
        "UPDATE airaapi_rank_projects SET deleted_at = ? WHERE id = ?",
        (store.to_iso(now or store.now_utc()), project_id),
    )
    connection.commit()


def run_project(
    connection: sqlite3.Connection,
    client: Any,
    project_id: str,
    *,
    keyword_id: str = "",
    notify: service.Notifier | None = None,
    limits: service.Limits | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check every keyword now (or one), each a live search. Failures are reported per keyword."""
    _project_row(connection, project_id)
    rows = _keyword_rows(connection, project_id)
    if keyword_id:
        rows = [r for r in rows if r["id"] == keyword_id]
        if not rows:
            raise LookupError("Keyword not found in this project.")
    outcomes: list[dict[str, Any]] = []
    credits = 0
    for row in rows:
        try:
            outcome = service.run_monitor(
                connection,
                client,
                row["monitor_id"],
                now=now,
                notify=notify,
                limits=limits,
            )
        except SerpApiError as failure:
            outcomes.append(
                {
                    "keyword": row["keyword"],
                    "ok": False,
                    "error": failure.message,
                    "code": failure.code,
                }
            )
            if failure.code in {"not_configured", "invalid_api_key", "out_of_searches"}:
                break  # every other keyword would fail the same way
            continue
        except (ValueError, LookupError) as failure:
            outcomes.append(
                {"keyword": row["keyword"], "ok": False, "error": str(failure)}
            )
            continue
        record = outcome.get("record") or {}
        credits += int(record.get("credits") or 0)
        outcomes.append(
            {
                "keyword": row["keyword"],
                "ok": True,
                "summary": outcome.get("summary", ""),
            }
        )
    failed = [o for o in outcomes if not o["ok"]]
    if outcomes and len(failed) == len(outcomes):
        first = failed[0]
        if first.get("code"):
            raise SerpApiError(first["code"], first["error"])
        raise ValueError(first["error"])
    return {
        "project": get_project(connection, project_id),
        "results": outcomes,
        "credits": credits,
        "failed": len(failed),
    }


# ---------------------------------------------------------------------------
# the monitor hook
# ---------------------------------------------------------------------------


def find_position(result: Any, domain: str) -> dict[str, Any]:
    """The first organic result on ``domain``: position, url, title, how deep the page went, who ranked above."""
    organic = result.get("organic_results") if isinstance(result, Mapping) else None
    items = [item for item in organic or [] if isinstance(item, Mapping)]
    found: dict[str, Any] = {
        "position": None,
        "url": "",
        "title": "",
        "depth": len(items),
        "competitors": [],
    }
    above: list[str] = []
    for index, item in enumerate(items):
        link = item.get("link") or ""
        try:
            position = int(item.get("position") or index + 1)
        except (TypeError, ValueError):
            position = index + 1
        found["depth"] = max(found["depth"], position)
        if on_domain(link, domain):
            found.update(
                position=position,
                url=str(link)[:500],
                title=str(item.get("title") or "")[:300],
            )
            break
        host = host_of(link)
        if host and host not in above:
            above.append(host)
    found["competitors"] = above[:3]
    return found


def _label(position: int | None, depth: int) -> str:
    return f"#{position}" if position is not None else f"not in the top {depth or 10}"


def process_monitor_run(
    connection: sqlite3.Connection,
    monitor: sqlite3.Row,
    record: dict[str, Any],
    notify: service.Notifier | None,
) -> str | None:
    """Monitor hook: record where the project's website ranked; notify on notable moves."""
    store.ensure_extras(connection)
    keyword = connection.execute(
        "SELECT * FROM airaapi_rank_keywords WHERE monitor_id = ?", (monitor["id"],)
    ).fetchone()
    if keyword is None:
        return None
    project = connection.execute(
        "SELECT * FROM airaapi_rank_projects WHERE id = ? AND deleted_at IS NULL",
        (keyword["project_id"],),
    ).fetchone()
    if project is None:
        return None
    result = record.get("result")
    if result is None and record.get("id"):
        try:
            result = service.get_search(connection, record["id"]).get("result")
        except LookupError:
            result = None
    if not isinstance(result, Mapping):
        return "No results to read the position from"
    found = find_position(result, project["domain"])
    previous = connection.execute(
        "SELECT position FROM airaapi_rank_checks WHERE keyword_id = ? ORDER BY checked_at DESC, rowid DESC LIMIT 1",
        (keyword["id"],),
    ).fetchone()
    checked_at = str(record.get("created_at") or store.to_iso(store.now_utc()))
    store.bump_rank_usage(connection, project["id"], checked_at[:7])
    connection.execute(
        "INSERT INTO airaapi_rank_checks (id, project_id, keyword_id, checked_at, position, url, title, "
        "depth, search_id, competitors) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            store.new_id(),
            project["id"],
            keyword["id"],
            checked_at,
            found["position"],
            found["url"],
            found["title"],
            found["depth"],
            record.get("id") or "",
            json.dumps(found["competitors"]),
        ),
    )
    connection.commit()
    now_label = _label(found["position"], found["depth"])
    if previous is None:
        return f"First check: {now_label}"
    before = previous["position"]
    after = found["position"]
    if before == after:
        return f"Unchanged at {now_label}"
    summary = f"{_label(before, found['depth'])} → {now_label}"
    notable = (
        (before is None) != (after is None)
        or (
            after is not None
            and before is not None
            and abs(before - after) >= NOTABLE_MOVE
        )
        or (after is not None and after <= 3 and (before is None or before > 3))
    )
    if notable and project["notify"] and notify is not None:
        better = after is not None and (before is None or after < before)
        notify(
            f"{project['domain']} {'climbed' if better else 'slipped'} for “{keyword['keyword']}”",
            f"{summary} on Google.",
            f"#/api?tab=rank&project={project['id']}",
        )
    return summary


service.register_monitor_hook(process_monitor_run)


# ---------------------------------------------------------------------------
# client report
# ---------------------------------------------------------------------------


def _fmt_day(iso: str | None) -> str:
    return iso[:10] if iso else ""


def report(
    connection: sqlite3.Connection,
    project_id: str,
    fmt: str = "html",
    *,
    prepared_by: str = "",
    now: datetime | None = None,
) -> tuple[bytes, str, str]:
    """(body, content type, file name) of the client report: every keyword, its position and movement."""
    project = get_project(connection, project_id)
    moment = now or store.now_utc()
    slug = re.sub(r"[^a-z0-9]+", "-", project["domain"]).strip("-") or "project"
    day = moment.strftime("%Y-%m-%d")
    keywords = project["keywords"]
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "keyword",
                "position",
                "previous",
                "change",
                "best",
                "ranking page",
                "checked",
                "above you",
            ]
        )
        for k in keywords:
            writer.writerow(
                [
                    _safe_cell(k["keyword"]),
                    "" if k["position"] is None else k["position"],
                    "" if k["previous"] is None else k["previous"],
                    "" if k["change"] is None else k["change"],
                    "" if k["best"] is None else k["best"],
                    _safe_cell(k["url"]),
                    _fmt_day(k["checked_at"]),
                    _safe_cell(", ".join(k["competitors"])),
                ]
            )
        return (
            out.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"rank-report-{slug}-{day}.csv",
        )
    if fmt != "html":
        raise ValueError("Report format must be csv or html.")
    return (
        _html_report(project, prepared_by, moment).encode("utf-8"),
        "text/html; charset=utf-8",
        f"rank-report-{slug}-{day}.html",
    )


def _safe_cell(text: str) -> str:
    """Spreadsheet programs run cells that start with = + - @ as formulas; keep them as text."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _html_report(project: Mapping[str, Any], prepared_by: str, moment: datetime) -> str:
    e = html.escape
    stats = project["stats"]

    def move(k: Mapping[str, Any]) -> str:
        if k["entered"]:
            return '<span class="up">new</span>'
        if k["dropped"]:
            return '<span class="down">dropped out</span>'
        change = k["change"]
        if not change:
            return "–"
        return f'<span class="{"up" if change > 0 else "down"}">{"▲" if change > 0 else "▼"} {abs(change)}</span>'

    rows = "".join(
        "<tr>"
        f"<td>{e(k['keyword'])}</td>"
        f"<td class=n>{'' if k['position'] is None else k['position']}{'' if k['position'] is not None or not k['checked_at'] else 'not found'}</td>"
        f"<td class=n>{move(k)}</td>"
        f"<td class=n>{'' if k['best'] is None else k['best']}</td>"
        f"<td class=u>{e(k['url'])}</td>"
        f"<td>{e(_fmt_day(k['checked_at']))}</td>"
        "</tr>"
        for k in project["keywords"]
    )
    where = (
        ", ".join(
            part
            for part in (
                project["location"],
                f"country {project['gl']}" if project["gl"] else "",
                f"language {project['hl']}" if project["hl"] else "",
                project["device"],
            )
            if part
        )
        or "default location"
    )
    avg = "–" if stats["average"] is None else stats["average"]
    client = (
        f"Prepared for {e(project['client_name'])}" if project["client_name"] else ""
    )
    by = f"by {e(prepared_by)}" if prepared_by else ""
    subtitle = " ".join(part for part in (client, by) if part)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Search ranking report: {e(project["domain"])}</title>
<style>
body{{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1d2a24;margin:0;padding:24px;background:#f6f8f6}}
main{{max-width:960px;margin:0 auto;background:#fff;border:1px solid #dfe6e1;border-radius:12px;padding:24px}}
h1{{font-size:24px;margin:0 0 4px}} .muted{{color:#5b6b63}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:20px 0}}
.card{{border:1px solid #dfe6e1;border-radius:10px;padding:12px}} .card b{{display:block;font-size:24px}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;padding:8px;border-bottom:1px solid #e6ece8;vertical-align:top}}
td.n{{white-space:nowrap}} td.u{{word-break:break-all;font-size:13px;color:#5b6b63}}
.up{{color:#13723f;font-weight:600}} .down{{color:#a12a2a;font-weight:600}}
@media print{{body{{background:#fff;padding:0}} main{{border:0}}}}
</style></head><body><main>
<h1>Search ranking report: {e(project["domain"])}</h1>
<p class="muted">{subtitle + " · " if subtitle else ""}{e(moment.strftime("%d %B %Y").lstrip("0"))} · Google, {e(where)}</p>
<div class="cards">
<div class="card"><b>{stats["top3"]}</b>in the top 3</div>
<div class="card"><b>{stats["top10"]}</b>on the first page</div>
<div class="card"><b>{avg}</b>average position</div>
<div class="card"><b>{stats["improved"]}</b>improved since last check</div>
<div class="card"><b>{stats["declined"]}</b>declined</div>
</div>
<table><thead><tr><th>Keyword</th><th>Position</th><th>Change</th><th>Best</th><th>Ranking page</th><th>Checked</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="muted">Position is the website's first organic result on Google for the keyword. “Not found” means it was not in the results checked.</p>
</main></body></html>
"""
