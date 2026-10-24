"""A stand-in SerpApi server for tests and demos (never talks to the network).

Start it, point Aira at it with ``AIRA_SERPAPI_BASE_URL`` and every SerpApi call
Aira makes lands here::

    stub = SerpApiStub()
    stub.start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", stub.base_url)
    ...
    assert stub.search_calls == 1     # cache hits must not reach the stub
    stub.stop()

Endpoints: ``/search.json``, ``/account.json``, ``/locations.json`` and
``/searches/{id}.json|.html``. Responses are fixture-shaped: rich recorded-shape
fixtures in ``tests/fixtures/serpapi/`` for google, google_jobs, google_news,
google_maps, google_maps_reviews, google_trends, google_flights, google_hotels,
youtube, google_shopping and bing; every other engine gets a minimal body built
from its catalog ``result_keys``. Every body carries
``search_metadata.fixture = true`` so nothing here can be mistaken for live data.

Keys: only the strings in ``valid_keys`` are accepted (default
``stub_valid_key_0123456789abcdef``); anything else answers 401.

Magic substrings in any parameter value simulate failures:

===================  ==========================================================
``__invalid_key__``  401 invalid API key
``__rate_limit__``   429 hourly throughput exceeded
``__out_of_searches__``  429 account out of searches
``__empty__``        200 with ``error`` "hasn't returned any results", no list
``__server_error__`` 500 every time (Aira retries once)
``__flaky__``        500 on the first call for that query, then success
``__search_failed__``  200 with ``search_metadata.status = "Error"``
``__huge__``         a result larger than 1 MB (to exercise the size cap)
===================  ==========================================================

``stub.variant`` (an int) changes results between runs: positive ``n`` appends
``n`` synthetic items to the main list, negative removes ``-n`` items from the
front. ``stub.async_polls`` is how many archive polls answer "Processing" before
an ``async=true`` search completes. ``stub.calls`` records every request (the API
key is stored only as a boolean ``key_ok``) and ``stub.count(...)`` filters it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from aira.airaapi import catalog

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "serpapi"
DEFAULT_KEY = "stub_valid_key_0123456789abcdef"
RICH_ENGINES = {
    "google",
    "google_jobs",
    "google_news",
    "google_maps",
    "google_maps_reviews",
    "google_trends",
    "google_flights",
    "google_hotels",
    "youtube",
    "google_shopping",
    "bing",
}
_DICT_KEYS = {
    "search_information",
    "knowledge_graph",
    "pagination",
    "serpapi_pagination",
    "product_results",
    "place_results",
    "page_information",
    "price_insights",
    "about",
    "text_content",
    "meta_tags",
    "open_graph",
}
_MAIN_LIST_KEYS = (
    "organic_results",
    "jobs_results",
    "news_results",
    "shopping_results",
    "local_results",
    "video_results",
    "properties",
    "best_flights",
    "reviews",
)


def digest_of(key: str) -> str:
    """Short digest identifying which key a call used (tests compare digests, not keys)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:10] if key else ""


def fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def with_pixel_positions(body: Any) -> Any:
    """A copy of an archived body as ``json_with_pixel_position`` returns it.

    Every item of a top-level result list gets a ``rectangle`` (stacked down a
    1,280-pixel-wide page in a 652-pixel column, like Google's desktop layout),
    each list gets a parent rectangle, and ``pixel_position_information``
    carries the viewport.
    """
    if not isinstance(body, dict):
        return body
    out = copy.deepcopy(body)
    parents: dict[str, Any] = {}
    y = 180.0
    for key, value in out.items():
        if key in {
            "search_metadata",
            "search_parameters",
            "serpapi_pagination",
            "pagination",
        }:
            continue
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            continue
        top = y
        for item in value:
            if isinstance(item, dict):
                item["rectangle"] = {"x": 180, "y": y, "width": 652, "height": 120.5}
                y += 132
        parents[key] = {"x": 172, "y": top - 8, "width": 668, "height": y - top + 8}
        y += 40
    out["pixel_position_information"] = {
        "viewport_width": 1280,
        "viewport_height": 900,
        "time_taken": 1.25,
        "parent_rectangles": parents,
    }
    out.setdefault("search_metadata", {})["fixture"] = True
    return out


