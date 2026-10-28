"""Developers app: rank tracker, reselling API keys, earnings, and the console fixes found in the browser walk.

Runs the real request handler against the stand-in SerpApi server (``tests/serpapi_stub.py``)
exactly like ``test_airaapi_routes.py`` (its fixtures are reused). Nothing here touches the
network or a real SerpApi account. The stub's Google fixture ranks example.com at positions
1 to 5; ``stub.variant = -n`` drops the first n results, so example.com slips to n + 1.
"""

# ruff: noqa: F811  (the fixtures imported from test_airaapi_routes are parameters here)
from __future__ import annotations

import csv
import io
import re
import sqlite3
from pathlib import Path

import pytest

from aira.airaapi import compat, rank, service, store
from aira.airaapi import routes as airaapi_routes
from aira.apps import APPS
from test_airaapi_routes import (  # noqa: F401  (pytest fixtures)
    KEY,
    api,
    connected,
    env,
)

ROOT = Path(__file__).resolve().parents[1]


def _notifications(api_client) -> list[dict]:
    status, body, _ = api_client.get("/api/notifications")
    assert status == 200, body
    return body.get("notifications") or body.get("items") or []


def _new_project(api_client, **extra) -> dict:
    body = {
        "name": "Example shop",
        "domain": "https://www.Example.com/shop",
        "client_name": "Example Ltd",
        "keywords": "coffee beans\nespresso machine\nCoffee Beans",
        "interval_minutes": 0,
        "monthly_fee": 150,
        **extra,
    }
    return api_client.ok("POST", "/api/airaapi/rank", body, expect=201)


# -- fixes found in the browser walk ------------------------------------------------------


def test_saving_the_result_you_just_ran_records_that_run(connected):
    record = connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "coffee"}}
    )
    saved = connected.ok(
        "POST",
        "/api/airaapi/saved",
        {
            "name": "Coffee",
            "engine": "google",
            "params": {"q": "coffee"},
            "from_search_id": record["id"],
        },
        expect=201,
    )
    assert saved["last_search_id"] == record["id"]
    assert saved["last_run_at"] == record["created_at"]
    # a run of different parameters is not this saved search's run
    other = connected.ok(
        "POST",
        "/api/airaapi/saved",
        {
            "name": "Tea",
            "engine": "google",
            "params": {"q": "tea"},
            "from_search_id": record["id"],
        },
        expect=201,
    )
    assert other["last_run_at"] is None and other["last_search_id"] is None


def test_playground_remembers_parameters_it_runs_and_enter_saves():
    source = (ROOT / "auto_apply_assistant" / "views-playground.js").read_text(
        encoding="utf-8"
    )
    run = source[source.index("async function run(") :]
    run = run[: run.index("const token = ++S.runToken")]
    # parameters from a shared link never fire onChange; running them must remember them
    assert "rememberParams(S.engineId, params)" in run
    dialog = source[source.index("function openSaveDialog(") :]
    dialog = dialog[: dialog.index("// ---- compare")]
    assert 'event.key === "Enter"' in dialog and "primary.click()" in dialog
    assert "from_search_id" in dialog


# -- rank tracker -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"domain": "not a website", "keywords": "x"}, "website"),
        ({"domain": "example.com", "keywords": ""}, "keyword"),
        (
            {"domain": "example.com", "keywords": "x", "interval_minutes": 5},
            "how often",
        ),
        ({"domain": "example.com", "keywords": "x", "monthly_fee": "lots"}, "fee"),
        ({"domain": "example.com", "keywords": "x", "gl": "United States"}, "Country"),
        ({"domain": "example.com", "keywords": "x", "device": "watch"}, "Device"),
    ],
)
def test_rank_project_rejects_wrong_input(api, body, message):
    status, payload, _ = api.post("/api/airaapi/rank", body)
    assert status == 400, payload
    assert message.lower() in payload["error"].lower()


