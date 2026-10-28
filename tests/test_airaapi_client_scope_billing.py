"""Developers app, third round: what a client's key may see, honest monthly billing, hosted rules, household profiles.

Route tests run the real request handler against the stand-in SerpApi server (``tests/serpapi_stub.py``) with the
fixtures of ``test_airaapi_routes.py``; billing windows are tested on a database directly with fixed dates; the
hosted tests drive the gateway with ``tests/cloud_support.py``. Nothing calls SerpApi.
"""

# ruff: noqa: F811  (the fixtures imported from test_airaapi_routes are parameters here)
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aira.airaapi import earnings, rank, store
from aira.airaapi import http as airaapi_http
from cloud_support import build_org, cloud, sign_in  # noqa: F401  (cloud is a fixture)
from serpapi_stub import DEFAULT_KEY, SerpApiStub
from test_airaapi_routes import (  # noqa: F401  (pytest fixtures)
    api,
    connected,
    env,
)

ROOT = Path(__file__).resolve().parents[1]


def _key(api_client, **terms) -> tuple[dict, str]:
    created = api_client.ok(
        "POST",
        "/api/airaapi/keys",
        {"name": "key", "hourly_limit": 0, **terms},
        expect=201,
    )
    return created["key"], created["secret"]


def _texts(listing: dict) -> list[str]:
    return [
        json.dumps(item["params"]) + " " + item["label"] for item in listing["items"]
    ]


# -- a client's key sees only its own searches -------------------------------------------


def test_a_client_key_lists_and_opens_only_its_own_searches(env, connected):
    connected.ok(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "owner private plan"}},
    )
    project = connected.ok(
        "POST",
        "/api/airaapi/rank",
        {"domain": "example.com", "keywords": ["coffee"], "interval_minutes": 0},
        expect=201,
    )
    connected.ok("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    _, secret_a = _key(connected, client_name="Client A", allowed_engines=["google"])
    _, secret_b = _key(connected, client_name="Client B")
    status, b_result, _ = env.v1(
        "/v1/search.json?engine=bing&q=client+b+confidential", key=secret_b
    )
    assert status == 200
    b_id = b_result["search_metadata"]["aira"]["id"]
    b_serpapi_id = b_result["search_metadata"]["id"]
    status, a_result, _ = env.v1(
        "/v1/search.json?engine=google&q=client+a+query", key=secret_a
    )
    assert status == 200

    status, listing, _ = env.v1("/v1/searches.json?limit=50", key=secret_a)
    assert status == 200 and listing["total"] == 1, _texts(listing)
    assert "client a query" in _texts(listing)[0]
    for search_id in (b_id, b_serpapi_id):
        status, body, _ = env.v1(f"/v1/searches/{search_id}.json", key=secret_a)
        assert status == 404, body
    # its own search still opens by either id, and the archive link it was given works
    a_id = a_result["search_metadata"]["aira"]["id"]
    assert env.v1(f"/v1/searches/{a_id}.json", key=secret_a)[0] == 200
    assert (
        env.v1(
            a_result["search_metadata"]["json_endpoint"][len(env.base) :], key=secret_a
        )[0]
        == 200
    )

    # a key without terms is the owner's own: it still reaches the whole account
    _, own = _key(connected, name="my laptop")
    status, everything, _ = env.v1("/v1/searches.json?limit=50", key=own)
    joined = " ".join(_texts(everything))
    assert "owner private plan" in joined and "client b confidential" in joined
    assert "Rank:" in joined or everything["total"] >= 4


def test_a_cache_hit_on_someone_elses_search_belongs_to_the_client(env, connected):
    connected.ok(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "shared"}}
    )
    _, secret = _key(connected, client_name="Client A")
    status, body, _ = env.v1("/v1/search.json?engine=google&q=shared", key=secret)
    assert status == 200 and body["search_metadata"]["aira"]["cache_hit"] is True
    status, again, _ = env.v1(
        f"/searches/{body['search_metadata']['id']}.json", key=secret
    )
    assert status == 200
    assert (
        again["search_metadata"]["aira"]["id"] == body["search_metadata"]["aira"]["id"]
    )


