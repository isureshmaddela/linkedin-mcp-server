"""Aira API service: caching, errors, usage, diff, export, retention, monitors, keys."""

from __future__ import annotations

import copy
import csv
import io
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from aira.airaapi import catalog, service, store
from aira.airaapi.client import RawResponse, SerpApiError
from serpapi_stub import SerpApiStub, load_fixture

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
KEY = "service_test_key_0123456789abcdef"


def fixture_body(engine: str, variant: int = 0) -> dict:
    text = (
        json.dumps(load_fixture(engine))
        .replace("{{base}}", "http://stub")
        .replace("{{id}}", "sid")
    )
    body = json.loads(text)
    key = next(
        name
        for name, value in body.items()
        if isinstance(value, list) and value and isinstance(value[0], dict)
    )
    if variant > 0:
        for index in range(variant):
            extra = copy.deepcopy(body[key][-1])
            extra["title"] = f"Added {index}"
            extra["link"] = f"https://example.com/added/{index}"
            for identity in ("job_id", "place_id", "product_id"):
                extra.pop(identity, None)
            body[key].append(extra)
    elif variant < 0:
        del body[key][:-variant]
    return body


class FakeClient:
    """Stands in for SerpApiClient: records calls and never touches the network."""

    def __init__(self, variant=0, delay=0.0):
        self.calls = []
        self.archives = []
        self.variant = variant
        self.delay = delay
        self.failures: list[Exception] = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.account_calls = 0
        self.location_calls = 0

    def search(self, engine, params, options):
        with self.lock:
            self.calls.append((engine, dict(params), dict(options)))
            number = len(self.calls)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.failures:
                raise self.failures.pop(0)
            if options.get("async"):
                return RawResponse(
                    200,
                    {"search_metadata": {"id": f"sid{number}", "status": "Queued"}},
                    None,
                    f"sid{number}",
                    3,
                    queued=True,
                )
            if options.get("output", "json") != "json":
                return RawResponse(
                    200, None, "<html><p>page</p></html>", f"sid{number}", 4
                )
            body = fixture_body(
                engine
                if engine
                in {"google", "google_jobs", "google_news", "bing", "google_maps"}
                else "google",
                self.variant,
            )
            body["search_metadata"]["id"] = f"sid{number}"
            return RawResponse(200, body, None, f"sid{number}", 20)
        finally:
            with self.lock:
                self.active -= 1

    def archive(self, search_id, fmt="json"):
        if not self.archives:
            raise SerpApiError("not_found", "no archive", 404)
        item = self.archives.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def account(self):
        self.account_calls += 1
        return {
            "plan_name": "Fake",
            "total_searches_left": 10,
            "hourly_rate_limit": 100,
        }

    def locations(self, q, limit=10):
        self.location_calls += 1
        return [
            {"id": "1", "name": q.title(), "canonical_name": q.title(), "gps": None}
        ]


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def fake():
    return FakeClient()


def run(db, fake, params=None, *, engine="google", now=NOW, **kwargs):
    return service.run_search(
        db,
        fake,
        engine,
        params if params is not None else {"q": "coffee"},
        now=now,
        **kwargs,
    )


def usage(db):
    rows = db.execute(
        "SELECT day, engine, source, searches, cached, errors, credits FROM airaapi_usage"
    ).fetchall()
    return {(row[1], row[2]): row[3:] for row in rows}


# -- settings and options --------------------------------------------------------


def test_setting_parsing_and_limits():
    assert service.parse_setting("airaapi_cache_ttl_minutes", "30") == 30
    assert service.parse_setting("airaapi_hourly_limit", 0) == 0
    for name, bad in (
        ("airaapi_cache_ttl_minutes", "abc"),
        ("airaapi_cache_ttl_minutes", -1),
        ("airaapi_retention_days", 0),
        ("airaapi_max_records", 10),
        ("airaapi_result_cap_mb", 500),
    ):
        with pytest.raises(ValueError):
            service.parse_setting(name, bad)
    values = {"airaapi_cache_ttl_minutes": "15", "airaapi_hourly_limit": "x"}
    limits = service.load_limits(lambda name: values.get(name, ""))
    assert limits.cache_ttl_minutes == 15 and limits.hourly_limit == 0
    assert limits.retention_days == 30 and limits.max_records == 2000
    assert limits.result_cap_bytes == 5 * 1024 * 1024


def test_option_normalisation():
    options = service.normalize_options(None)
    assert options["output"] == "json" and not options["no_cache"]
    assert service.normalize_options({"no_cache": "true"})["no_cache"] is True
    assert service.normalize_options({"output": "HTML"})["output"] == "html"
    for bad, message in (
        ({"colour": 1}, "Unknown option"),
        ({"output": "xml"}, "output must be one of"),
        ({"async": True, "no_cache": True}, "async together with no_cache"),
        ({"async": True, "output": "html"}, "collected as JSON"),
        ({"json_restrictor": "a", "output": "html"}, "only applies to JSON"),
        ({"json_restrictor": "x" * 501}, "limited to 500"),
        ({"no_cache": "perhaps"}, "must be true or false"),
    ):
        with pytest.raises(ValueError, match=message):
            service.normalize_options(bad)


def test_params_hash_is_order_independent_and_option_sensitive():
    base = service.normalize_options({})
    first = service.params_hash("google", {"q": "a", "gl": "us"}, base)
    assert first == service.params_hash("google", {"gl": "us", "q": "a"}, base)
    assert first != service.params_hash("bing", {"q": "a", "gl": "us"}, base)
    assert first != service.params_hash("google", {"q": "b", "gl": "us"}, base)
    html = service.normalize_options({"output": "html"})
    assert first != service.params_hash("google", {"q": "a", "gl": "us"}, html)
    restricted = service.normalize_options({"json_restrictor": "organic_results"})
    assert first != service.params_hash("google", {"q": "a", "gl": "us"}, restricted)
    no_cache = service.normalize_options({"no_cache": True})
    assert first == service.params_hash("google", {"q": "a", "gl": "us"}, no_cache)


# -- run_search -------------------------------------------------------------------


def test_run_search_stores_a_full_record(db, fake):
    record = run(db, fake, {"q": "coffee", "gl": "us"}, label=" my label ")
    assert (
        record["engine"] == "google"
        and record["engine_name"] == catalog.engine("google").name
    )
    assert (
        record["status"] == "ok" and record["credits"] == 1 and not record["cache_hit"]
    )
    assert record["params"] == {"q": "coffee", "gl": "us"}
    assert record["label"] == "my label" and record["source"] == "playground"
    assert (
        record["result_count"] == 5 and record["main_result_key"] == "organic_results"
    )
    assert record["result"]["organic_results"][0]["position"] == 1
    assert record["serpapi_id"] == "sid1" and record["duration_ms"] == 20
    assert record["created_at"] == "2026-10-22T12:00:00Z"
    assert record["html_available"] is True and record["output"] == "json"
    fetched = service.get_search(db, record["id"])
    assert fetched["result"] == record["result"] and fetched["result_bytes"] > 0
    assert usage(db) == {("google", "playground"): (1, 0, 0, 1)}