def test_rank_project_is_saved_searches_with_monitors_that_the_console_protects(
    connected,
):
    project = _new_project(connected)
    assert project["domain"] == "example.com"
    assert [k["keyword"] for k in project["keywords"]] == [
        "coffee beans",
        "espresso machine",
    ]  # duplicates in another case are dropped
    assert project["searches_per_month"] == 0 and project["stats"]["checked"] == 0
    listing = connected.ok("GET", "/api/airaapi/rank")
    assert [p["id"] for p in listing["projects"]] == [project["id"]]
    saved = connected.ok("GET", "/api/airaapi/saved")["saved"]
    assert len(saved) == 2 and all(s["rank_tracker"] for s in saved)
    assert all(s["engine"] == "google" for s in saved)
    monitors = connected.ok("GET", "/api/airaapi/monitors")["monitors"]
    assert len(monitors) == 2
    assert all(m["rank_tracker"] and not m["enabled"] for m in monitors)
    # the generic Monitors tab cannot orphan a project's keyword
    status, body, _ = connected.call("DELETE", f"/api/airaapi/saved/{saved[0]['id']}")
    assert status == 400 and "rank" in body["error"]
    status, body, _ = connected.call(
        "DELETE", f"/api/airaapi/monitors/{monitors[0]['id']}"
    )
    assert status == 400 and "rank" in body["error"]
    status, body, _ = connected.post(
        "/api/airaapi/monitors", {"saved_id": saved[0]["id"], "interval_minutes": 60}
    )
    assert status == 400


def test_rank_check_finds_the_position_tracks_moves_and_notifies(env, connected):
    project = _new_project(connected, keywords="coffee beans")
    first = connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    keyword = first["project"]["keywords"][0]
    assert keyword["position"] == 1
    assert keyword["url"].startswith("https://example.com/")
    assert keyword["checks"] == 1 and keyword["change"] is None
    assert first["credits"] == 1 and first["failed"] == 0
    env.stub.variant = -3  # example.com drops to #4
    second = connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    keyword = second["project"]["keywords"][0]
    assert (keyword["position"], keyword["previous"], keyword["change"]) == (4, 1, -3)
    assert keyword["best"] == 1
    assert second["project"]["stats"]["declined"] == 1
    assert env.stub.search_calls == 2  # rank checks always run live
    titles = [n.get("title", "") for n in _notifications(connected)]
    assert any("example.com slipped" in title for title in titles), titles
    # the monitor's summary says what happened, in words
    monitors = connected.ok("GET", "/api/airaapi/monitors")["monitors"]
    assert "#1 → #4" in monitors[0]["last_change_summary"]


def test_rank_reports_download_as_safe_csv_and_html(connected):
    project = _new_project(
        connected,
        keywords="<script>alert(1)</script>\n=cmd()",
        client_name="Bob & Sons",
    )
    connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    status, body, headers = connected.call(
        "GET", f"/api/airaapi/rank/{project['id']}/report?format=csv", raw=True
    )
    assert status == 200 and "attachment" in headers.get("Content-Disposition", "")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows[0][0] == "keyword"
    assert "'=cmd()" in [row[0] for row in rows[1:]]  # never a spreadsheet formula
    status, body, headers = connected.call(
        "GET", f"/api/airaapi/rank/{project['id']}/report?format=html", raw=True
    )
    page = body.decode("utf-8")
    assert status == 200 and headers.get("Content-Type", "").startswith("text/html")
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "Prepared for Bob &amp; Sons" in page
    status, _, _ = connected.call(
        "GET", f"/api/airaapi/rank/{project['id']}/report?format=pdf"
    )
    assert status == 400