def generic_body(engine_id: str) -> dict[str, Any]:
    """A minimal body shaped from the engine's catalog result keys."""
    found = catalog.engine(engine_id)
    body: dict[str, Any] = {
        "search_metadata": {
            "id": "{{id}}",
            "status": "Success",
            "json_endpoint": "{{base}}/searches/{{id}}.json",
            "fixture": True,
            "fixture_note": "Minimal fixture generated from the catalog result keys. Not live SerpApi data.",
        }
    }
    for key in found.result_keys:
        if key == "error":
            # a documented failure field (google_scholar_cite lists it), never part of a successful body
            continue
        fields = found.result_item_fields.get(key)
        if fields:
            body[key] = [
                {
                    name: _sample_field(name, index, key)
                    for name in fields
                    if name not in {"link_list"}
                }
                for index in range(1, 4)
            ]
        elif key in _DICT_KEYS or key.endswith(("_info", "_information", "_details")):
            body[key] = {"fixture": True}
        else:
            body[key] = [
                {
                    "position": index,
                    "title": f"{key} item {index}",
                    "link": f"https://example.com/{key}/{index}",
                }
                for index in range(1, 4)
            ]
    return body


def _sample_field(name: str, index: int, key: str) -> Any:
    lowered = name.lower()
    if any(part in lowered for part in ("link", "url", "thumbnail", "image", "logo")):
        return f"https://example.com/{key}/{name}/{index}"
    if any(
        part in lowered
        for part in ("price", "rating", "position", "count", "views", "reviews")
    ):
        return index
    return f"{name} {index}"


class Call:
    """One request the stub received."""

    __slots__ = ("path", "engine", "params", "status", "key_ok", "key_digest")

    def __init__(
        self,
        path: str,
        engine: str,
        params: dict[str, str],
        status: int,
        key_ok: bool,
        key_digest: str = "",
    ) -> None:
        self.key_digest = key_digest
        self.path = path
        self.engine = engine
        self.params = params
        self.status = status
        self.key_ok = key_ok

    def __repr__(self) -> str:
        return f"Call({self.path} engine={self.engine} status={self.status})"


