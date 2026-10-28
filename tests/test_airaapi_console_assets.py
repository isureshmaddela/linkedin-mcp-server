"""Static checks for the API console front end (views-airaapi.js and its CSS block).

The console is plain browser JavaScript with no build step, so these tests guard the
wiring that is easy to break: script order in index.html, the tabs the spec lists, that
every backend route the console calls exists, and that nothing writes server data with
innerHTML.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aira.airaapi import routes  # noqa: F401  (importing registers the routes)
from aira.airaapi.http import AIRA_ROUTES

STATIC = Path(__file__).resolve().parent.parent / "auto_apply_assistant"
SOURCE = (STATIC / "views-airaapi.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
TABS = (
    "overview",
    "playground",
    "engines",
    "history",
    "locations",
    "monitors",
    "rank",
    "keys",
    "earnings",
    "account",
    "docs",
    "status",
)


def test_script_loads_after_the_kit_and_before_the_playground() -> None:
    order = re.findall(r'src="/static/([^"]+)"', INDEX)
    assert (
        order.index("views-serp-kit.js")
        < order.index("views-airaapi.js")
        < order.index("views-playground.js")
    )


def test_every_spec_tab_is_registered() -> None:
    declared = re.search(r"const TABS = \[(.*?)\];", SOURCE, re.S)
    assert declared is not None
    assert tuple(re.findall(r'\["(\w+)", "', declared.group(1))) == TABS
    for tab in TABS:
        assert re.search(rf"\btabs\.{tab}\s*=\s*async", SOURCE), tab


def _route_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [(route.method, route.pattern) for route in AIRA_ROUTES]


def _calls() -> set[tuple[str, str]]:
    """(method, path) pairs for every literal ${BASE}/... call in the source."""
    found: set[tuple[str, str]] = set()
    for match in re.finditer(
        r"api\(`\$\{BASE\}((?:/(?:[\w-]+|\$\{[^}]+\})|\$\{[^}]+\})*)[^`]*`(?:,\s*\{([^}]*)\})?",
        SOURCE,
    ):
        path = re.sub(r"(?<=/)\$\{[^}]+\}", "a" * 32, match.group(1))
        path = re.sub(
            r"\$\{[^}]+\}", "", path
        )  # a query suffix such as ${refresh ? ... : ...}
        method = re.search(r'method:\s*"(\w+)"', match.group(2) or "")
        found.add((method.group(1) if method else "GET", path))
    return found


def test_every_backend_call_hits_a_real_route() -> None:
    patterns = _route_patterns()
    calls = _calls()
    assert len(calls) > 20
    for method, path in calls:
        target = f"/api/airaapi{path}"
        assert any(
            m == method and pattern.fullmatch(target) for m, pattern in patterns
        ), (method, target)


def test_server_data_is_never_written_with_innerhtml() -> None:
    # The h() helper's html attribute is only used for the static icon paths.
    assert "innerHTML" not in SOURCE
    assert SOURCE.count("html:") == 1


def test_no_secret_is_kept_in_the_dom_or_storage() -> None:
    assert "localStorage" not in SOURCE and "sessionStorage" not in SOURCE
    assert 'type: "password"' in SOURCE and 'autocomplete: "new-password"' in SOURCE


def test_console_css_block_is_closed() -> None:
    assert "/* ==== API console" in CSS
    assert CSS.count("/* ==== end API console ==== */") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_console_parses() -> None:
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [node, "--check", str(STATIC / "views-airaapi.js")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_coverage_page_lists_parameters_apis_and_errors_with_known_statuses() -> None:
    labels = re.search(r"const STATUS_LABEL = \{(.*?)\};", SOURCE, re.S)
    assert labels is not None
    known = set(re.findall(r"(\w+):", labels.group(1)))
    for table in ("PARAMETERS", "APIS", "ERRORS"):
        block = re.search(rf"const {table} = \[(.*?)\n  \];", SOURCE, re.S)
        assert block is not None, table
        used = set(re.findall(r'^\s*\[".*?", "(\w+)", ', block.group(1), re.M))
        assert used and used <= known, (table, used - known)
    for needle in ("output=md", "zero_trace", "json_restrictor", "410", "async"):
        assert needle in SOURCE


def test_usage_can_be_exported_and_google_jobs_records_can_be_saved_to_aira() -> None:
    assert "aira-api-usage-30-days.csv" in SOURCE
    assert "/records/${record.id}/jobs" in SOURCE and "Save jobs to Aira" in SOURCE