def test_rank_project_edits_keywords_schedule_and_domain(connected):
    project = _new_project(connected)
    connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    edited = connected.ok(
        "PATCH",
        f"/api/airaapi/rank/{project['id']}",
        {"keywords": ["coffee beans", "grinder"], "interval_minutes": 1440},
    )
    assert [k["keyword"] for k in edited["keywords"]] == ["coffee beans", "grinder"]
    assert edited["keywords"][0]["checks"] == 1  # kept keywords keep their history
    assert edited["searches_per_month"] == 60
    monitors = connected.ok("GET", "/api/airaapi/monitors")["monitors"]
    assert len(monitors) == 2 and all(
        m["enabled"] and m["interval_minutes"] == 1440 for m in monitors
    )
    moved = connected.ok(
        "PATCH",
        f"/api/airaapi/rank/{project['id']}",
        {"domain": "other.example.org", "location": "Austin, Texas, United States"},
    )
    assert moved["domain"] == "other.example.org"
    assert all(
        k["checks"] == 0 for k in moved["keywords"]
    )  # old site's positions are gone
    saved = connected.ok("GET", "/api/airaapi/saved")["saved"]
    assert all(
        s["params"].get("location") == "Austin, Texas, United States" for s in saved
    )
    after = connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    assert all(k["position"] is None for k in after["project"]["keywords"])
    assert after["project"]["stats"]["not_found"] == 2
    connected.ok("DELETE", f"/api/airaapi/rank/{project['id']}")
    assert connected.ok("GET", "/api/airaapi/saved")["saved"] == []
    assert connected.ok("GET", "/api/airaapi/monitors")["monitors"] == []
    status, _, _ = connected.call("GET", f"/api/airaapi/rank/{project['id']}")
    assert status == 404


def test_scheduled_rank_checks_run_through_the_monitor_scheduler(env, connected):
    project = _new_project(connected, keywords="coffee beans", interval_minutes=1440)
    with env.user_db() as connection:
        connection.execute(
            "UPDATE airaapi_monitors SET next_run_at = '2000-01-01T00:00:00Z'"
        )
        connection.commit()
    notes: list[tuple[str, str, str]] = []
    with env.user_db() as connection:
        connection.row_factory = sqlite3.Row
        results = service.run_due_monitors(
            connection,
            airaapi_routes.make_client(KEY),
            notify=lambda *args: notes.append(args),
        )
    assert results and results[0]["ok"] and results[0]["summary"] == "First check: #1"
    detail = connected.ok("GET", f"/api/airaapi/rank/{project['id']}")
    assert detail["keywords"][0]["position"] == 1
    assert notes == []  # a first check is a baseline, not news


def test_rank_keywords_have_their_own_limit_and_leave_monitor_room(
    connected, monkeypatch
):
    monkeypatch.setattr(rank, "MAX_KEYWORDS_TOTAL", 3)
    monkeypatch.setattr(service, "MAX_MONITORS", 1)
    _new_project(connected, keywords="a\nb")
    status, body, _ = connected.post(
        "/api/airaapi/rank", {"domain": "example.org", "keywords": "c\nd"}
    )
    assert status == 400 and "up to 3 keywords" in body["error"]
    # rank monitors do not use up the ordinary monitor limit
    saved = connected.ok(
        "POST",
        "/api/airaapi/saved",
        {"name": "Mine", "engine": "google", "params": {"q": "tea"}},
        expect=201,
    )
    connected.ok(
        "POST",
        "/api/airaapi/monitors",
        {"saved_id": saved["id"], "interval_minutes": 60},
        expect=201,
    )


def test_rank_run_without_a_serpapi_key_says_so(api):
    project = _new_project(api)
    status, body, _ = api.post(f"/api/airaapi/rank/{project['id']}/run", {})
    assert status == 409 and body["code"] == "not_configured"


def test_find_position_matches_subdomains_but_not_lookalikes():
    result = {
        "organic_results": [
            {"position": 1, "link": "https://notexample.com/a"},
            {"position": 2, "link": "https://example.com.evil.net/b"},
            {"position": 3, "link": "https://shop.example.com/c", "title": "Shop"},
        ]
    }
    found = rank.find_position(result, "example.com")
    assert found["position"] == 3 and found["title"] == "Shop"
    assert found["competitors"] == ["notexample.com", "example.com.evil.net"]
    assert rank.find_position({}, "example.com")["position"] is None
    assert rank.normalize_domain("HTTPS://WWW.Example.COM/path?q=1") == "example.com"
    with pytest.raises(ValueError):
        rank.normalize_domain("localhost")