def test_a_client_key_account_shows_its_own_allowance_not_yours(env, connected):
    key, secret = _key(
        connected,
        client_name="Client A",
        monthly_quota=100,
        allowed_engines=["google"],
        hourly_limit=7,
    )
    assert env.v1("/v1/search.json?engine=google&q=tea", key=secret)[0] == 200
    status, account, _ = env.v1("/v1/account.json", key=secret)
    assert status == 200
    text = json.dumps(account)
    for private in ("account_email", "plan_renewal_date", "plan_monthly_price"):
        assert private not in account
    assert "fixture@" not in text and "Developer Plan" not in text
    assert account["plan_name"] == "API access for Client A"
    assert account["searches_per_month"] == 100
    assert account["this_month_usage"] == 1 and account["plan_searches_left"] == 99
    assert account["account_rate_limit_per_hour"] == 7
    assert account["allowed_engines"] == ["google"]
    # the root path behaves the same
    assert (
        env.v1("/account.json", key=secret)[1]["plan_name"] == "API access for Client A"
    )
    # your own key still gets your SerpApi account
    _, own = _key(connected, name="mine")
    assert env.v1("/v1/account.json", key=own)[1]["plan_name"] == "Developer Plan"


# -- rank-owned searches stay as the project says ------------------------------------------


def test_rank_owned_monitors_and_saved_searches_cannot_be_changed_elsewhere(connected):
    connected.ok(
        "POST",
        "/api/airaapi/rank",
        {"domain": "example.com", "keywords": ["coffee"], "interval_minutes": 1440},
        expect=201,
    )
    [monitor] = [
        m
        for m in connected.ok("GET", "/api/airaapi/monitors")["monitors"]
        if m["rank_tracker"]
    ]
    status, body, _ = connected.call(
        "PATCH",
        f"/api/airaapi/monitors/{monitor['id']}",
        {"interval_minutes": 60, "enabled": True},
    )
    assert status == 400 and "Rank tracker" in body["error"]
    status, body, _ = connected.call(
        "PATCH",
        f"/api/airaapi/saved/{monitor['saved_id']}",
        {"params": {"q": "something else"}},
    )
    assert status == 400 and "Rank tracker" in body["error"]
    [project] = connected.ok("GET", "/api/airaapi/rank")["projects"]
    assert project["interval_minutes"] == 1440 and project["searches_per_month"] == 30


def test_the_playground_leaves_rank_searches_out_of_saved():
    source = (ROOT / "auto_apply_assistant" / "views-playground.js").read_text(
        encoding="utf-8"
    )
    loader = source[source.index("async function loadSaved(") :]
    loader = loader[: loader.index("function savedMatch(")]
    assert "!item.rank_tracker" in loader
    assert loader.count("rank_tracker") == 2  # saved searches and monitors


# -- keyword limits ------------------------------------------------------------------------


