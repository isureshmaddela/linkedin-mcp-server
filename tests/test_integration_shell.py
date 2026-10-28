"""How the API and Careers sections join the rest of the app.

Static checks on the navigation, shortcuts, script order and the Dashboard, Settings and Jobs wiring (plain browser
JavaScript has no build step, so these guard what is easy to break), plus HTTP journeys that the new Dashboard widgets,
the Settings card and the Jobs source list depend on: they all have to follow a SerpApi key stored in the app or set in
the environment, and no response may carry the key.
"""

# ruff: noqa: F811  (the imported fixtures are used by name as test arguments)
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from aira.airaapi.http import AIRA_ROUTES, load_route_modules
from test_airaapi_routes import (  # noqa: F401
    KEY,
    api,
    connect_key,
    connected,
    env,
)

STATIC = Path(__file__).resolve().parent.parent / "auto_apply_assistant"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
WIDGETS = (STATIC / "views-widgets.js").read_text(encoding="utf-8")
DASHBOARD = (STATIC / "views-applications.js").read_text(encoding="utf-8")
SETTINGS = (STATIC / "views-settings.js").read_text(encoding="utf-8")
JOBS = (STATIC / "views-jobs.js").read_text(encoding="utf-8")

load_route_modules()


def _nav() -> list[dict[str, str]]:
    block = re.search(r"const NAV = \[(.*?)\n\];", APP_JS, re.S)
    assert block is not None
    return [
        dict(re.findall(r'(\w+): (?:"([^"]*)"|true)', line))
        for line in block.group(1).splitlines()
        if line.strip().startswith("{")
    ]


def test_new_sections_are_in_the_navigation_with_icons_and_chords() -> None:
    nav = {item["id"]: item for item in _nav()}
    assert nav["airaapi"]["label"] == "API" and nav["airaapi"]["key"] == "p"
    assert nav["airaapi"]["app"] == "api"
    # The research workspace keeps its route id; inside the Careers app it is called Research.
    assert nav["careers"]["label"] == "Research" and nav["careers"]["app"] == "careers"
    # Leads is a section of Marketing (with its own `g l` chord); Settings belongs to the account.
    assert nav["leads"]["app"] == "marketing" and nav["leads"]["key"] == "l"
    assert nav["settings"]["app"] == "account"
    assert nav["hub"]["app"] == "account" and nav["hub"]["key"] == "0"
    assert nav["developers"]["app"] == "api"
    # Fewer apps, each with sections: Commerce is Shop and Bookings, Courses & Events is Academy and Events,
    # Social holds Family. A section is opened inside its app, so it has no chord of its own.
    sections_of = {
        "studio": {"studio"},
        "marketing": {"marketing", "leads"},
        "service": {"service"},
        "commerce": {"shop", "bookings"},
        "experiences": {"academy", "events"},
        "social": {"social", "family"},
        "care": {"care"},
        "learn": {"learn"},
        "play": {"play"},
        "planner": {"planner"},
        "news": {"news"},
        "videos": {"videos"},
        "journey": {"journey"},
        "ai": {"ai"},
        "stocks": {"stocks"},
        "ads": {"ads"},
    }
    for app_id, ids in sections_of.items():
        assert {i for i, item in nav.items() if item["app"] == app_id} == ids, app_id
        for section in ids - {"leads"}:
            assert "key" not in nav[section], section
    assert nav["today"]["app"] == "account" and nav["display"]["app"] == "account"
    assert len(nav) == 34
    keys = [item["key"] for item in nav.values() if "key" in item]
    assert len(set(keys)) == len(keys), "two sections share a g-chord"
    for item in nav.values():
        assert re.search(rf"\b{item['icon']}: '<svg", APP_JS), item["icon"]
    # Every section belongs to an app (or to the account, shown under every app).
    assert {item["app"] for item in nav.values()} == {
        "careers",
        "api",
        "account",
        *sections_of,
    }
    # Phones keep four quick items for the Careers app; the rest sit behind More.
    quick = [
        line
        for line in APP_JS.splitlines()
        if "id:" in line and "quick: true" in line and 'app: "careers"' in line
    ]
    assert len(quick) == 4


def test_chords_and_help_are_driven_by_the_same_list() -> None:
    assert "NAV.find((item) => item.key === event.key)" in APP_JS
    assert "APPS.find((item) => item.key === event.key" in APP_JS
    assert "sections.map((item) => [`g then ${item.key}`, item.label])" in APP_JS


def test_the_app_keys_do_not_collide_with_section_chords() -> None:
    block = re.search(r"const APPS = \[(.*?)\n\];", APP_JS, re.S)
    assert block is not None
    app_keys = dict(re.findall(r'id: "(\w+)".*?key: "(\w)"', block.group(1)))
    section_keys = {item["id"]: item["key"] for item in _nav() if "key" in item}
    # An app's key may only coincide with its own home section (Developers: `g p` opens the API console).
    for app_id, key in app_keys.items():
        owners = [
            section
            for section, section_key in section_keys.items()
            if section_key == key
        ]
        assert owners in ([], [app_id], ["airaapi"] if app_id == "api" else [app_id]), (
            app_id,
            owners,
        )