# -- reselling keys and earnings ---------------------------------------------------------


def _client_key(api_client, **terms) -> tuple[dict, str]:
    body = {"name": "Acme scraper", "hourly_limit": 0, **terms}
    created = api_client.ok("POST", "/api/airaapi/keys", body, expect=201)
    return created["key"], created["secret"]


def test_client_key_terms_are_enforced_on_v1(env, connected):
    key, secret = _client_key(
        connected,
        client_name="Acme Ltd",
        price_per_1000=5,
        monthly_quota=2,
        allowed_engines="google, google_jobs",
    )
    assert key["client_name"] == "Acme Ltd" and key["price_per_1000"] == 5
    assert key["allowed_engines"] == ["google", "google_jobs"]
    status, body, _ = env.v1("/v1/search.json?engine=bing&q=tea", key=secret)
    assert status == 403 and body["code"] == "engine_not_allowed"
    assert env.stub.search_calls == 0
    assert env.v1("/v1/search.json?engine=google&q=tea", key=secret)[0] == 200
    # the second identical call is a cache hit: free for you, still a billable call
    status, body, _ = env.v1("/v1/search.json?engine=google&q=tea", key=secret)
    assert status == 200 and body["search_metadata"]["aira"]["cache_hit"] is True
    status, body, _ = env.v1("/v1/search.json?engine=google&q=coffee", key=secret)
    assert status == 429 and body["code"] == "quota_exceeded"
    assert "2 searches for this month" in body["error"]
    listed = connected.ok("GET", "/api/airaapi/keys")["keys"][0]
    assert listed["used_this_month"] == 2 and listed["quota_left"] == 0
    updated = connected.ok(
        "PATCH",
        f"/api/airaapi/keys/{key['id']}",
        {"monthly_quota": 0, "allowed_engines": [], "price_per_1000": 7.5},
    )["key"]
    assert updated["monthly_quota"] == 0 and updated["allowed_engines"] == []
    assert env.v1("/v1/search.json?engine=bing&q=tea", key=secret)[0] == 200


@pytest.mark.parametrize(
    "terms",
    [
        {"price_per_1000": "cheap"},
        {"price_per_1000": -1},
        {"monthly_quota": 1.5},
        {"monthly_quota": -3},
        {"allowed_engines": "google, no_such_engine"},
        {"client_name": "x" * 121},
    ],
)
def test_client_key_terms_reject_wrong_input(connected, terms):
    status, body, _ = connected.post(
        "/api/airaapi/keys", {"name": "Bad", "hourly_limit": 0, **terms}
    )
    assert status == 400, body
    key, _ = _client_key(connected)
    status, body, _ = connected.call("PATCH", f"/api/airaapi/keys/{key['id']}", terms)
    assert status == 400, body
    status, _, _ = connected.call("PATCH", f"/api/airaapi/keys/{key['id']}", {})
    assert status == 400