class SerpApiStub:
    def __init__(self, valid_keys: tuple[str, ...] = (DEFAULT_KEY,)) -> None:
        self.valid_keys = set(valid_keys)
        self.variant = 0
        self.async_polls = 1
        self.calls: list[Call] = []
        self.archive: dict[str, tuple[dict[str, Any] | None, str]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._flaky_seen: set[str] = set()
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""
        self.account_template = load_fixture("account")
        self.monthly_allowance = 5000
        self.pixel_calls = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> SerpApiStub:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                stub._handle(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        server = self._server
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> SerpApiStub:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # -- inspection -----------------------------------------------------------

    def count(self, path: str | None = None, engine: str | None = None) -> int:
        return sum(
            1
            for call in self.calls
            if (path is None or call.path == path)
            and (engine is None or call.engine == engine)
        )

    @property
    def search_calls(self) -> int:
        return self.count("/search.json")

    @property
    def live_searches(self) -> int:
        """Searches that succeeded (what SerpApi would bill)."""
        return sum(
            1
            for call in self.calls
            if call.path == "/search.json" and call.status == 200
        )

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.archive.clear()
            self._pending.clear()
            self._flaky_seen.clear()
            self.pixel_calls = 0

    # -- request handling ---------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(handler.path)
        query = {name: values[0] for name, values in parse_qs(parts.query).items()}
        key_ok = query.get("api_key", "") in self.valid_keys
        path = parts.path
        engine = query.get("engine", "")
        status, body, kind = 404, {"error": "Not found."}, "application/json"
        try:
            if path == "/search.json":
                status, body, kind = self._search(query, key_ok)
            elif path == "/account.json":
                status, body, kind = self._account(query, key_ok)
            elif path == "/locations.json":
                status, body, kind = self._locations(query)
            elif path.startswith("/searches/"):
                status, body, kind = self._archive(path, key_ok)
        except Exception as error:  # pragma: no cover - stub bug surfaced to the test
            status, body, kind = (
                500,
                {"error": f"stub failure: {error!r}"},
                "application/json",
            )
        with self._lock:
            self.calls.append(
                Call(
                    "/searches" if path.startswith("/searches/") else path,
                    engine,
                    {k: v for k, v in query.items() if k != "api_key"},
                    status,
                    key_ok,
                    digest_of(query.get("api_key", "")),
                )
            )
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", f"{kind}; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _magic(query: dict[str, str]) -> str:
        joined = " ".join(value for name, value in query.items() if name != "api_key")
        for marker in (
            "__invalid_key__",
            "__rate_limit__",
            "__out_of_searches__",
            "__empty__",
            "__server_error__",
            "__flaky__",
            "__search_failed__",
            "__huge__",
        ):
            if marker in joined:
                return marker
        return ""

    def _search(self, query: dict[str, str], key_ok: bool) -> tuple[int, Any, str]:
        magic = self._magic(query)
        if not key_ok or magic == "__invalid_key__":
            return (
                401,
                {"error": "Invalid API key. Provide a valid key."},
                "application/json",
            )
        engine = query.get("engine", "google")
        try:
            catalog.engine(engine)
        except LookupError:
            return (
                400,
                {"error": f"Unsupported `{engine}` search engine."},
                "application/json",
            )
        if magic == "__rate_limit__":
            return (
                429,
                {"error": "Your account hourly searches limit exceeded."},
                "application/json",
            )
        if magic == "__out_of_searches__":
            return (
                429,
                {"error": "Your account has run out of searches."},
                "application/json",
            )
        if magic == "__server_error__":
            return 500, {"error": "Internal error."}, "application/json"
        if magic == "__flaky__":
            marker = json.dumps(sorted(query.items()))
            with self._lock:
                first = marker not in self._flaky_seen
                self._flaky_seen.add(marker)
            if first:
                return 500, {"error": "Temporary upstream failure."}, "application/json"
        search_id = uuid.uuid4().hex[:24]
        base = self._body_for(engine, query, search_id, magic)
        if magic == "__search_failed__":
            base["search_metadata"]["status"] = "Error"
            base["error"] = "We couldn't get valid results for this search."
            return 200, base, "application/json"
        html = f"<html><body><h1>Fixture page for {engine}</h1><p>{json.dumps(query.get('q', ''))}</p></body></html>"
        output = query.get("output", "json")
        if output == "html":
            self.archive[search_id] = (base, html)
            return 200, html.encode("utf-8"), "text/html"
        if output == "md":
            return (
                200,
                f"# Fixture markdown for {engine}\n".encode("utf-8"),
                "text/markdown",
            )
        if query.get("async") == "true":
            queued = {
                "search_metadata": {
                    "id": search_id,
                    "status": "Queued",
                    "json_endpoint": f"{self.base_url}/searches/{search_id}.json",
                    "fixture": True,
                },
                "search_parameters": base["search_parameters"],
            }
            with self._lock:
                self._pending[search_id] = {"polls": 0, "body": base}
                self.archive[search_id] = (base, html)
            return 200, queued, "application/json"
        self.archive[search_id] = (base, html)
        restrictor = query.get("json_restrictor", "")
        if restrictor:
            wanted = {
                item.strip().split("[")[0].split(".")[0]
                for item in restrictor.split(",")
                if item.strip()
            }
            base = {
                key: value
                for key, value in base.items()
                if key in wanted or key in {"search_metadata", "search_parameters"}
            }
        return 200, base, "application/json"

    def _body_for(
        self, engine: str, query: dict[str, str], search_id: str, magic: str
    ) -> dict[str, Any]:
        body = load_fixture(engine) if engine in RICH_ENGINES else generic_body(engine)
        text = (
            json.dumps(body)
            .replace("{{base}}", self.base_url)
            .replace("{{id}}", search_id)
        )
        body = json.loads(text)
        body["search_parameters"] = {
            **{name: value for name, value in query.items() if name != "api_key"},
            "engine": engine,
        }
        self._linkedin_xray(engine, query, body)
        self._service_requests(engine, query, body)
        if magic == "__empty__":
            for key in list(body):
                if key not in {"search_metadata", "search_information"}:
                    del body[key]
            body["error"] = "Google hasn't returned any results for this query."
            return body
        if magic == "__huge__":
            filler = "x" * 4096
            body["organic_results"] = [
                {"position": i, "title": f"Huge {i}", "snippet": filler}
                for i in range(400)
            ]
        self._apply_variant(body)
        return body

    @staticmethod
    def _linkedin_xray(
        engine: str, query: dict[str, str], body: dict[str, Any]
    ) -> None:
        """``site:linkedin.com`` searches on the google engine answer with LinkedIn-shaped results."""
        q = query.get("q", "")
        if engine != "google" or "site:linkedin.com" not in q:
            return
        if "site:linkedin.com/company" in q:
            body["organic_results"] = [
                {
                    "position": i + 1,
                    "title": f"{name} | LinkedIn",
                    "link": f"https://www.linkedin.com/company/{slug}",
                    "snippet": f"{name} | Cloud software company. Austin, Texas · 51-200 employees.",
                }
                for i, (name, slug) in enumerate(
                    [
                        ("Northwind Cloud", "northwind-cloud"),
                        ("Contoso Data", "contoso-data"),
                    ]
                )
            ]
            return
        people = [
            (
                "Jane Doe",
                "VP Sales",
                "Northwind Cloud",
                "jane-doe-1a2b3c",
                "Austin, Texas, United States",
            ),
            (
                "Raj Patel",
                "Head of Sales",
                "Contoso Data",
                "raj-patel-9z8y7x",
                "Dallas, Texas, United States",
            ),
            (
                "Ana Silva",
                "Sales Director at Fabrikam",
                "",
                "ana-silva-4d5e6f",
                "Remote",
            ),
        ]
        results = [
            {
                "position": i + 1,
                "title": f"{name} - {job}{(' - ' + company) if company else ''} | LinkedIn",
                "link": f"https://www.linkedin.com/in/{slug}",
                "snippet": f"{where} · 500+ connections on LinkedIn. View {name}'s profile.",
            }
            for i, (name, job, company, slug, where) in enumerate(people)
        ]
        results.append(  # a LinkedIn page that is not a profile: must be skipped
            {
                "position": 4,
                "title": "Sales leadership trends | LinkedIn",
                "link": "https://www.linkedin.com/pulse/sales-leadership-trends",
                "snippet": "An article.",
            }
        )
        body["organic_results"] = results

    @staticmethod
    def _service_requests(
        engine: str, query: dict[str, str], body: dict[str, Any]
    ) -> None:
        """Searches for people asking for web, marketing, content or ads help answer with public-post shaped results."""
        q = query.get("q", "")
        if engine != "google" or "looking for" not in q or "site:linkedin.com" in q:
            return
        body["organic_results"] = [
            {
                "position": 1,
                "title": "Looking for a web designer for my bakery : r/smallbusiness",
                "link": "https://www.reddit.com/r/smallbusiness/comments/abc111/looking_for_a_web_designer/",
                "snippet": "I own a small bakery and need a new website. Budget around $1,500. Anyone recommend a web developer? Need it ASAP.",
                "date": "2 days ago",
            },
            {
                "position": 2,
                "title": "Need someone to run Google Ads for our dental clinic - Quora",
                "link": "https://www.quora.com/Who-can-run-Google-Ads-for-a-dental-clinic",
                "snippet": "We are looking for a PPC specialist to manage Google Ads for a dental practice in Austin.",
                "date": "1 week ago",
            },
            {
                "position": 3,
                "title": "Hiring a content writer for our blog | LinkedIn",
                "link": "https://www.linkedin.com/posts/someone_hiring-content-writer-activity-123",
                "snippet": "We are hiring a freelance content writer, paid per article.",
            },
        ]

    def _apply_variant(self, body: dict[str, Any]) -> None:
        if not self.variant:
            return
        key = next(
            (name for name in _MAIN_LIST_KEYS if isinstance(body.get(name), list)), None
        )
        if key is None:
            key = next(
                (
                    name
                    for name, value in body.items()
                    if isinstance(value, list) and value and isinstance(value[0], dict)
                ),
                None,
            )
        if key is None:
            return
        items = body[key]
        if self.variant > 0:
            for index in range(self.variant):
                extra = copy.deepcopy(items[-1]) if items else {}
                extra["title"] = f"Added by variant {index + 1}"
                extra["link"] = f"https://example.com/variant/{index + 1}"
                extra.pop("job_id", None)
                extra.pop("place_id", None)
                extra.pop("product_id", None)
                items.append(extra)
        else:
            del items[: min(-self.variant, len(items))]

    def _account(self, query: dict[str, str], key_ok: bool) -> tuple[int, Any, str]:
        if not key_ok:
            return (
                401,
                {"error": "Invalid API key. Provide a valid key."},
                "application/json",
            )
        account = copy.deepcopy(self.account_template)
        used = 100 + self.live_searches
        account["api_key"] = query.get("api_key")
        account["this_month_usage"] = used
        account["plan_searches_left"] = max(self.monthly_allowance - used, 0)
        account["total_searches_left"] = (
            account["plan_searches_left"] + account["extra_credits"]
        )
        account["this_hour_searches"] = self.live_searches
        return 200, account, "application/json"

    def _locations(self, query: dict[str, str]) -> tuple[int, Any, str]:
        needle = query.get("q", "").strip().lower()
        try:
            limit = max(1, min(int(query.get("limit", "10")), 10))
        except ValueError:
            limit = 10
        rows = [
            row
            for row in load_fixture("locations")
            if not needle
            or needle in row["name"].lower()
            or needle in row["canonical_name"].lower()
        ]
        return 200, rows[:limit], "application/json"

    def _archive(self, path: str, key_ok: bool) -> tuple[int, Any, str]:
        if not key_ok:
            return (
                401,
                {"error": "Invalid API key. Provide a valid key."},
                "application/json",
            )
        name = path.rsplit("/", 1)[-1]
        search_id, _, suffix = name.partition(".")
        if search_id.startswith("expired"):
            return 410, {"error": "This search has expired."}, "application/json"
        if search_id not in self.archive:
            return 404, {"error": "Search not found."}, "application/json"
        body, html = self.archive[search_id]
        if suffix == "html":
            return 200, html.encode("utf-8"), "text/html"
        if suffix == "json_with_pixel_position":
            self.pixel_calls += 1
            return 200, with_pixel_positions(body), "application/json"
        with self._lock:
            pending = self._pending.get(search_id)
            if pending is not None:
                pending["polls"] += 1
                if pending["polls"] <= self.async_polls:
                    return (
                        200,
                        {
                            "search_metadata": {
                                "id": search_id,
                                "status": "Processing",
                                "fixture": True,
                            }
                        },
                        "application/json",
                    )
                del self._pending[search_id]
        return 200, body, "application/json"