def test_validation_failures_record_nothing_and_call_nothing(db, fake):
    with pytest.raises(ValueError, match="'q' is required"):
        run(db, fake, {})
    with pytest.raises(ValueError, match="Unknown parameter"):
        run(db, fake, {"q": "x", "bogus": 1})
    with pytest.raises(ValueError, match="not an engine parameter"):
        run(db, fake, {"q": "x", "api_key": "k"})
    with pytest.raises(ValueError, match="source must be"):
        run(db, fake, source="elsewhere")
    with pytest.raises(LookupError, match="Did you mean"):
        run(db, fake, engine="googel")
    assert fake.calls == []
    assert db.execute("SELECT COUNT(*) FROM airaapi_searches").fetchone()[0] == 0
    assert usage(db) == {}


def test_cache_hit_costs_nothing_and_does_not_call_the_client(db, fake):
    first = run(db, fake, {"q": "coffee"}, source="api")
    second = run(
        db, fake, {"q": "coffee"}, now=NOW + timedelta(minutes=10), source="playground"
    )
    assert len(fake.calls) == 1
    assert second["cache_hit"] is True and second["credits"] == 0
    assert second["id"] != first["id"] and second["serpapi_id"] == first["serpapi_id"]
    assert second["result"] == first["result"] and second["duration_ms"] == 0
    assert second["source"] == "playground"
    assert usage(db) == {
        ("google", "api"): (1, 0, 0, 1),
        ("google", "playground"): (1, 1, 0, 0),
    }
    history = service.list_searches(db, cache_hit=True)
    assert history["total"] == 1 and history["items"][0]["id"] == second["id"]


def test_cache_respects_ttl_no_cache_and_parameters(db, fake):
    limits = service.Limits(cache_ttl_minutes=30)
    run(db, fake, limits=limits)
    run(db, fake, now=NOW + timedelta(minutes=29), limits=limits)
    assert len(fake.calls) == 1
    run(db, fake, now=NOW + timedelta(minutes=31), limits=limits)  # expired
    assert len(fake.calls) == 2
    run(
        db,
        fake,
        options={"no_cache": True},
        limits=limits,
        now=NOW + timedelta(minutes=32),
    )
    assert len(fake.calls) == 3
    assert fake.calls[-1][2]["no_cache"] is True
    run(db, fake, {"q": "tea"}, limits=limits, now=NOW + timedelta(minutes=32))
    assert len(fake.calls) == 4
    run(
        db,
        fake,
        limits=service.Limits(cache_ttl_minutes=0),
        now=NOW + timedelta(minutes=32),
    )
    assert len(fake.calls) == 5


def test_cache_hits_do_not_extend_the_window(db, fake):
    limits = service.Limits(cache_ttl_minutes=60)
    run(db, fake, limits=limits)
    run(db, fake, limits=limits, now=NOW + timedelta(minutes=50))  # hit
    run(
        db, fake, limits=limits, now=NOW + timedelta(minutes=70)
    )  # original is stale now
    assert len(fake.calls) == 2


def test_cache_serves_without_a_client_but_live_calls_need_one(db, fake):
    run(db, fake)
    cached = run(db, None, now=NOW + timedelta(minutes=1))
    assert cached["cache_hit"] is True
    with pytest.raises(SerpApiError) as caught:
        run(db, None, {"q": "uncached"})
    assert caught.value.code == "not_configured" and caught.value.response_status == 409


def test_errors_are_recorded_counted_and_never_cached(db, fake):
    fake.failures.append(
        SerpApiError("out_of_searches", "Your account has run out of searches.", 429)
    )
    with pytest.raises(SerpApiError) as caught:
        run(db, fake)
    assert caught.value.record_id
    row = store.get_search_row(db, caught.value.record_id)
    assert (
        row["status"] == "error" and row["credits"] == 0 and row["http_status"] == 429
    )
    assert "run out of searches" in row["error"]
    assert usage(db) == {("google", "playground"): (1, 0, 1, 0)}
    retry = run(db, fake, now=NOW + timedelta(minutes=1))  # not served from the error
    assert retry["status"] == "ok" and len(fake.calls) == 2
    history = service.list_searches(db, status="error")
    assert history["total"] == 1


def test_empty_results_are_a_billed_ok_record_with_a_note(db):
    class Empty(FakeClient):
        def search(self, engine, params, options):
            return RawResponse(
                200,
                {"search_metadata": {"id": "e1", "status": "Success"}},
                None,
                "e1",
                5,
                note="Google hasn't returned any results for this query.",
            )

    record = run(db, Empty())
    assert (
        record["status"] == "ok"
        and record["result_count"] == 0
        and record["credits"] == 1
    )
    assert record["note"].startswith("Google hasn't returned")


def test_local_hourly_limit_counts_live_calls_only(db, fake):
    limits = service.Limits(hourly_limit=2, cache_ttl_minutes=60)
    run(db, fake, {"q": "a"}, limits=limits)
    run(db, fake, {"q": "b"}, limits=limits)
    run(db, fake, {"q": "a"}, limits=limits)  # cache hit is free
    with pytest.raises(SerpApiError) as caught:
        run(db, fake, {"q": "c"}, limits=limits)
    assert (
        caught.value.code == "local_rate_limit" and caught.value.response_status == 429
    )
    assert len(fake.calls) == 2
    run(db, fake, {"q": "c"}, limits=limits, now=NOW + timedelta(hours=1, minutes=1))
    assert len(fake.calls) == 3


def test_concurrency_is_capped(tmp_path, monkeypatch):
    path = tmp_path / "concurrent.sqlite3"
    seed = sqlite3.connect(path)
    store.ensure_schema(seed)
    seed.commit()
    seed.close()
    fake = FakeClient(delay=0.08)
    errors: list[Exception] = []

    def work(index: int) -> None:
        connection = sqlite3.connect(path, timeout=20)
        try:
            service.run_search(
                connection, fake, "google", {"q": f"query {index}"}, now=NOW
            )
        except Exception as error:  # noqa: BLE001 - surfaced below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(fake.calls) == 10
    assert 1 < fake.max_active <= service.MAX_CONCURRENT_SEARCHES
    assert service.inflight_searches() == 0