def test_earnings_add_up_client_keys_and_rank_fees(env, connected):
    settings = connected.ok(
        "PUT",
        "/api/airaapi/earnings/settings",
        {"currency": "eur", "cost_per_search": 0.01, "business_name": "Data Co"},
    )["settings"]
    assert settings == {
        "currency": "EUR",
        "cost_per_search": 0.01,
        "business_name": "Data Co",
    }
    key, secret = _client_key(connected, client_name="Acme Ltd", price_per_1000=20)
    for query in ("tea", "tea", "coffee"):
        assert env.v1(f"/v1/search.json?engine=google&q={query}", key=secret)[0] == 200
    env.v1("/v1/search.json?engine=google&q=__server_error__", key=secret)
    _client_key(connected, name="My laptop")  # a personal key is not a client line
    project = _new_project(connected, keywords="coffee beans", monthly_fee=150)
    connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    report = connected.ok("GET", "/api/airaapi/earnings")
    [line] = report["keys"]
    assert (line["calls"], line["cached"], line["errors"], line["credits"]) == (
        3,
        1,
        1,
        2,
    )
    assert line["billed"] == 0.06 and line["cost"] == 0.02 and line["margin"] == 0.04
    [fee] = report["rank"]
    assert fee["checks"] == 1 and fee["billed"] == 150 and fee["cost"] == 0.01
    assert report["totals"]["billed"] == 150.06
    assert report["totals"]["margin"] == 150.03
    assert report["totals"]["clients"] == 2 and report["cost_known"] is True
    month = report["month"]
    assert re.fullmatch(r"\d{4}-\d{2}", month) and month in report["months"]
    status, body, headers = connected.call(
        "GET", f"/api/airaapi/earnings/statement/{key['id']}?format=csv", raw=True
    )
    assert status == 200 and f"{month}.csv" in headers.get("Content-Disposition", "")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    total = next(row for row in rows if row and row[0] == "total")
    assert total[1] == "3" and total[-1] == "0.06"
    status, body, _ = connected.call(
        "GET", f"/api/airaapi/earnings/statement/{key['id']}?format=html", raw=True
    )
    assert status == 200 and "Acme Ltd" in body.decode() and "Data Co" in body.decode()
    # a month before the key and the project existed is an empty report, not an error
    empty = connected.ok("GET", "/api/airaapi/earnings?month=2001-01")
    assert empty["totals"]["billed"] == 0 and empty["totals"]["clients"] == 0
    assert empty["keys"] == [] and empty["rank"] == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/airaapi/earnings?month=2026-13", None),
        ("PUT", "/api/airaapi/earnings/settings", {"currency": "dollars"}),
        ("PUT", "/api/airaapi/earnings/settings", {"cost_per_search": "free"}),
        ("PUT", "/api/airaapi/earnings/settings", {"cost_per_search": 500}),
    ],
)
def test_earnings_reject_wrong_input(connected, method, path, body):
    status, payload, _ = connected.call(method, path, body)
    assert status == 400, payload


def test_statement_for_an_unknown_key_is_404(connected):
    status, _, _ = connected.call("GET", "/api/airaapi/earnings/statement/" + "0" * 32)
    assert status == 404


# -- schema, catalog and hosted gating ----------------------------------------------------


def test_an_older_database_gains_the_new_tables_and_key_columns(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE airaapi_api_keys (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                last_used_at TEXT, revoked_at TEXT, hourly_limit INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.execute(
            "INSERT INTO airaapi_api_keys (id, name, prefix, key_hash, created_at) "
            "VALUES ('k', 'Old', 'aira_ab', 'h', '2026-01-01T00:00:00Z')"
        )
        connection.commit()
    with sqlite3.connect(path) as connection:
        [row] = store.list_api_keys(connection)
        view = compat.key_view(row)
        assert view["client_name"] == "" and view["allowed_engines"] == []
        for table in store.EXTRA_TABLES:
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone()


def test_new_routes_are_inside_the_developers_app_so_hosted_billing_gates_them():
    developers = next(entry for entry in APPS if entry.id == "api")
    for path in (
        "/api/airaapi/rank",
        "/api/airaapi/earnings",
        "/api/airaapi/earnings/settings",
        "/api/airaapi/keys/" + "0" * 32,
    ):
        assert path.startswith(developers.route_prefixes), path


def test_rank_hook_is_registered_once():
    assert service.MONITOR_HOOKS.count(rank.process_monitor_run) == 1


def test_rank_history_is_capped(connected, monkeypatch):
    monkeypatch.setattr(rank, "HISTORY_POINTS", 2)
    project = _new_project(connected, keywords="coffee beans")
    for _ in range(3):
        connected.ok(
            "POST",
            f"/api/airaapi/rank/{project['id']}/run",
            {"keyword_id": project["keywords"][0]["id"]},
        )
    detail = connected.ok("GET", f"/api/airaapi/rank/{project['id']}")
    assert len(detail["keywords"][0]["history"]) == 2
