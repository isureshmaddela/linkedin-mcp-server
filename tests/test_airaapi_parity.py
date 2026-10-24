"""SerpApi dashboard parity: search inside stored results, XLSX and Markdown export,
monitor budgets and pause on error, and X-Ray (pixel positions mapped to JSON paths).

Service tests use an in-memory database and the fake client from the service
tests; the HTTP journeys use the real request handler with the stand-in SerpApi
server, like ``test_airaapi_routes.py``.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from aira import schema
from aira.airaapi import service, store, xlsx
from aira.airaapi.client import RawResponse, SerpApiError
from serpapi_stub import with_pixel_positions
from test_airaapi_routes import api, connected, env  # noqa: F401  (fixtures)
from test_airaapi_service import NOW, FakeClient, fixture_body, make_monitor, run


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def fake():
    return FakeClient()


# -- search inside stored results ----------------------------------------------------


def test_text_inside_results_is_found_only_when_asked(db, fake):
    record = run(db, fake, {"q": "coffee"})
    plain = service.list_searches(db, text="short description")
    assert plain["total"] == 0
    found = service.list_searches(db, text="short description", in_results=True)
    assert [item["id"] for item in found["items"]] == [record["id"]]
    excerpt = found["items"][0]["match_excerpt"]
    assert store.SNIPPET_OPEN in excerpt and store.SNIPPET_CLOSE in excerpt
    assert "description" in excerpt.lower()
    # metadata matches still work with the switch on, and need no excerpt
    by_query = service.list_searches(db, text="coffee", in_results=True)
    assert by_query["total"] == 1


def test_prefix_and_multiword_matching(db, fake):
    run(db, fake, {"q": "coffee"})
    assert service.list_searches(db, text="descr", in_results=True)["total"] == 1
    assert (
        service.list_searches(db, text="fixtures short", in_results=True)["total"] == 1
    )
    assert (
        service.list_searches(db, text="fixtures zebra", in_results=True)["total"] == 0
    )


@pytest.mark.parametrize(
    "text", ['"', "NEAR(a b)", "* OR -", "a:b", "(((", "' OR 1=1 --", "%_\\"]
)
def test_search_syntax_in_user_text_never_errors(db, fake, text):
    run(db, fake, {"q": "coffee"})
    outcome = service.list_searches(db, text=text, in_results=True)
    assert outcome["total"] >= 0


def test_request_metadata_and_data_uris_are_not_indexed():
    body = {
        "search_metadata": {"id": "zzsecretid"},
        "search_parameters": {"q": "paramword"},
        "organic_results": [
            {"title": "Visible title", "thumbnail": "data:image/png;base64,AAAA"},
            {"title": "Second", "rectangle": {"x": 1, "y": 2, "width": 3, "height": 4}},
        ],
    }
    text = store.result_text(body)
    assert "Visible title" in text and "Second" in text
    assert "zzsecretid" not in text and "paramword" not in text and "data:" not in text
    assert len(store.result_text({"r": ["x" * 5000] * 200}, limit=1000)) == 1000


def test_cache_hit_copies_are_searchable(db, fake):
    run(db, fake, {"q": "coffee"})
    cached = run(db, fake, {"q": "coffee"}, now=NOW + timedelta(minutes=1))
    assert cached["cache_hit"]
    found = service.list_searches(db, text="short description", in_results=True)
    assert found["total"] == 2


def test_deleting_and_pruning_remove_index_rows(db, fake):
    first = run(db, fake, {"q": "coffee"})
    second = run(db, fake, {"q": "tea"}, now=NOW + timedelta(minutes=1))
    count = lambda: db.execute("SELECT COUNT(*) FROM airaapi_search_text").fetchone()[0]  # noqa: E731
    assert count() == 2
    service.delete_search(db, first["id"])
    assert count() == 1
    store.prune(db, retention_days=1, max_records=0, now=NOW + timedelta(days=5))
    assert count() == 0
    assert store.get_search_row(db, second["id"]) is None


def test_backfill_indexes_results_stored_before_the_index(db, fake):
    record = run(db, fake, {"q": "coffee"})
    db.execute("DELETE FROM airaapi_search_text")
    assert (
        service.list_searches(db, text="short description", in_results=True)["total"]
        == 0
    )
    assert store.backfill_text_index(db) == 1
    assert store.backfill_text_index(db) == 0  # idempotent
    found = service.list_searches(db, text="short description", in_results=True)
    assert found["items"][0]["id"] == record["id"]


def test_like_fallback_when_fts5_is_missing(fake):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE airaapi_search_text (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
    )
    store.ensure_schema(connection)
    run(connection, fake, {"q": "coffee"})
    found = service.list_searches(connection, text="Short Description", in_results=True)
    assert found["total"] == 1
    assert store.SNIPPET_OPEN in found["items"][0]["match_excerpt"]
    connection.close()


def test_migration_15_is_registered_and_idempotent():
    connection = sqlite3.connect(":memory:")
    applied = schema.migrate(connection)
    assert 15 in applied and schema.schema_version(connection) == schema.LATEST_VERSION
    assert schema.migrate(connection) == []
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(airaapi_monitors)")
    }
    assert {"monthly_budget", "budget_month", "month_runs", "pause_on_error"} <= columns
    connection.close()


# -- XLSX and Markdown export ------------------------------------------------------------


def _sheet(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml"} <= names
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


def test_xlsx_writer_keeps_formulas_as_text_and_numbers_numeric():
    data = xlsx.workbook(
        "results: [1]",
        ["title", "position"],
        [['=HYPERLINK("x")', "3"], ["a & <b>", ""]],
    )
    sheet = _sheet(data)
    assert "<f>" not in sheet
    assert "=HYPERLINK(&quot;x&quot;)" in sheet or '=HYPERLINK("x")' in sheet
    assert '<c r="B2"><v>3</v></c>' in sheet
    assert "a &amp; &lt;b&gt;" in sheet
    assert '<c r="B3"' not in sheet  # empty cells are omitted
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert 'name="results 1"' in archive.read("xl/workbook.xml").decode()
    assert xlsx._column_name(0) == "A" and xlsx._column_name(26) == "AA"


def test_search_exports_as_xlsx_and_markdown(db, fake):
    record = run(db, fake, {"q": "coffee"})
    body, kind, name = service.export_search(db, record["id"], "xlsx")
    assert kind.startswith("application/vnd.openxmlformats") and name.endswith(".xlsx")
    sheet = _sheet(body)
    assert "Coffee - Example Source 1" in sheet and "snippet" in sheet
    text, kind, name = service.export_search(db, record["id"], "md")
    markdown = text.decode("utf-8")
    assert kind.startswith("text/markdown") and name.endswith(".md")
    assert markdown.startswith("# Google Search: coffee")
    assert "| # | " in markdown and "| 1 | " in markdown
    with pytest.raises(ValueError, match="xlsx, md"):
        service.export_search(db, record["id"], "pdf")


def test_markdown_escapes_pipes_and_caps_columns():
    result = {
        "organic_results": [
            {f"field{i}": f"value|{i}" for i in range(12)} for _ in range(2)
        ]
    }
    markdown = service.results_to_markdown(
        {
            "engine": "google",
            "params": {"q": "a|b"},
            "created_at": "2026-10-22T12:00:00Z",
        },
        result,
    )
    assert "a\\|b" in markdown and "value\\|0" in markdown
    assert "Showing 8 of 12 columns" in markdown
    header = next(line for line in markdown.splitlines() if line.startswith("| # |"))
    assert header.count("|") == 10


# -- monitor budgets ----------------------------------------------------------------------


def test_budget_caps_runs_per_month_and_resets(db, fake):
    _, monitor = make_monitor(db, interval=60, monthly_budget=2)
    assert monitor["monthly_budget"] == 2 and monitor["estimated_monthly_searches"] == 2
    assert monitor["unbudgeted_monthly_searches"] == 720
    for minutes in (60, 120):
        service.run_monitor(
            db, fake, monitor["id"], now=NOW + timedelta(minutes=minutes)
        )
    view = service.list_monitors(db, NOW + timedelta(minutes=130))[0]
    assert (
        view["month_runs"] == 2
        and view["budget_remaining"] == 0
        and view["budget_exhausted"]
    )
    with pytest.raises(ValueError, match="raise the budget"):
        service.run_monitor(db, fake, monitor["id"], now=NOW + timedelta(minutes=180))
    assert len(fake.calls) == 2
    later = NOW + timedelta(hours=4)
    outcome = service.run_due_monitors(db, fake, now=later)
    assert outcome[0]["skipped"] and len(fake.calls) == 2
    current = service.list_monitors(db, later)[0]
    assert current["next_run_at"] == "2026-11-01T00:00:00Z"
    assert current["last_change_summary"].startswith("Monthly budget reached: 2 of 2")
    november = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
    assert service.list_monitors(db, november)[0]["month_runs"] == 0
    ran = service.run_due_monitors(db, fake, now=november)
    assert ran[0]["ok"] and not ran[0]["skipped"] and len(fake.calls) == 3
    assert service.list_monitors(db, november)[0]["month_runs"] == 1


def test_raising_the_budget_brings_a_waiting_monitor_back(db, fake):
    _, monitor = make_monitor(db, interval=60, monthly_budget=1)
    service.run_monitor(db, fake, monitor["id"], now=NOW)
    service.run_due_monitors(db, fake, now=NOW + timedelta(hours=2))
    assert service.list_monitors(db, NOW)[0]["next_run_at"] == "2026-11-01T00:00:00Z"
    raised = service.update_monitor(
        db, monitor["id"], {"monthly_budget": 5}, now=NOW + timedelta(hours=2)
    )
    assert raised["next_run_at"] == "2026-10-22T15:00:00Z"
    assert raised["budget_remaining"] == 4 and not raised["budget_exhausted"]
    unlimited = service.update_monitor(
        db, monitor["id"], {"monthly_budget": 0}, now=NOW
    )
    assert unlimited["budget_remaining"] is None


@pytest.mark.parametrize("bad", [-1, "lots", 100_001])
def test_budget_validation(db, bad):
    _, monitor = make_monitor(db)
    with pytest.raises(ValueError, match="monthly_budget"):
        service.update_monitor(db, monitor["id"], {"monthly_budget": bad})


def test_failed_runs_do_not_count_and_can_pause_the_monitor(db, fake):
    _, monitor = make_monitor(db, interval=30, monthly_budget=3, pause_on_error=True)
    assert monitor["pause_on_error"] is True
    sent = []
    fake.failures.append(SerpApiError("rate_limited", "Too fast", 429))
    with pytest.raises(SerpApiError):
        service.run_monitor(
            db, fake, monitor["id"], now=NOW, notify=lambda *a: sent.append(a)
        )
    current = service.list_monitors(db, NOW)[0]
    assert current["enabled"] is False and current["month_runs"] == 0
    assert current["last_change_summary"].startswith("Run failed: Too fast")
    assert "Paused" in current["last_change_summary"]
    assert sent and sent[0][0] == "Monitor paused: Watch"
    turned_off = service.update_monitor(db, monitor["id"], {"pause_on_error": False})
    assert turned_off["pause_on_error"] is False


# -- X-Ray ----------------------------------------------------------------------------------


def pixel_body(variant: int = 0) -> dict:
    return with_pixel_positions(fixture_body("google", variant))


def test_xray_elements_map_rectangles_to_paths():
    elements, capped = service.xray_elements(pixel_body())
    assert not capped and elements
    paths = [item["path"] for item in elements]
    assert "organic_results[0]" in paths and "related_questions[0]" in paths
    ys = [item["rect"]["y"] for item in elements]
    assert ys == sorted(ys)
    first = next(item for item in elements if item["path"] == "organic_results[0]")
    assert first["label"] == "Coffee - Example Source 1"
    assert first["section"] == "organic_results"
    assert first["rect"] == {
        "x": 180.0,
        "y": first["rect"]["y"],
        "width": 652.0,
        "height": 120.5,
        "hidden": False,
    }
    assert "rectangle" not in first["fields"] and first["fields"]["title"]


def test_xray_skips_bad_rectangles_and_caps(monkeypatch):
    body = {
        "organic_results": [
            {
                "title": "ok",
                "rectangle": {
                    "x": 1,
                    "y": 2,
                    "width": 3,
                    "height": 4,
                    "is_hidden": True,
                },
            },
            {
                "title": "nan",
                "rectangle": {"x": float("nan"), "y": 2, "width": 3, "height": 4},
            },
            {"title": "text", "rectangle": {"x": "1", "y": 2, "width": 3, "height": 4}},
            {
                "title": "bool",
                "rectangle": {"x": True, "y": 2, "width": 3, "height": 4},
            },
        ]
    }
    elements, _ = service.xray_elements(body)
    assert [item["label"] for item in elements] == ["ok"] and elements[0]["rect"][
        "hidden"
    ]
    monkeypatch.setattr(service, "XRAY_ELEMENT_CAP", 2)
    many, capped = service.xray_elements(pixel_body())
    assert len(many) == 2 and capped


def test_xray_fetches_once_then_serves_the_stored_copy(db, fake):
    record = run(db, fake, {"q": "coffee"})
    fake.archives.append(RawResponse(200, pixel_body(), None, "sid1", 5))
    first = service.xray(db, fake, record["id"])
    assert not first["cached"] and first["element_count"] > 0
    assert first["viewport"]["width"] == 1280 and first["parents"]
    assert first["viewport"]["page_height"] >= 900
    again = service.xray(db, None, record["id"])  # no client needed once stored
    assert again["cached"] and again["element_count"] == first["element_count"]
    fake.archives.append(RawResponse(200, pixel_body(1), None, "sid1", 5))
    refreshed = service.xray(db, fake, record["id"], refresh=True)
    assert (
        not refreshed["cached"]
        and refreshed["element_count"] == first["element_count"] + 1
    )


def test_xray_refuses_what_it_cannot_measure(db, fake):
    jobs = run(db, fake, {"q": "nurse"}, engine="google_jobs")
    with pytest.raises(ValueError, match="Google Search and Google Ads"):
        service.xray(db, fake, jobs["id"])
    record = run(db, fake, {"q": "coffee"})
    with pytest.raises(SerpApiError) as failure:
        service.xray(db, None, record["id"])
    assert failure.value.code == "not_configured"
    fake.archives.append(
        RawResponse(
            200,
            {"search_metadata": {"status": "Processing"}},
            None,
            "sid",
            1,
            queued=True,
        )
    )
    with pytest.raises(ValueError, match="still measuring"):
        service.xray(db, fake, record["id"])
    with pytest.raises(LookupError):
        service.xray(db, fake, "0" * 32)


# -- HTTP journeys ----------------------------------------------------------------------------


def test_http_history_search_inside_results_and_exports(env, connected):  # noqa: F811
    record = connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "coffee"}}
    )
    page = connected.ok("GET", "/api/airaapi/searches?q=short+description&in_results=1")
    assert page["total"] == 1 and page["in_results"] is True
    assert "match_excerpt" in page["items"][0]
    assert (
        connected.ok("GET", "/api/airaapi/searches?q=short+description")["total"] == 0
    )
    status, body, headers = connected.call(
        "GET", f"/api/airaapi/searches/{record['id']}/export?format=xlsx", raw=True
    )
    assert status == 200 and body[:2] == b"PK"
    assert "spreadsheetml" in headers.get("Content-Type", "")
    assert ".xlsx" in headers.get("Content-Disposition", "")
    status, body, _ = connected.call(
        "GET", f"/api/airaapi/searches/{record['id']}/export?format=md", raw=True
    )
    assert status == 200 and body.decode("utf-8").startswith("# Google Search: coffee")
    status, _, _ = connected.call(
        "GET", f"/api/airaapi/searches/{record['id']}/export?format=pdf"
    )
    assert status == 400


def test_http_xray_uses_the_free_archive_once(env, connected):  # noqa: F811
    record = connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "coffee"}}
    )
    live = env.stub.live_searches
    first = connected.ok("GET", f"/api/airaapi/searches/{record['id']}/xray")
    assert first["element_count"] > 0 and not first["cached"]
    second = connected.ok("GET", f"/api/airaapi/searches/{record['id']}/xray")
    assert second["cached"] and env.stub.pixel_calls == 1
    assert env.stub.live_searches == live  # X-Ray never spends a search
    jobs = connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google_jobs", "params": {"q": "nurse"}},
    )
    status, body, _ = connected.get(f"/api/airaapi/searches/{jobs['id']}/xray")
    assert status == 400 and "Google" in body["error"]


def test_http_monitor_budget_round_trip(env, connected):  # noqa: F811
    status, saved, _ = connected.call(
        "POST",
        "/api/airaapi/saved",
        {"name": "Watch", "engine": "google", "params": {"q": "coffee"}},
    )
    assert status in (200, 201), saved
    created = connected.ok(
        "POST",
        "/api/airaapi/monitors",
        {
            "saved_id": saved["id"],
            "interval_minutes": 1440,
            "monthly_budget": 1,
            "pause_on_error": True,
        },
        expect=201,
    )
    assert created["monthly_budget"] == 1 and created["pause_on_error"] is True
    connected.ok("POST", f"/api/airaapi/monitors/{created['id']}/run", {})
    status, body, _ = connected.post(f"/api/airaapi/monitors/{created['id']}/run", {})
    assert status == 400 and "budget" in body["error"].lower()
    updated = connected.ok(
        "PATCH", f"/api/airaapi/monitors/{created['id']}", {"monthly_budget": 3}
    )
    assert updated["budget_remaining"] == 2