def _db(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "dev.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure_schema(connection)
    store.ensure_extras(connection)
    return connection


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_swapping_keywords_at_the_limit_and_changing_capitals(tmp_path, monkeypatch):
    monkeypatch.setattr(rank, "MAX_KEYWORDS_TOTAL", 4)
    connection = _db(tmp_path)
    first = rank.create_project(
        connection, {"domain": "a.com", "keywords": ["one", "two"]}
    )
    rank.create_project(connection, {"domain": "b.com", "keywords": ["x", "y"]})
    swapped = rank.update_project(
        connection, first["id"], {"keywords": ["three", "four"]}
    )
    assert [k["keyword"] for k in swapped["keywords"]] == ["three", "four"]
    with pytest.raises(ValueError, match="up to 4 keywords"):
        rank.update_project(
            connection, first["id"], {"keywords": ["three", "four", "five"]}
        )
    kept_id = swapped["keywords"][0]["id"]
    respelled = rank.update_project(
        connection, first["id"], {"keywords": ["Three", "four"]}
    )
    assert [k["keyword"] for k in respelled["keywords"]] == ["Three", "four"]
    assert respelled["keywords"][0]["id"] == kept_id  # same keyword, history kept
    saved = store.get_saved(connection, respelled["keywords"][0]["saved_id"])
    assert saved is not None and json.loads(saved["params_json"])["q"] == "Three"
    assert "Three" in saved["name"]


# -- monthly billing -----------------------------------------------------------------------


def _check(connection: sqlite3.Connection, project: dict, when: str) -> None:
    keyword = project["keywords"][0]
    record = {
        "id": store.new_id(),
        "created_at": when,
        "result": {
            "organic_results": [{"position": 1, "link": "https://example.com/"}]
        },
    }
    assert rank.process_monitor_run(
        connection, {"id": keyword["monitor_id"]}, record, None
    )


def test_a_rank_fee_is_billed_only_for_the_months_the_project_existed(tmp_path):
    connection = _db(tmp_path)
    created = _at("2026-08-10T09:00:00")
    project = rank.create_project(
        connection,
        {
            "domain": "example.com",
            "keywords": ["coffee"],
            "client_name": "Acme",
            "monthly_fee": 150,
        },
        now=created,
    )
    _check(connection, project, "2026-08-11T09:00:00Z")
    _check(connection, project, "2026-10-02T09:00:00Z")
    now = _at("2026-10-27T12:00:00")
    before = earnings.month_report(connection, "2025-01", now=now)
    assert before["totals"]["billed"] == 0 and before["rank"] == []
    assert before["totals"]["clients"] == 0
    with pytest.raises(ValueError, match="not started"):
        earnings.month_report(connection, "2027-03", now=now)
    august = earnings.month_report(connection, "2026-08", now=now)
    assert august["totals"]["billed"] == 150 and august["rank"][0]["checks"] == 1
    september = earnings.month_report(connection, "2026-09", now=now)
    assert september["rank"][0]["billed"] == 150 and september["rank"][0]["checks"] == 0
    # the picker reaches back to the first month there was something to bill, with no usage needed
    assert earnings.month_report(connection, now=now)["months"] == [
        "2026-10",
        "2026-09",
        "2026-08",
    ]

    # deleting the project keeps what it already earned and the checks already run
    rank.delete_project(connection, project["id"], now=_at("2026-10-20T00:00:00"))
    assert rank.list_projects(connection) == []
    with pytest.raises(LookupError):
        rank.get_project(connection, project["id"])
    october = earnings.month_report(connection, "2026-10", now=now)
    [line] = october["rank"]
    assert line["billed"] == 150 and line["checks"] == 1 and line["deleted"] is True
    assert (
        earnings.month_report(connection, "2026-08", now=now)["rank"][0]["checks"] == 1
    )
    # ... and bills nothing after the month it was deleted
    later = _at("2026-12-05T00:00:00")
    assert earnings.month_report(connection, "2026-11", now=later)["rank"] == []
    # a deleted project frees its place for a new one
    assert rank.create_project(
        connection, {"domain": "example.com", "keywords": ["coffee"]}
    )


def test_your_own_site_is_a_cost_note_not_a_client(tmp_path):
    connection = _db(tmp_path)
    store.set_pref(connection, earnings.PREF, {"cost_per_search": 0.5})
    own = rank.create_project(
        connection,
        {"domain": "example.com", "keywords": ["coffee"]},
        now=_at("2026-10-01T00:00:00"),
    )
    _check(connection, own, "2026-10-02T00:00:00Z")
    report = earnings.month_report(
        connection, "2026-10", now=_at("2026-10-27T00:00:00")
    )
    assert report["rank"] == [] and report["totals"]["clients"] == 0
    assert report["totals"]["own_checks"] == 1 and report["totals"]["own_cost"] == 0.5
    assert report["totals"]["cost"] == 0


def test_a_client_key_is_listed_only_for_the_months_it_existed(tmp_path):
    connection = _db(tmp_path)
    store.insert_api_key(
        connection,
        {
            "id": "k" * 32,
            "name": "client",
            "prefix": "aira_abcd",
            "key_hash": "h" * 64,
            "created_at": "2026-09-15T00:00:00Z",
            "hourly_limit": 0,
            "client_name": "Acme",
            "price_per_1000": 5,
        },
    )
    store.revoke_api_key(connection, "k" * 32, "2026-10-03T00:00:00Z")
    connection.commit()
    now = _at("2026-12-01T00:00:00")
    assert earnings.month_report(connection, "2026-08", now=now)["keys"] == []
    assert len(earnings.month_report(connection, "2026-09", now=now)["keys"]) == 1
    assert len(earnings.month_report(connection, "2026-10", now=now)["keys"]) == 1
    assert earnings.month_report(connection, "2026-11", now=now)["keys"] == []


def test_the_rank_ledger_is_filled_from_older_checks(tmp_path):
    connection = _db(tmp_path)
    project = rank.create_project(
        connection, {"domain": "example.com", "keywords": ["coffee"]}
    )
    _check(connection, project, "2026-10-02T00:00:00Z")
    connection.execute("DROP TABLE airaapi_rank_usage")
    connection.commit()
    store.ensure_extras(connection)
    assert rank.checks_in_month(connection, project["id"], "2026-10") == 1


# -- household profiles --------------------------------------------------------------------


def test_a_child_profile_cannot_open_keys_prices_rank_or_earnings(env, connected):
    key, _ = _key(connected, client_name="Acme", price_per_1000=5)
    me = connected.ok("GET", "/api/life/me")["profile"]
    kid = connected.ok(
        "POST", "/api/life/profiles", {"name": "Kid", "age_group": "child"}, expect=201
    )["profile"]
    as_kid = {"X-Aira-Profile": kid["id"]}
    for method, path, body in (
        ("GET", "/api/airaapi/earnings", None),
        ("GET", "/api/airaapi/keys", None),
        ("PATCH", f"/api/airaapi/keys/{key['id']}", {"price_per_1000": 0}),
        ("POST", "/api/airaapi/keys", {"name": "kid key"}),
        ("DELETE", f"/api/airaapi/keys/{key['id']}", None),
        ("GET", "/api/airaapi/rank", None),
        ("POST", "/api/airaapi/rank", {"domain": "example.com", "keywords": ["x"]}),
        ("PUT", "/api/airaapi/earnings/settings", {"currency": "EUR"}),
    ):
        status, payload, _ = connected.call(method, path, body, headers=as_kid)
        assert status == 403 and payload.get("code") == "adult_only", (path, payload)
    # nothing changed, and the adult who owns the account still gets in
    status, keys, _ = connected.call(
        "GET", "/api/airaapi/keys", headers={"X-Aira-Profile": me["id"]}
    )
    assert status == 200 and keys["keys"][0]["price_per_1000"] == 5
    assert not keys["keys"][0]["revoked_at"]
    # with several people and no profile named, the browser is asked to pick one
    status, payload, _ = connected.call("GET", "/api/airaapi/earnings")
    assert status == 403 and payload.get("code") == "profile_required"
    # the everyday console (Playground, history) stays open to everyone
    status, _, _ = connected.call("GET", "/api/airaapi/overview", headers=as_kid)
    assert status == 200


# -- the hosted build ----------------------------------------------------------------------


NEW_ROUTES = (
    "rank_list",
    "rank_create",
    "rank_detail",
    "rank_update",
    "rank_delete",
    "rank_run",
    "rank_report",
    "earnings_report",
    "earnings_settings",
    "earnings_statement",
    "keys_update",
)


def test_every_developers_route_has_a_hosted_rule():
    import aira_cloud.routes  # noqa: F401  (registers the Developers rules)
    from aira_cloud.policy import POLICY

    airaapi_http.load_route_modules()
    names = {
        entry.attr
        for entry in airaapi_http.AIRA_ROUTES
        if entry.handler.__module__ == "aira.airaapi.routes"
    }
    missing = sorted(name for name in names if name not in POLICY)
    assert missing == []
    for short in NEW_ROUTES:
        rule = POLICY[f"airaapi__routes__{short}"]
        assert rule.scope == "org_api", short
        assert rule.permission.startswith("integrations."), short
    assert POLICY["airaapi__routes__rank_run"].kind == "api_search"
    assert POLICY["airaapi__routes__rank_list"].permission == "integrations.read"
    assert POLICY["airaapi__routes__keys_update"].write is True


@pytest.fixture
def serpapi(monkeypatch):
    stub = SerpApiStub()
    stub.start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", stub.base_url)
    yield stub
    stub.stop()


def test_hosted_rank_earnings_and_terms_need_the_app_then_work_by_role(cloud, serpapi):
    plain = build_org(cloud, "No Api", plan="team", seats=25, slug_email="noapi.test")
    owner = sign_in(cloud, plain.owner)
    for method, path, body in (
        ("GET", "/api/airaapi/rank", None),
        ("GET", "/api/airaapi/earnings", None),
        ("POST", "/api/airaapi/rank", {"domain": "example.com", "keywords": ["x"]}),
    ):
        status, payload = owner.call(method, path, body)
        assert status == 402 and payload.get("code") == "app_required", (path, payload)

    org = build_org(
        cloud, "Api Org", plan="team", seats=25, slug_email="api.test", apps=["api"]
    )
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY})[0] == 200
    status, project = owner.call(
        "POST",
        "/api/airaapi/rank",
        {"domain": "example.com", "keywords": ["coffee"], "monthly_fee": 100},
    )
    assert status == 201, project
    assert owner.call("GET", f"/api/airaapi/rank/{project['id']}")[0] == 200
    assert (
        owner.call("PATCH", f"/api/airaapi/rank/{project['id']}", {"monthly_fee": 120})[
            0
        ]
        == 200
    )
    status, ran = owner.call("POST", f"/api/airaapi/rank/{project['id']}/run", {})
    assert status == 200 and ran["credits"] == 1, ran
    assert (
        owner.call(
            "GET", f"/api/airaapi/rank/{project['id']}/report?format=csv", raw=True
        )[0]
        == 200
    )
    assert (
        owner.call("PUT", "/api/airaapi/earnings/settings", {"currency": "EUR"})[0]
        == 200
    )
    status, created = owner.call(
        "POST",
        "/api/airaapi/keys",
        {"name": "client", "client_name": "C", "price_per_1000": 5},
    )
    assert status == 201, created
    key_id = created["key"]["id"]
    status, updated = owner.call(
        "PATCH", f"/api/airaapi/keys/{key_id}", {"monthly_quota": 5}
    )
    assert status == 200 and updated["key"]["monthly_quota"] == 5
    status, report = owner.call("GET", "/api/airaapi/earnings")
    assert status == 200 and report["rank"][0]["billed"] == 120
    assert (
        owner.call(
            "GET", f"/api/airaapi/earnings/statement/{key_id}?format=csv", raw=True
        )[0]
        == 200
    )

    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/airaapi/rank")[0] == 200
    assert auditor.call("GET", "/api/airaapi/earnings")[0] == 200
    assert (
        auditor.call("PATCH", f"/api/airaapi/keys/{key_id}", {"monthly_quota": 0})[0]
        == 403
    )
    for role in ("coach", "candidate", "billing"):
        member = sign_in(cloud, getattr(org, role))
        assert member.call("GET", "/api/airaapi/earnings")[0] == 403, role
        assert (
            member.call(
                "POST", "/api/airaapi/rank", {"domain": "x.com", "keywords": ["x"]}
            )[0]
            == 403
        )
    assert owner.call("DELETE", f"/api/airaapi/rank/{project['id']}")[0] == 200

    other = sign_in(
        cloud,
        build_org(
            cloud, "Other", plan="team", seats=25, slug_email="other.test", apps=["api"]
        ).owner,
    )
    assert other.call("GET", "/api/airaapi/rank")[1]["projects"] == []
    assert other.call("GET", "/api/airaapi/earnings")[1]["keys"] == []


# -- dialogs -------------------------------------------------------------------------------


def test_enter_saves_the_terms_and_rank_project_dialogs_and_cost_asks_survive_a_double_click():
    console = (ROOT / "auto_apply_assistant" / "views-airaapi.js").read_text(
        encoding="utf-8"
    )
    for name in ("function keyTermsDialog(", "function rankProjectDialog("):
        body = console[console.index(name) :]
        body = body[: body.index("\n  }\n")]
        assert "submitOnEnter(dialog," in body, name
    assert (
        'confirmCost(count, `Checking ${pluralize(count, "keyword")} for ${project.domain}`, { live: true })'
        in console
    )
    kit = (ROOT / "auto_apply_assistant" / "views-serp-kit.js").read_text(
        encoding="utf-8"
    )
    ask = kit[kit.index("function confirmCost(") :]
    ask = ask[: ask.index("\n  }\n")]
    assert "stopImmediatePropagation" in ask and "Each check runs live" in ask