def test_busy_when_no_slot_frees_up(db, fake, monkeypatch):
    monkeypatch.setattr(service, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(service, "SLOT_WAIT_SECONDS", 0.05)
    assert service._slots.acquire(timeout=1)
    with pytest.raises(SerpApiError) as caught:
        run(db, fake)
    assert caught.value.code == "busy" and caught.value.response_status == 429
    assert fake.calls == []


def test_oversized_results_are_returned_but_not_stored_or_cached(db, fake):
    limits = service.Limits(result_cap_mb=1)

    class Big(FakeClient):
        def search(self, engine, params, options):
            body = fixture_body("google")
            body["organic_results"] = [
                {"title": str(i), "snippet": "x" * 4096} for i in range(400)
            ]
            self.calls.append((engine, dict(params), dict(options)))
            return RawResponse(200, body, None, "big", 5)

    big = Big()
    record = run(db, big, limits=limits)
    assert record["result_truncated"] is True and record["result"] is not None
    assert record["result_count"] == 400
    stored = service.get_search(db, record["id"])
    assert stored["result"] is None and stored["result_truncated"] is True
    run(db, big, limits=limits, now=NOW + timedelta(minutes=1))
    assert len(big.calls) == 2  # nothing to serve from cache
    with pytest.raises(ValueError, match="no stored result"):
        service.diff_searches(db, record["id"], record["id"])


def test_html_output_is_stored_and_exported(db, fake):
    record = run(db, fake, options={"output": "html"})
    assert record["output"] == "html" and record["result"] is None
    assert record["html"] == "<html><p>page</p></html>" and record["html_available"]
    fetched = service.get_search(db, record["id"], include_html=True)
    assert fetched["html"] == "<html><p>page</p></html>"
    body, content_type, filename = service.export_search(db, record["id"], "html")
    assert body == b"<html><p>page</p></html>" and content_type.startswith("text/html")
    assert filename.endswith(".html")
    cached = run(db, fake, options={"output": "html"}, now=NOW + timedelta(minutes=2))
    assert cached["cache_hit"] and cached["html"] == record["html"]
    assert len(fake.calls) == 1
    with pytest.raises(LookupError, match="no stored JSON"):
        service.export_search(db, record["id"], "json")


def test_markdown_output_exports_as_markdown(db, fake):
    record = run(db, fake, options={"output": "md"})
    body, content_type, filename = service.export_search(db, record["id"], "html")
    assert content_type.startswith("text/markdown") and filename.endswith(".md")


def test_async_search_is_polled_to_completion(db, fake):
    record = run(db, fake, options={"async": True})
    assert record["status"] == "running" and record["credits"] == 0
    assert record["result_count"] == 0
    assert service.queue_state(db)["async_pending"] == 1
    # Still processing: nothing changes.
    fake.archives.append(
        RawResponse(
            200,
            {"search_metadata": {"id": "sid1", "status": "Processing"}},
            None,
            "sid1",
            1,
            queued=True,
        )
    )
    again = service.get_search(db, record["id"], client=fake, now=NOW)
    assert again["status"] == "running"
    done_body = fixture_body("google")
    fake.archives.append(RawResponse(200, done_body, None, "sid1", 250))
    finished = service.get_search(db, record["id"], client=fake, now=NOW)
    assert finished["status"] == "ok" and finished["credits"] == 1
    assert finished["result_count"] == 5 and finished["result"]["organic_results"]
    assert usage(db) == {("google", "playground"): (1, 0, 0, 1)}
    assert service.queue_state(db)["async_pending"] == 0
    assert service.get_search(db, record["id"], client=fake)["status"] == "ok"


def test_async_search_that_expires_or_fails_becomes_an_error(db, fake):
    record = run(db, fake, {"q": "one"}, options={"async": True})
    fake.archives.append(SerpApiError("gone", "This search has expired.", 410))
    result = service.get_search(db, record["id"], client=fake, now=NOW)
    assert result["status"] == "error" and "expired" in result["error"]
    other = run(db, fake, {"q": "two"}, options={"async": True})
    failed = fixture_body("google")
    failed["search_metadata"]["status"] = "Error"
    failed["error"] = "We couldn't get valid results."
    fake.archives.append(RawResponse(200, failed, None, "x", 1))
    result = service.get_search(db, other["id"], client=fake, now=NOW)
    assert result["status"] == "error" and result["credits"] == 0
    third = run(db, fake, {"q": "three"}, options={"async": True})
    fake.archives.append(SerpApiError("rate_limited", "slow down", 429))
    assert service.get_search(db, third["id"], client=fake)["status"] == "running"


def test_archive_html_is_fetched_lazily_and_stored(db, fake):
    record = run(db, fake)
    fake.archives.append(RawResponse(200, None, "<html>archived</html>", "sid1", 1))
    fetched = service.get_search(db, record["id"], include_html=True, client=fake)
    assert fetched["html"] == "<html>archived</html>"
    again = service.get_search(db, record["id"], include_html=True)
    assert again["html"] == "<html>archived</html>"
    other = run(db, fake, {"q": "other"})
    missing = service.get_search(db, other["id"], include_html=True, client=fake)
    assert missing["html"] is None


def test_rerun_is_fresh_unless_the_cache_is_requested(db, fake):
    record = run(db, fake, {"q": "coffee", "gl": "us"}, label="orig")
    fresh = service.rerun(db, fake, record["id"], now=NOW + timedelta(minutes=1))
    assert (
        fresh["source"] == "rerun"
        and not fresh["cache_hit"]
        and fresh["label"] == "orig"
    )
    assert len(fake.calls) == 2 and fresh["params"] == record["params"]
    cached = service.rerun(
        db, fake, record["id"], {"use_cache": True}, now=NOW + timedelta(minutes=2)
    )
    assert cached["cache_hit"] and len(fake.calls) == 2
    with pytest.raises(LookupError):
        service.rerun(db, fake, "missing")


def test_history_meta_pin_and_delete(db, fake):
    record = run(db, fake)
    updated = service.update_search_meta(
        db, record["id"], {"pinned": True, "label": " Keep "}
    )
    assert updated["pinned"] is True and updated["label"] == "Keep"
    assert service.list_searches(db, pinned=True)["total"] == 1
    with pytest.raises(ValueError):
        service.update_search_meta(db, record["id"], {})
    with pytest.raises(LookupError):
        service.update_search_meta(db, "missing", {"pinned": True})
    service.delete_search(db, record["id"])
    with pytest.raises(LookupError):
        service.get_search(db, record["id"])
    with pytest.raises(LookupError):
        service.delete_search(db, record["id"])


def test_bulk_delete_needs_a_filter_and_keeps_pins(db, fake):
    for index in range(3):
        run(db, fake, {"q": f"q{index}"})
    first = service.list_searches(db)["items"][0]
    service.update_search_meta(db, first["id"], {"pinned": True})
    with pytest.raises(ValueError, match="never accidental"):
        service.bulk_delete(db)
    with pytest.raises(ValueError, match="ISO-8601"):
        service.bulk_delete(db, before="yesterday")
    assert service.bulk_delete(db, everything=True) == {"deleted": 2, "kept_pinned": 1}
    assert service.bulk_delete(db, engine="google", include_pinned=True) == {
        "deleted": 1,
        "kept_pinned": 0,
    }


# -- usage and overview ------------------------------------------------------------


def test_usage_rollups_and_overview(db, fake):
    run(db, fake, {"q": "a"}, source="api")
    run(db, fake, {"q": "a"}, source="api", now=NOW + timedelta(minutes=1))  # cached
    run(db, fake, {"q": "b"}, engine="bing", now=NOW + timedelta(hours=2))
    fake.failures.append(SerpApiError("upstream_error", "boom", 500))
    with pytest.raises(SerpApiError):
        run(db, fake, {"q": "c"}, engine="google_news", now=NOW + timedelta(hours=3))
    old = NOW - timedelta(days=3)
    run(db, fake, {"q": "z"}, now=old)
    overview = service.usage_overview(db, NOW + timedelta(hours=3))
    assert overview["today"] == 4 and overview["last_30_days"] == 5
    assert overview["last_7_days"] == 5
    assert (
        len(overview["by_day"]) == 30 and overview["by_day"][-1]["day"] == "2026-10-22"
    )
    today = overview["by_day"][-1]
    assert (today["searches"], today["cached"], today["errors"]) == (4, 1, 1)
    assert overview["by_day"][-4]["searches"] == 1
    assert overview["cache_hit_rate"] == pytest.approx(0.2)
    assert overview["error_rate"] == pytest.approx(0.2)
    assert overview["credits_used_30d"] == 3
    assert overview["by_source"] == [
        {"source": "playground", "searches": 3},
        {"source": "api", "searches": 2},
    ]
    engines = {row["engine"]: row for row in overview["by_engine"]}
    assert engines["google"]["searches"] == 3 and engines["google_news"]["errors"] == 1
    assert (
        engines["google"]["name"] == catalog.engine("google").name
        and engines["google"]["avg_ms"] == 20
    )
    empty = service.usage_overview(db, NOW + timedelta(days=60))
    assert empty["last_30_days"] == 0 and empty["cache_hit_rate"] == 0


def test_overview_includes_saved_monitors_and_recent(db, fake):
    saved = service.create_saved(
        db, {"name": "Coffee", "engine": "google", "params": {"q": "coffee"}}, now=NOW
    )
    service.create_monitor(
        db, {"saved_id": saved["id"], "interval_minutes": 30}, now=NOW
    )
    for index in range(12):
        run(db, fake, {"q": f"q{index}"}, now=NOW + timedelta(seconds=index))
    overview = service.overview(db, NOW + timedelta(minutes=1))
    assert overview["saved"] == 1 and overview["engines"] == len(catalog.engines())
    assert overview["monitors"] == {"active": 1, "due_next": "2026-10-22T12:30:00Z"}
    assert len(overview["recent"]) == 10 and overview["recent"][0]["params"] == {
        "q": "q11"
    }
    assert overview["categories"]


def test_throttle_queue_and_engine_listing(db, fake):
    run(db, fake)
    state = service.throttle_state(
        db, service.Limits(hourly_limit=5), NOW + timedelta(minutes=5)
    )
    assert state == {"hourly_limit": 5, "used_this_hour": 1}
    assert (
        service.throttle_state(db, service.Limits(), NOW + timedelta(hours=2))[
            "used_this_hour"
        ]
        == 0
    )
    assert service.queue_state(db) == {"running": 0, "async_pending": 0}
    listing = service.engines_listing(category="jobs")
    assert (
        listing["total"] == 3 and listing["categories"] and listing["families"]
    )  # google_jobs, its listing, aira_jobs
    assert {item["id"] for item in listing["engines"]} == {
        "aira_jobs",
        "google_jobs",
        "google_jobs_listing",
    }


# -- diff ---------------------------------------------------------------------------


def test_diff_reports_added_removed_changed_and_rank(db):
    before = fixture_body("google")
    after = copy.deepcopy(before)
    removed = after["organic_results"].pop(0)
    after["organic_results"][0]["snippet"] = "A new snippet"
    after["organic_results"].append(
        {"position": 9, "title": "Fresh", "link": "https://example.com/fresh"}
    )
    after["search_information"]["total_results"] = 1
    after["search_metadata"]["id"] = "different-id"
    diff = service.diff_results("google", before, after)
    assert diff["list_key"] == "organic_results" and diff["changed_any"]
    assert [item["title"] for item in diff["added"]] == ["Fresh"]
    assert [item["link"] for item in diff["removed"]] == [removed["link"]]
    paths = {item["path"] for item in diff["changed"]}
    assert any(path.endswith(".snippet") for path in paths)
    assert any(path.endswith(".rank") for path in paths)
    assert "search_information.total_results" in paths
    assert not any(path.startswith("search_metadata") for path in paths)
    assert (
        diff["summary"].startswith("1 added, 1 removed")
        and "organic_results" in diff["summary"]
    )
    assert diff["counts"]["added"] == 1


def test_diff_of_identical_results_is_empty():
    body = fixture_body("google_jobs")
    other = copy.deepcopy(body)
    other["search_metadata"]["id"] = "other"
    other["serpapi_pagination"]["next_page_token"] = "different"
    diff = service.diff_results("google_jobs", body, other)
    assert not diff["changed_any"] and diff["summary"] == "No differences"
    assert diff["added"] == [] and diff["removed"] == [] and diff["changed"] == []


def test_diff_handles_missing_or_odd_bodies():
    assert service.diff_results("google", None, None)["summary"] == "No differences"
    diff = service.diff_results(
        "google", {}, {"organic_results": [{"title": "a", "link": "l"}]}
    )
    assert len(diff["added"]) == 1
    duplicates = {"organic_results": [{"link": "same"}, {"link": "same", "x": 1}]}
    again = service.diff_results("google", duplicates, duplicates)
    assert not again["changed_any"]


def test_diff_searches_validates_inputs(db, fake):
    first = run(db, fake)
    fake.variant = 1
    second = run(db, fake, options={"no_cache": True}, now=NOW + timedelta(minutes=5))
    diff = service.diff_searches(db, first["id"], second["id"])
    assert (
        len(diff["added"]) == 1
        and diff["a"]["id"] == first["id"]
        and diff["b"]["id"] == second["id"]
    )
    other_engine = run(db, fake, {"q": "x"}, engine="bing")
    mixed = service.diff_searches(db, first["id"], other_engine["id"])
    assert mixed["summary"].startswith("Different engines")
    html = run(db, fake, {"q": "h"}, options={"output": "html"})
    with pytest.raises(ValueError, match="Only JSON"):
        service.diff_searches(db, first["id"], html["id"])
    with pytest.raises(LookupError):
        service.diff_searches(db, first["id"], "missing")


# -- export -------------------------------------------------------------------------


def test_export_csv_flattens_and_neutralises_formulas(db):
    class Formula(FakeClient):
        def search(self, engine, params, options):
            body = fixture_body("google")
            body["organic_results"][0]["title"] = '=HYPERLINK("http://evil")'
            body["organic_results"][1]["title"] = "-5 degrees"
            body["organic_results"][2]["snippet"] = 'line1\nline2, with "quotes"'
            body["organic_results"][0]["sitelinks"] = {
                "inline": [{"title": "a"}, {"title": "b"}]
            }
            return RawResponse(200, body, None, "csv1", 3)

    record = run(db, Formula())
    body, content_type, filename = service.export_search(db, record["id"], "csv")
    assert content_type.startswith("text/csv") and filename.endswith(".csv")
    assert body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    assert len(rows) == 5
    assert rows[0]["title"].startswith("'=HYPERLINK")
    assert rows[1]["title"] == "'-5 degrees"
    assert rows[2]["snippet"] == 'line1\nline2, with "quotes"'
    assert rows[0]["position"] == "1"
    assert json.loads(rows[0]["sitelinks.inline"]) == [{"title": "a"}, {"title": "b"}]
    assert "snippet_highlighted_words" in rows[0]


def test_export_csv_caps_columns_and_handles_detail_pages(db):
    wide = {f"column_{index}": index for index in range(100)}
    csv_text = service.results_to_csv("google", {"organic_results": [wide]})
    assert len(csv_text.splitlines()[0].split(",")) == service.CSV_COLUMN_CAP
    single = service.results_to_csv(
        "youtube_video",
        {"video_results": {"title": "x"}, "search_metadata": {"id": "1"}},
    )
    assert (
        single.splitlines()[0] == "video_results.title"
        and "search_metadata" not in single
    )
    with pytest.raises(ValueError):
        service.results_to_csv("google", None)
    scalars = service.results_to_csv("google", {"organic_results": ["a", "b"]})
    assert scalars.splitlines() == ["value", "a", "b"]


def test_export_json_and_curl_never_contain_a_key(db, fake):
    record = run(
        db, fake, {"q": "o'brien & sons", "gl": "us"}, options={"no_cache": True}
    )
    body, content_type, filename = service.export_search(db, record["id"], "json")
    document = json.loads(body)
    assert document["search"]["id"] == record["id"] and "result" in document
    assert "result" not in document["search"]
    assert content_type.startswith("application/json") and filename.endswith(".json")
    curl, kind, name = service.export_search(db, record["id"], "curl")
    text = curl.decode()
    assert text.startswith("curl -G ") and "api_key=YOUR_SERPAPI_KEY" in text
    assert "engine=google" in text and "no_cache=true" in text
    assert "'q=o'\"'\"'brien & sons'" in text or "o'\"'\"'brien" in text
    assert kind.startswith("text/plain") and name.endswith(".sh")
    with pytest.raises(ValueError):
        service.export_search(db, record["id"], "xml")
    with pytest.raises(LookupError, match="No raw page"):
        service.export_search(db, record["id"], "html")
    with pytest.raises(LookupError):
        service.export_search(db, "missing", "json")


# -- retention ----------------------------------------------------------------------


def test_retention_removes_old_and_surplus_records_and_old_usage(db, fake):
    limits = service.Limits(retention_days=30, max_records=50)
    for index in range(4):
        store.insert_search(
            db,
            {
                "id": f"old{index}",
                "engine": "google",
                "params_json": json.dumps({"q": f"old{index}"}),
                "created_at": store.to_iso(NOW - timedelta(days=45, minutes=index)),
            },
        )
    store.insert_search(
        db,
        {
            "id": "pinned",
            "engine": "google",
            "params_json": json.dumps({"q": "pinned"}),
            "created_at": store.to_iso(NOW - timedelta(days=45)),
            "pinned": 1,
        },
    )
    for index in range(3):
        run(
            db,
            fake,
            {"q": f"new{index}"},
            now=NOW - timedelta(minutes=index),
            limits=service.Limits(retention_days=3650),
        )
    store.bump_usage(db, "2024-01-01", "google", "api")
    deleted = service.apply_retention(db, limits, NOW)
    assert deleted == 4
    remaining = {
        item["params"]["q"] for item in service.list_searches(db, limit=100)["items"]
    }
    assert remaining == {"pinned", "new0", "new1", "new2"}
    assert (
        db.execute(
            "SELECT COUNT(*) FROM airaapi_usage WHERE day = '2024-01-01'"
        ).fetchone()[0]
        == 0
    )
    tight = service.Limits(retention_days=3650, max_records=50)
    assert service.apply_retention(db, tight, NOW) == 0
    surplus = service.Limits(retention_days=3650, max_records=50)
    for index in range(55):
        store.insert_search(
            db,
            {
                "id": f"extra{index:02d}",
                "engine": "bing",
                "created_at": store.to_iso(NOW - timedelta(hours=1, minutes=index)),
            },
        )
    assert service.apply_retention(db, surplus, NOW) == 9  # 59 rows - 50
    assert (
        db.execute(
            "SELECT COUNT(*) FROM airaapi_searches WHERE id = 'pinned'"
        ).fetchone()[0]
        == 1
    )


def test_retention_runs_opportunistically_after_searches(db, fake):
    limits = service.Limits(retention_days=30, max_records=50)
    run(db, fake, {"q": "ancient"}, now=NOW - timedelta(days=90), limits=limits)
    run(db, fake, {"q": "today"}, limits=limits)
    queries = {item["params"]["q"] for item in service.list_searches(db)["items"]}
    assert queries == {"today"}


# -- saved searches and monitors --------------------------------------------------


def test_saved_searches_validate_and_run(db, fake):
    saved = service.create_saved(
        db,
        {
            "name": " Barista jobs ",
            "engine": "google_jobs",
            "params": {"q": "barista"},
            "tags": "jobs, nyc, Jobs",
        },
        now=NOW,
    )
    assert saved["name"] == "Barista jobs" and saved["tags"] == ["jobs", "nyc"]
    assert saved["engine_name"] == "Google Jobs" and saved["last_run_at"] is None
    for payload, message in (
        ({"name": "", "engine": "google", "params": {"q": "x"}}, "name"),
        ({"name": "x", "engine": "google", "params": {}}, "'q' is required"),
        (
            {
                "name": "x",
                "engine": "google",
                "params": {"q": "a"},
                "tags": [f"t{n}" for n in range(11)],
            },
            "at most 10",
        ),
        ({"name": "x", "engine": "google", "params": {"q": "a"}, "tags": 5}, "tags"),
    ):
        with pytest.raises(ValueError, match=message):
            service.create_saved(db, payload)
    with pytest.raises(LookupError):
        service.create_saved(db, {"name": "x", "engine": "nope", "params": {}})
    record = service.run_saved(db, fake, saved["id"], now=NOW + timedelta(minutes=3))
    assert record["label"] == "Barista jobs" and record["engine"] == "google_jobs"
    listed = service.list_saved(db)
    assert (
        listed[0]["last_run_at"] == "2026-10-22T12:03:00Z"
        and listed[0]["last_search_id"] == record["id"]
    )
    updated = service.update_saved(
        db,
        saved["id"],
        {"name": "Renamed", "tags": ["x"], "pinned": True, "params": {"q": "cook"}},
    )
    assert (
        updated["name"] == "Renamed"
        and updated["pinned"]
        and updated["params"] == {"q": "cook"}
    )
    with pytest.raises(ValueError):
        service.update_saved(db, saved["id"], {"params": {}})
    with pytest.raises(ValueError):
        service.update_saved(db, saved["id"], {})
    with pytest.raises(LookupError):
        service.update_saved(db, "missing", {"name": "x"})
    service.delete_saved(db, saved["id"])
    with pytest.raises(LookupError):
        service.delete_saved(db, saved["id"])
    with pytest.raises(LookupError):
        service.run_saved(db, fake, saved["id"])


def test_saved_search_limit(db, monkeypatch):
    monkeypatch.setattr(service, "MAX_SAVED", 2)
    for index in range(2):
        service.create_saved(
            db, {"name": f"s{index}", "engine": "google", "params": {"q": "a"}}
        )
    with pytest.raises(ValueError, match="up to 2 saved"):
        service.create_saved(
            db, {"name": "s3", "engine": "google", "params": {"q": "a"}}
        )


def make_monitor(db, interval=30, **extra):
    saved = service.create_saved(
        db, {"name": "Watch", "engine": "google", "params": {"q": "coffee"}}, now=NOW
    )
    monitor = service.create_monitor(
        db, {"saved_id": saved["id"], "interval_minutes": interval, **extra}, now=NOW
    )
    return saved, monitor


def test_monitor_creation_rules_and_cost_estimate(db):
    saved, monitor = make_monitor(db, interval=60)
    assert monitor["estimated_monthly_searches"] == 720
    assert monitor["next_run_at"] == "2026-10-22T13:00:00Z" and monitor["enabled"]
    assert monitor["saved_name"] == "Watch" and monitor["engine"] == "google"
    assert service.estimate_monthly_searches(15) == 2880
    assert service.estimate_monthly_searches(1440) == 30
    with pytest.raises(ValueError, match="already has a monitor"):
        service.create_monitor(db, {"saved_id": saved["id"], "interval_minutes": 60})
    other = service.create_saved(
        db, {"name": "Other", "engine": "bing", "params": {"q": "a"}}
    )
    for bad, message in (
        (10, "every 15 minutes"),
        ("abc", "whole number"),
        (60 * 24 * 91, "90 days"),
    ):
        with pytest.raises(ValueError, match=message):
            service.create_monitor(
                db, {"saved_id": other["id"], "interval_minutes": bad}
            )
    with pytest.raises(LookupError):
        service.create_monitor(db, {"saved_id": "missing", "interval_minutes": 30})


def test_monitor_limit(db, monkeypatch):
    monkeypatch.setattr(service, "MAX_MONITORS", 1)
    make_monitor(db)
    other = service.create_saved(
        db, {"name": "Other", "engine": "bing", "params": {"q": "a"}}
    )
    with pytest.raises(ValueError, match="up to 1 monitors"):
        service.create_monitor(db, {"saved_id": other["id"], "interval_minutes": 30})


def test_monitor_update_and_delete(db):
    _, monitor = make_monitor(db, interval=30)
    paused = service.update_monitor(db, monitor["id"], {"enabled": False}, now=NOW)
    assert paused["enabled"] is False
    resumed = service.update_monitor(
        db, monitor["id"], {"enabled": True}, now=NOW + timedelta(hours=5)
    )
    assert resumed["next_run_at"] == "2026-10-22T17:30:00Z"
    changed = service.update_monitor(
        db, monitor["id"], {"interval_minutes": 120, "notify_on_change": False}, now=NOW
    )
    assert changed["interval_minutes"] == 120 and changed["notify_on_change"] is False
    with pytest.raises(ValueError):
        service.update_monitor(db, monitor["id"], {"interval_minutes": 5})
    with pytest.raises(ValueError):
        service.update_monitor(db, monitor["id"], {})
    with pytest.raises(LookupError):
        service.update_monitor(db, "missing", {"enabled": True})
    service.delete_monitor(db, monitor["id"])
    with pytest.raises(LookupError):
        service.delete_monitor(db, monitor["id"])


def test_monitor_baseline_then_change_notifies_once(db, fake):
    _, monitor = make_monitor(db, interval=30)
    sent = []
    notify = lambda title, body, link: sent.append((title, body, link))  # noqa: E731
    first = service.run_monitor(
        db, fake, monitor["id"], now=NOW + timedelta(minutes=30), notify=notify
    )
    assert first["diff"] is None and first["summary"].startswith(
        "Baseline captured (5 results)"
    )
    assert first["record"]["source"] == "monitor" and not first["record"]["cache_hit"]
    assert sent == []
    same = service.run_monitor(
        db, fake, monitor["id"], now=NOW + timedelta(minutes=60), notify=notify
    )
    assert same["diff"]["changed_any"] is False and sent == []
    fake.variant = 2
    changed = service.run_monitor(
        db, fake, monitor["id"], now=NOW + timedelta(minutes=90), notify=notify
    )
    assert changed["diff"]["counts"]["added"] == 2 and len(sent) == 1
    title, body, link = sent[0]
    assert title == "Monitor changed: Watch" and "2 added" in body
    assert link == f"#/airaapi?tab=history&search={changed['record']['id']}"
    current = service.list_monitors(db)[0]
    assert current["last_run_at"] == "2026-10-22T13:30:00Z"
    assert current["next_run_at"] == "2026-10-22T14:00:00Z"
    assert current["last_change_summary"].startswith("2 added")
    assert current["last_search_id"] == changed["record"]["id"]
    assert len(fake.calls) == 3  # every monitor run is live, never cached


def test_monitor_without_notify_flag_stays_quiet(db, fake):
    _, monitor = make_monitor(db, interval=30, notify_on_change=False)
    sent = []
    service.run_monitor(
        db, fake, monitor["id"], now=NOW, notify=lambda *a: sent.append(a)
    )
    fake.variant = 1
    result = service.run_monitor(
        db,
        fake,
        monitor["id"],
        now=NOW + timedelta(minutes=30),
        notify=lambda *a: sent.append(a),
    )
    assert result["diff"]["changed_any"] and sent == []


def test_monitor_failure_is_recorded_and_rescheduled(db, fake):
    _, monitor = make_monitor(db, interval=30)
    fake.failures.append(SerpApiError("rate_limited", "Too fast", 429))
    with pytest.raises(SerpApiError):
        service.run_monitor(db, fake, monitor["id"], now=NOW + timedelta(minutes=30))
    current = service.list_monitors(db)[0]
    assert current["last_change_summary"] == "Run failed: Too fast"
    assert current["next_run_at"] == "2026-10-22T13:00:00Z"


def test_run_due_monitors_runs_only_what_is_due(db, fake):
    saved_a, a = make_monitor(db, interval=30)
    saved_b = service.create_saved(
        db, {"name": "B", "engine": "bing", "params": {"q": "tea"}}, now=NOW
    )
    b = service.create_monitor(
        db, {"saved_id": saved_b["id"], "interval_minutes": 60}, now=NOW
    )
    saved_c = service.create_saved(
        db, {"name": "C", "engine": "google_news", "params": {"q": "tea"}}, now=NOW
    )
    c = service.create_monitor(
        db,
        {"saved_id": saved_c["id"], "interval_minutes": 30, "enabled": False},
        now=NOW,
    )
    assert service.run_due_monitors(db, fake, now=NOW + timedelta(minutes=10)) == []
    results = service.run_due_monitors(db, fake, now=NOW + timedelta(minutes=31))
    assert [item["monitor_id"] for item in results] == [a["id"]] and results[0]["ok"]
    results = service.run_due_monitors(db, fake, now=NOW + timedelta(hours=2))
    assert {item["monitor_id"] for item in results} == {a["id"], b["id"]}
    assert c["id"] not in {item["monitor_id"] for item in results}
    fake.failures.append(SerpApiError("busy", "later", None))
    outcome = service.run_due_monitors(db, fake, now=NOW + timedelta(hours=5))
    assert any(not item["ok"] for item in outcome) and any(
        item["ok"] for item in outcome
    )
    assert (
        service.run_due_monitors(db, fake, now=NOW + timedelta(hours=5), max_runs=0)
        == []
    )


def test_removed_baseline_starts_a_fresh_baseline(db, fake):
    _, monitor = make_monitor(db)
    first = service.run_monitor(db, fake, monitor["id"], now=NOW)
    service.delete_search(db, first["record"]["id"])
    again = service.run_monitor(
        db, fake, monitor["id"], now=NOW + timedelta(minutes=30)
    )
    assert again["diff"] is None and again["summary"].startswith("Baseline captured")


# -- keys --------------------------------------------------------------------------


@pytest.fixture
def secrets_store():
    return store.SecretStore(Fernet.generate_key())


def test_key_resolution_prefers_the_account_key_over_the_environment(db, secrets_store):
    environ = {"SERPAPI_API_KEY": "env_key_0123456789abcdef"}
    resolved = service.resolve_key(db, secrets_store, environ)
    assert resolved.source == "env" and resolved.last4 == "cdef" and resolved.configured
    assert resolved.public() == {"source": "env", "last4": "cdef"}
    secrets_store.put(db, service.APP_KEY_SECRET, "app_key_0123456789abcdefgh", NOW)
    resolved = service.resolve_key(db, secrets_store, environ)
    assert resolved.source == "app" and resolved.key == "app_key_0123456789abcdefgh"
    assert resolved.last4 == "efgh"
    secrets_store.delete(db, service.APP_KEY_SECRET)
    assert service.resolve_key(db, secrets_store, {}).configured is False
    assert service.resolve_key(db, secrets_store, {}).public() == {
        "source": None,
        "last4": None,
    }
    assert len(resolved.fingerprint()) == 16


def test_unreadable_stored_key_is_reported_and_falls_back(db):
    first = store.SecretStore(Fernet.generate_key())
    first.put(db, service.APP_KEY_SECRET, "app_key_0123456789abcdefgh", NOW)
    second = store.SecretStore(Fernet.generate_key())
    resolved = service.resolve_key(db, second, {})
    assert not resolved.configured and "cannot be decrypted" in (resolved.error or "")
    fallback = service.resolve_key(
        db, second, {"SERPAPI_API_KEY": "env_key_0123456789abcdef"}
    )
    assert fallback.source == "env" and fallback.error


def test_set_app_key_validates_before_storing(db, secrets_store):
    class Factory:
        def __init__(self, good):
            self.good = good
            self.seen = []

        def __call__(self, key):
            self.seen.append(key)
            outer = self

            class Client:
                def account(self):
                    if key not in outer.good:
                        raise SerpApiError("invalid_api_key", "Invalid API key.", 401)
                    return {"plan_name": "Developer"}

            return Client()

    factory = Factory({"good_key_0123456789abcdef", "better_key_0123456789abcd"})
    with pytest.raises(ValueError, match="does not look like a SerpApi key"):
        service.set_app_key(db, secrets_store, factory, "short", NOW)
    with pytest.raises(ValueError):
        service.set_app_key(
            db, secrets_store, factory, "has spaces in it 0123456789", NOW
        )
    assert factory.seen == []
    account = service.set_app_key(
        db, secrets_store, factory, "  good_key_0123456789abcdef  ", NOW
    )
    assert account["plan_name"] == "Developer"
    assert service.resolve_key(db, secrets_store, {}).key == "good_key_0123456789abcdef"
    with pytest.raises(SerpApiError):
        service.set_app_key(
            db, secrets_store, factory, "wrong_key_0123456789abcdefg", NOW
        )
    assert service.resolve_key(db, secrets_store, {}).key == "good_key_0123456789abcdef"
    service.set_app_key(db, secrets_store, factory, "better_key_0123456789abcd", NOW)
    assert service.resolve_key(db, secrets_store, {}).key == "better_key_0123456789abcd"
    raw = b"".join(
        bytes(row[0]) for row in db.execute("SELECT ciphertext FROM airaapi_secrets")
    )
    assert b"better_key" not in raw
    assert service.remove_app_key(db, secrets_store) is True
    assert service.remove_app_key(db, secrets_store) is False
    assert not service.resolve_key(db, secrets_store, {}).configured


def test_account_is_cached_for_a_minute_and_forceable(db, fake):
    first = service.cached_account(db, fake, "fp", NOW)
    assert (
        first["plan_name"] == "Fake" and first["fetched_at"] == "2026-10-22T12:00:00Z"
    )
    service.cached_account(db, fake, "fp", NOW + timedelta(seconds=30))
    assert fake.account_calls == 1
    service.cached_account(db, fake, "fp", NOW + timedelta(seconds=61))
    assert fake.account_calls == 2
    service.cached_account(db, fake, "fp", NOW + timedelta(seconds=62), force=True)
    assert fake.account_calls == 3
    service.cached_account(db, fake, "other", NOW + timedelta(seconds=62))
    assert fake.account_calls == 4


def test_locations_are_cached_for_a_day_and_validated(db, fake):
    found = service.cached_locations(db, fake, " austin ", 5, NOW)
    assert found[0]["name"] == "Austin"
    service.cached_locations(db, fake, "austin", 5, NOW + timedelta(hours=23))
    assert fake.location_calls == 1
    service.cached_locations(db, fake, "austin", 5, NOW + timedelta(hours=25))
    assert fake.location_calls == 2
    service.cached_locations(db, fake, "austin", 99, NOW)
    assert fake.location_calls == 3  # limit is part of the key and clamped to 10
    with pytest.raises(ValueError, match="at least 2"):
        service.cached_locations(db, fake, "a", 5, NOW)
    with pytest.raises(ValueError, match="120"):
        service.cached_locations(db, fake, "x" * 121, 5, NOW)


def test_account_cache_never_holds_the_api_key():
    with SerpApiStub() as stub:
        from aira.airaapi.client import SerpApiClient

        key = "stub_valid_key_0123456789abcdef"
        connection = sqlite3.connect(":memory:")
        store.ensure_schema(connection)
        service.cached_account(connection, SerpApiClient(key, stub.base_url), "fp", NOW)
        cached = connection.execute("SELECT value FROM airaapi_cache").fetchone()[0]
        assert key not in cached and "api_key" not in cached
        assert stub.count("/account.json") == 1


def test_record_shape_for_unknown_engine_and_bad_json():
    row = {
        "id": "x",
        "engine": "retired_engine",
        "params_json": "{broken",
        "options_json": "{broken",
        "status": "ok",
        "source": "api",
        "created_at": "t",
    }
    record = service.record_from_row(row)
    assert record["engine_name"] == "retired_engine" and record["params"] == {}
    assert record["options"] == {} and record["cache_hit"] is False


def test_main_result_key_prefers_the_result_set():
    body = {
        "local_results": [{"a": 1}],
        "related_searches": [{"q": 1}] * 9,
        "organic_results": [{"b": 1}, {"b": 2}],
    }
    assert service.main_result_key("google", body) == "organic_results"
    assert (
        service.main_result_key(
            "google_maps", {"local_results": [1], "filters": [1, 2, 3]}
        )
        == "local_results"
    )
    assert (
        service.main_result_key("unknown_engine", {"things": [1], "more": [1, 2]})
        == "more"
    )
    assert (
        service.main_result_key("google", {"related_searches": [1]})
        == "related_searches"
    )
    assert service.main_result_key("google", {"organic_results": []}) is None
    assert service.main_result_key("google", "text") is None
    assert (
        service.count_results(
            "google", {"search_information": {"x": 1}, "knowledge_graph": {"t": 1}}
        )
        == 1
    )
    assert service.count_results("google", {"search_metadata": {"id": 1}}) == 0


# -- security regressions (adversarial review) ------------------------------------


def _file_db(tmp_path, name="shared.sqlite3"):
    path = tmp_path / name
    seed = sqlite3.connect(path)
    store.ensure_schema(seed)
    seed.commit()
    seed.close()
    return path


def test_identical_searches_started_together_cost_one_search(tmp_path):
    """A cold-cache stampede must spend one SerpApi search, not one per request."""
    path = _file_db(tmp_path)
    fake = FakeClient(delay=0.25)
    results: list[dict] = []
    errors: list[Exception] = []

    def work() -> None:
        connection = sqlite3.connect(path, timeout=20)
        try:
            results.append(
                service.run_search(
                    connection, fake, "google", {"q": "stampede"}, now=NOW
                )
            )
        except Exception as error:  # noqa: BLE001 - surfaced below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(fake.calls) == 1
    assert sorted(item["cache_hit"] for item in results) == [False] + [True] * 7
    assert sum(item["credits"] for item in results) == 1
    assert service._flights == {}


def test_uncacheable_searches_are_not_serialised(tmp_path):
    """no_cache means "ask again": two of them may run side by side."""
    path = _file_db(tmp_path)
    fake = FakeClient(delay=0.2)

    def work() -> None:
        connection = sqlite3.connect(path, timeout=20)
        try:
            service.run_search(
                connection,
                fake,
                "google",
                {"q": "x"},
                options={"no_cache": True},
                now=NOW,
            )
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(fake.calls) == 3 and fake.max_active > 1


def test_local_hourly_limit_holds_for_simultaneous_searches(tmp_path):
    path = _file_db(tmp_path)
    fake = FakeClient(delay=0.2)
    limits = service.Limits(hourly_limit=2)
    outcomes: list[str] = []

    def work(index: int) -> None:
        connection = sqlite3.connect(path, timeout=20)
        try:
            service.run_search(
                connection, fake, "google", {"q": f"q{index}"}, now=NOW, limits=limits
            )
            outcomes.append("ok")
        except SerpApiError as failure:
            outcomes.append(failure.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 2 and outcomes.count("local_rate_limit") == 4
    assert len(fake.calls) == 2


def test_cache_hits_and_failures_respect_the_history_cap(db, fake):
    """Identical requests copy the stored body; the row cap must hold without a live search."""
    limits = service.Limits(max_records=20)
    run(db, fake, limits=limits)
    for _ in range(80):
        run(db, fake, limits=limits)
    assert len(fake.calls) == 1
    assert store.count_searches(db) <= 20 + max(10, 20 // 20)
    # failures are rows too
    for index in range(60):
        fake.failures.append(SerpApiError("upstream_error", "boom", 500))
        with pytest.raises(SerpApiError):
            run(
                db,
                fake,
                {"q": f"fail {index}"},
                limits=limits,
                options={"no_cache": True},
            )
    assert store.count_searches(db) <= 20 + max(10, 20 // 20)


def test_csv_export_neutralises_formulas_in_cells_lists_and_headers():
    result = {
        "organic_results": [
            {
                "title": '=HYPERLINK("http://evil","x")',
                "tags": ["@SUM(1+1)", "ok"],
                "-evil header": "fine",
                "+cmd": 5,
                "n": -3,
                "tab": "\tcmd",
            }
        ]
    }
    text = service.results_to_csv("google", result)
    rows = list(csv.reader(io.StringIO(text)))
    header, row = rows[0], dict(zip(rows[0], rows[1], strict=True))
    assert all(not cell.startswith(("=", "+", "-", "@", "\t")) for cell in header)
    assert "'-evil header" in header and "'+cmd" in header
    assert row["title"].startswith("'=")
    assert row["tags"].startswith("'@")
    assert row["tab"].startswith("'\t")
    assert row["n"] == "-3"  # real numbers stay numbers


def test_resolved_key_never_prints_the_secret():
    resolved = service.ResolvedKey("sk_live_SECRET_0123456789", "app", "6789")
    assert "SECRET" not in repr(resolved) and "SECRET" not in str(resolved)
    assert resolved.public() == {"source": "app", "last4": "6789"}


def test_diff_survives_absurdly_nested_bodies():
    def nest(depth: int, leaf):
        value = leaf
        for _ in range(depth):
            value = {"a": value}
        return value

    before = {"organic_results": [{"title": "t", "link": "l"}], "deep": nest(900, 1)}
    after = {"organic_results": [{"title": "t", "link": "l"}], "deep": nest(900, 2)}
    diff = service.diff_results("google", before, after)
    assert diff["changed_any"] and diff["counts"]["changed"] >= 1


def test_monitor_run_is_not_derailed_by_an_unreadable_previous_result(db, fake):
    saved = service.create_saved(
        db, {"name": "m", "engine": "google", "params": {"q": "x"}}, now=NOW
    )
    monitor = service.create_monitor(
        db, {"saved_id": saved["id"], "interval_minutes": 15}, now=NOW
    )
    first = service.run_monitor(db, fake, monitor["id"], now=NOW)
    previous = first["record"]["id"]
    bomb = store.pack_text(
        "x"
    )  # not JSON: reading it back as a result must not break the run
    db.execute(
        "UPDATE airaapi_searches SET result_blob = ? WHERE id = ?", (bomb, previous)
    )
    again = service.run_monitor(
        db, fake, monitor["id"], now=NOW + timedelta(minutes=20)
    )
    assert again["diff"] is None and again["record"]["status"] == "ok"


def test_locations_cache_is_bounded(db, fake, monkeypatch):
    monkeypatch.setattr(service, "LOCATION_CACHE_MAX_ENTRIES", 5)
    for index in range(12):
        service.cached_locations(db, fake, f"city {index}", 10, NOW)
    count = db.execute(
        "SELECT COUNT(*) FROM airaapi_cache WHERE key LIKE 'locations:%'"
    ).fetchone()[0]
    assert count == 5