def test_every_nav_section_has_a_registered_view() -> None:
    scripts = "".join(
        path.read_text(encoding="utf-8")
        for path in [*STATIC.glob("views-*.js"), STATIC / "life.js"]
    )
    for item in _nav():
        assert re.search(rf"Monosage\.views\.{item['id']}\s*=", scripts), item["id"]


def test_script_order_and_every_script_exists() -> None:
    order = re.findall(r'src="/static/([^"]+)"', INDEX)
    for name in order:
        assert (STATIC / name).is_file(), name
    assert order[0] == "app.js"
    assert order.index("views-careers.js") < order.index("views-widgets.js")
    assert order.index("views-applications.js") < order.index("views-widgets.js")


def test_dashboard_mounts_the_widgets_and_degrades_without_them() -> None:
    assert "Aira.dashboardWidgets ? Aira.dashboardWidgets() : []" in DASHBOARD
    assert "Aira.dashboardWidgets = " in WIDGETS
    # The widgets load after the page draws; nothing awaits them.
    assert "await Aira.dashboardWidgets" not in DASHBOARD


def test_widgets_and_settings_call_real_routes() -> None:
    patterns = [(route.method, route.pattern) for route in AIRA_ROUTES]
    assert 'api("/api/airaapi/overview", { silent: true })' in WIDGETS
    assert 'api("/api/careers/overview", { silent: true })' in WIDGETS
    assert 'api("/api/airaapi/overview")' in SETTINGS
    for path in ("/api/airaapi/overview", "/api/careers/overview"):
        assert any(
            method == "GET" and pattern.fullmatch(path) for method, pattern in patterns
        ), path


def test_widgets_build_nodes_without_innerhtml_or_storage() -> None:
    assert "innerHTML" not in WIDGETS
    assert "localStorage" not in WIDGETS


def test_jobs_view_refreshes_sources_and_links_to_the_key_page() -> None:
    assert "sourcesLoad = null;" in JOBS
    assert "#/api?tab=account" in JOBS
    assert "Google Jobs needs a SerpApi key" in JOBS


def test_security_headers_allow_https_images_and_nothing_looser(env, api) -> None:
    with urllib.request.urlopen(env.base + "/static/app.js", timeout=30) as response:
        csp = response.headers["Content-Security-Policy"]
    directives = dict(
        part.strip().split(" ", 1) for part in csp.split(";") if part.strip()
    )
    assert directives["img-src"] == "'self' data: blob: https:"
    assert directives["script-src"] == "'self'"
    assert directives["connect-src"] == "'self'"
    assert directives["default-src"] == "'self'"
    assert directives["object-src"] == "'none'"


def test_new_static_files_are_served(env, api) -> None:
    for name in ("views-widgets.js", "views-careers.js", "views-airaapi.js"):
        with urllib.request.urlopen(f"{env.base}/static/{name}", timeout=30) as reply:
            assert reply.status == 200
            assert "javascript" in reply.headers["Content-Type"]


def test_widget_endpoints_work_before_and_after_a_key_is_connected(env, api) -> None:
    overview = api.ok("GET", "/api/airaapi/overview")
    assert overview["configured"] is False and overview["account"] is None
    careers = api.ok("GET", "/api/careers/overview")
    assert careers["configured"] is False
    assert careers["alerts"] == [] and careers["saved_searches"] == []
    connect_key(api)
    overview = api.ok("GET", "/api/airaapi/overview")
    assert overview["configured"] is True and overview["key"]["source"] == "app"
    assert overview["account"]["total_searches_left"] >= 0
    assert api.ok("GET", "/api/careers/overview")["configured"] is True
    for key in ("usage", "monitors", "recent"):
        assert key in overview
    for key in ("today", "last_30_days", "cache_hit_rate", "by_day"):
        assert key in overview["usage"]


def test_key_state_reaches_every_consumer_and_the_key_never_leaks(
    env, api, monkeypatch
) -> None:
    def flags() -> tuple[bool, bool, bool]:
        sources = api.ok("GET", "/api/sources")["sources"]
        google = next(item for item in sources if item["source"] == "google_jobs")
        return (
            google["connected"],
            api.ok("GET", "/api/status")["google_jobs"],
            api.ok("GET", "/api/airaapi/overview")["configured"],
        )

    assert flags() == (False, False, False)
    connect_key(api)
    assert flags() == (True, True, True)
    api.ok("DELETE", "/api/airaapi/key")
    assert flags() == (False, False, False)
    # A key set only in the environment counts everywhere too, and cannot be removed from the app.
    monkeypatch.setenv("SERPAPI_API_KEY", KEY)
    assert flags() == (True, True, True)
    overview = api.ok("GET", "/api/airaapi/overview")
    assert overview["key"]["source"] == "env"
    status, body, _ = api.call("DELETE", "/api/airaapi/key")
    assert status == 409 and body["code"] == "conflict"
    for payload in env.transcript:
        assert KEY.encode() not in payload


def test_sources_hint_points_to_the_key_page(env, api) -> None:
    sources = api.ok("GET", "/api/sources")["sources"]
    google = next(item for item in sources if item["source"] == "google_jobs")
    assert "API > Account" in google["connection_hint"]
    assert "API" in google["access_method"]
