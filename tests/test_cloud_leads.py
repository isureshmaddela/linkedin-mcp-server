"""Aira Leads in the hosted shell: plan and role gating, source gates, metering, limits, forwarding and data handling."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from cloud_support import (
    build_org,
    local_module,
    seed_workspace,
    sign_in,
    signup,
)
from serpapi_stub import DEFAULT_KEY, SerpApiStub

from aira.leads import service as leads_service
from aira_cloud import plans
from aira_cloud.policy import POLICY

SALES_NAV_URL = (
    "https://www.linkedin.com/sales/search/people?query=(recentSearchParam:(id:7))"
)
MAPS = {"source": "google_maps", "query": "coffee roasters", "location": "New York"}
ALL_GOOGLE = ["aira_jobs", "company_boards", "google_jobs"]


@pytest.fixture
def serpapi(monkeypatch):
    stub = SerpApiStub()
    stub.start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", stub.base_url)
    yield stub
    stub.stop()


class FakeBridge:
    """The candidate's LinkedIn tools: records the thread that read Sales Navigator (web tier or an agent)."""

    def __init__(self) -> None:
        self.main = threading.current_thread()
        self.calls: list[dict[str, Any]] = []

    def linkedin_state(self) -> str:
        return "connected"

    def linkedin_call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(
            {
                "tool": name,
                "where": "web" if threading.current_thread() is self.main else "agent",
                "arguments": arguments,
            }
        )
        return {
            "pages_read": 1,
            "warnings": [],
            "leads": [
                {
                    "name": "Maria Lopez",
                    "title": "Director of Sales",
                    "company": "Northwind Cloud",
                    "location": "Austin, Texas",
                    "profile_url": "https://www.linkedin.com/sales/lead/ACwAAA111,NAME_SEARCH",
                    "degree": "2nd",
                }
            ],
        }

    def sender_name(self) -> str:
        return "Hosted Rep"

    def model(self, prompt: str) -> str | None:
        return None


@pytest.fixture
def bridge():
    previous = leads_service.gateway()
    fake = FakeBridge()
    leads_service.set_gateway(fake)
    yield fake
    leads_service.set_gateway(previous)


def trial_owner(cloud, domain):
    """The owner of a trial organization (a trial has every app and two staff seats, so no full cast)."""
    return signup(cloud, "Trial Org", f"owner@{domain}")["owner"]


def rep(cloud, org):
    """The person who works the business's leads: a coach, a team member with no admin rights."""
    return sign_in(cloud, org.coach)


def configure(cloud, org, *, sources=None, linkedin=False, key=True):
    policy: dict[str, Any] = {"allowed_sources": sources or ALL_GOOGLE}
    if linkedin:
        policy["linkedin_allowed"] = True
        policy["allowed_sources"] = [*policy["allowed_sources"], "linkedin"]
    cloud.tenancy.update_policy(org.owner, policy)
    if key:
        owner = sign_in(cloud, org.owner)
        assert (
            owner.call("POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY})[0] == 200
        )


def override(cloud, org, values):
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overrides = ? WHERE org_id = ?",
            (json.dumps(values), org.org_id),
        )
    cloud.entitlements.invalidate(org.org_id)


def used(cloud, org, metric):
    return cloud.usage.used(org.org_id, metric)


@pytest.fixture
def team(cloud, serpapi):
    org = build_org(
        cloud, "Leads Team", plan="team", seats=25, slug_email="leads.test", apps="all"
    )
    configure(cloud, org, linkedin=True)
    return org


# ---------------------------------------------------------------------------
# policy, plans and roles
# ---------------------------------------------------------------------------


def test_every_leads_route_has_a_policy_entry():
    local = local_module()
    names = {name for _m, _p, name, _a in local.ROUTES if "leads_routes" in name}
    assert len(names) >= 41
    assert names <= set(POLICY), sorted(names - set(POLICY))
    for name in names:
        rule = POLICY[name]
        assert rule.scope == "org_api", (
            name,
            "Leads is part of Marketing: the business's shared pipeline",
        )
        assert rule.feature in {"leads", "leads_outreach"}, name
        assert rule.permission in {"suite.read", "suite.manage"}, name
        assert rule.write == (rule.permission == "suite.manage"), name


def test_leads_comes_with_marketing_which_the_trial_includes_and_paid_plans_turn_on(
    cloud,
):
    owner = sign_in(cloud, trial_owner(cloud, "trial.test"))
    assert owner.call("GET", "/api/leads/overview")[0] == 200, (
        "the trial includes every app"
    )
    for plan_id in ("starter", "team"):
        assert "leads" not in plans.DEFAULT_PLANS[plan_id]["entitlements"]["features"]
        assert plans.app_state(plan_id, "marketing") == "addon"
        assert plans.app_state(plan_id, "leads") == "addon", "the old id is Marketing"
    assert "leads" in plans.DEFAULT_PLANS["enterprise"]["entitlements"]["features"]
    starter = build_org(
        cloud, "Starter Org", plan="starter", seats=5, slug_email="starter.test"
    )
    status, body = rep(cloud, starter).call("GET", "/api/leads/overview")
    assert status == 402 and body["code"] == "app_required"
    assert body["app"] == "marketing" and body["name"] == "Marketing"
    cloud.billing.subscribe_app(starter.owner, "marketing")
    assert rep(cloud, starter).call("GET", "/api/leads/overview")[0] == 200
    assert rep(cloud, starter).call("GET", "/api/marketing/overview")[0] != 402, (
        "one subscription opens both sections"
    )


def test_a_plan_version_published_before_leads_gets_the_feature_and_limits():
    old = {
        "candidate_seats": 25,
        "features": [
            "sources.aira_jobs",
            "auth.mfa",
            "coach.act",
            "sources.google_jobs",
        ],
    }
    merged = plans.with_later_limits("starter", old)
    assert (
        "leads" not in merged["features"]  # an add-on, turned on under Apps
        and merged["leads_stored"] == 2000
        and merged["lead_enrichments_per_month"] == 200
    )
    assert "leads" not in plans.with_later_limits("trial", {"features": []})["features"]


def test_the_business_team_shares_one_pipeline_and_candidates_have_none(cloud, serpapi):
    org = build_org(
        cloud, "Role Org", plan="team", seats=25, slug_email="roles.test", apps="all"
    )
    configure(cloud, org)
    coach = rep(cloud, org)
    created = coach.call(
        "POST",
        "/api/leads/items/import-csv",
        {"csv": "Name,Company\nShared Person,Shared Co\n"},
    )
    assert created[0] == 201, created
    for who in (org.owner, org.admin, org.auditor):
        status, body = sign_in(cloud, who).call("GET", "/api/leads/items")
        assert status == 200 and body["total"] == 1, (who.role, status, body)
        assert body["leads"][0]["name"] == "Shared Person"
    admin = sign_in(cloud, org.admin)
    assert (
        admin.call(
            "POST", "/api/leads/items", {"leads": [{"name": "Two", "company": "B"}]}
        )[0]
        == 201
    )
    assert coach.call("GET", "/api/leads/items")[1]["total"] == 2, (
        "what one person adds, the whole team sees"
    )
    auditor = sign_in(cloud, org.auditor)
    assert (
        auditor.call("POST", "/api/leads/items/import-csv", {"csv": "Name\nx\n"})[0]
        == 403
    ), "an auditor reads, never writes"
    for who in (org.candidate, org.other, org.billing):
        browser = sign_in(cloud, who)
        status, body = browser.call("GET", "/api/leads/items")
        assert status == 403, (who.role, status, body)
        assert (
            browser.call("POST", "/api/leads/items/import-csv", {"csv": "Name\nx\n"})[0]
            == 403
        )
    assert (
        sign_in(cloud, org.candidate).call(
            "GET", "/api/leads/items", as_candidate=org.candidate.member_id
        )[0]
        == 403
    ), "naming a workspace does not open Leads to a candidate"


def test_another_organizations_lead_ids_are_not_found(cloud, serpapi):
    a = build_org(
        cloud, "Org A", plan="team", seats=25, slug_email="a.test", apps="all"
    )
    b = build_org(
        cloud, "Org B", plan="team", seats=25, slug_email="b.test", apps="all"
    )
    for org in (a, b):
        configure(cloud, org, key=False)
    ra, rb = rep(cloud, a), rep(cloud, b)
    saved = ra.call(
        "POST", "/api/leads/items", {"leads": [{"name": "Only A", "company": "Acme"}]}
    )[1]
    lead_id = saved["leads"][0]["id"]
    for method, path, body in (
        ("GET", f"/api/leads/items/{lead_id}", None),
        ("PATCH", f"/api/leads/items/{lead_id}", {"notes": "x"}),
        ("DELETE", f"/api/leads/items/{lead_id}", None),
        ("POST", f"/api/leads/items/{lead_id}/enrich", {}),
        ("POST", f"/api/leads/items/{lead_id}/draft", {}),
    ):
        status, _ = rb.call(method, path, body)
        assert status == 404, (method, path, status)
    assert rb.call("GET", "/api/leads/items")[1]["total"] == 0
    assert ra.call("GET", "/api/leads/items")[1]["total"] == 1


# ---------------------------------------------------------------------------
# Google sources: gates and metering
# ---------------------------------------------------------------------------


def test_google_lead_searches_follow_the_organizations_source_policy_and_kill_switch(
    cloud, team
):
    candidate = rep(cloud, team)
    cloud.tenancy.update_policy(
        team.owner, {"allowed_sources": ["aira_jobs", "company_boards"]}
    )
    status, body = candidate.call("POST", "/api/leads/search", MAPS)
    assert (
        status == 403 and body["code"] == "source_blocked" and "policy" in body["error"]
    )
    cloud.tenancy.update_policy(
        team.owner, {"allowed_sources": [*ALL_GOOGLE, "linkedin"]}
    )
    status, body = candidate.call("POST", "/api/leads/search", MAPS)
    assert status == 200 and body["count"] == 4
    with cloud.db.write() as connection:
        connection.execute(
            "INSERT INTO blocked_sources (source, reason, blocked_at) VALUES ('google_jobs', 'upstream incident', ?)",
            (cloud.clock.now(),),
        )
    status, body = candidate.call("POST", "/api/leads/search", MAPS)
    assert status == 403 and "switched off" in body["error"]


def test_google_lead_searches_are_metered_and_a_plan_without_the_source_is_refused(
    cloud, team, serpapi
):
    candidate = rep(cloud, team)
    assert used(cloud, team, "searches") == 0
    plan = candidate.call("POST", "/api/leads/search", {**MAPS, "dry_run": True})
    assert (
        plan[0] == 200
        and plan[1]["dry_run"]
        and used(cloud, team, "searches") == 0
        and serpapi.search_calls == 0
    )
    for source, extra in (
        ("google_maps", {"query": "coffee", "location": "New York"}),
        ("google_web", {"mode": "people", "titles": "VP Sales"}),
        ("google_jobs", {"query": "barista"}),
    ):
        status, body = candidate.call(
            "POST", "/api/leads/search", {"source": source, **extra}
        )
        assert status == 200, (source, body)
    assert used(cloud, team, "searches") == 3
    cached = candidate.call(
        "POST",
        "/api/leads/search",
        {"source": "google_maps", "query": "coffee", "location": "New York"},
    )[1]
    assert (
        cached["cache_hit"]
        and cached["credits"] == 0
        and used(cloud, team, "searches") == 4
    ), "every search counts, credits aside"
    assert (
        candidate.call("POST", "/api/leads/search", {"source": "google_maps"})[0] == 400
    )
    assert used(cloud, team, "searches") == 4, "a refused request is not counted"
    starter = build_org(
        cloud,
        "Starter Org",
        plan="starter",
        seats=5,
        slug_email="st.test",
        apps=["marketing"],
    )
    cloud.tenancy.update_policy(starter.owner, {"allowed_sources": ALL_GOOGLE})
    sign_in(cloud, starter.owner).call(
        "POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY}
    )
    sc = rep(cloud, starter)
    assert sc.call("POST", "/api/leads/search", MAPS)[0] == 200
    starter_used = used(cloud, starter, "searches")
    cloud.tenancy.update_policy(starter.owner, {"allowed_sources": ["aira_jobs"]})
    status, body = sc.call("POST", "/api/leads/search", MAPS)
    assert status == 403 and body["code"] == "source_blocked"
    assert used(cloud, starter, "searches") == starter_used


def test_company_news_signals_are_gated_and_metered_like_other_google_searches(
    cloud, team, serpapi
):
    candidate = rep(cloud, team)
    lead = candidate.call(
        "POST",
        "/api/leads/items",
        {
            "leads": [
                {
                    "name": "Northwind Cloud",
                    "company": "Northwind Cloud",
                    "kind": "company",
                }
            ]
        },
    )[1]["leads"][0]
    path = f"/api/leads/items/{lead['id']}/signals"
    assert (
        candidate.call("POST", path, {"dry_run": True})[0] == 200
        and used(cloud, team, "searches") == 0
    )
    status, body = candidate.call("POST", path, {})
    assert (
        status == 200
        and used(cloud, team, "searches") == 1
        and serpapi.count("/search.json", "google_news") == 1
    )
    assert (
        candidate.call("POST", path, {})[1]["credits"] == 0
        and used(cloud, team, "searches") == 2
    )
    cloud.tenancy.update_policy(
        team.owner, {"allowed_sources": ["aira_jobs", "company_boards"]}
    )
    status, body = candidate.call("POST", path, {})
    assert (
        status == 403
        and body["code"] == "source_blocked"
        and used(cloud, team, "searches") == 2
    )
    assert (
        sign_in(cloud, trial_owner(cloud, "tr.test")).call(
            "POST", f"/api/leads/items/{'0' * 32}/signals", {}
        )[0]
        == 402
    ), "the trial has Marketing but not Google sources"


def test_a_lead_search_with_the_pooled_key_spends_the_pooled_allowance(cloud, serpapi):
    cloud.config.pooled_serpapi_key = DEFAULT_KEY
    org = build_org(
        cloud, "Pool Org", plan="team", seats=25, slug_email="pool.test", apps="all"
    )
    cloud.tenancy.update_policy(
        org.owner, {"allowed_sources": ALL_GOOGLE, "use_pooled_serpapi": True}
    )
    candidate = rep(cloud, org)
    assert candidate.call("POST", "/api/leads/search", MAPS)[0] == 200
    assert (used(cloud, org, "pooled_searches"), used(cloud, org, "searches")) == (1, 1)
    assert candidate.call("POST", "/api/leads/search", MAPS)[1]["cache_hit"]
    assert used(cloud, org, "pooled_searches") == 1, "a cache hit is free"
    override(cloud, org, {"pooled_searches_per_month": 1})
    status, body = candidate.call(
        "POST", "/api/leads/search", {**MAPS, "query": "bakeries"}
    )
    assert status == 402 and body["limit"] == "pooled_searches_per_month"


# ---------------------------------------------------------------------------
# Sales Navigator: the LinkedIn gates and the worker host
# ---------------------------------------------------------------------------


def nav_search(browser, **extra):
    return browser.call(
        "POST",
        "/api/leads/search",
        {"source": "sales_navigator", "url": SALES_NAV_URL, **extra},
    )


def test_the_shared_pipeline_never_reads_anyones_sales_navigator(cloud, team, bridge):
    """Sales Navigator reads one person's own LinkedIn session; the business's Leads is shared, so it imports a
    Sales Navigator list as a CSV file instead and no LinkedIn session is ever used."""
    coach = rep(cloud, team)
    for extra in ({}, {"dry_run": True}, {"auto_add": True}):
        status, body = nav_search(coach, **extra)
        assert status == 403 and body["code"] == "source_unavailable", body
        assert "CSV" in body["error"]
    saved = coach.call(
        "POST",
        "/api/leads/searches",
        {
            "name": "Directors",
            "source": "sales_navigator",
            "params": {"url": SALES_NAV_URL},
        },
    )
    if saved[0] in (200, 201):
        search_id = saved[1]["search"]["id"]
        status, body = coach.call("POST", f"/api/leads/searches/{search_id}/run", {})
        assert status == 403 and body["code"] == "source_unavailable"
    assert bridge.calls == [] and used(cloud, team, "searches") == 0
    imported = coach.call(
        "POST",
        "/api/leads/items/import-csv",
        {"csv": "Name,Title,Company\nMaria Lopez,Director of Sales,Northwind Cloud\n"},
    )
    assert imported[0] == 201, "the way in for a Sales Navigator list"
    # a Google search still works for the same person
    assert coach.call("POST", "/api/leads/search", MAPS)[0] == 200


def test_a_saved_google_search_is_gated_by_its_source_and_metered(cloud, team):
    candidate = rep(cloud, team)
    saved = candidate.call(
        "POST",
        "/api/leads/searches",
        {
            "name": "NYC",
            "source": "google_maps",
            "params": {"query": "coffee roasters", "location": "New York"},
        },
    )[1]["search"]
    status, body = candidate.call("POST", f"/api/leads/searches/{saved['id']}/run", {})
    assert status == 200 and body["added"] == 4 and used(cloud, team, "searches") == 1
    cloud.tenancy.update_policy(
        team.owner, {"allowed_sources": ["aira_jobs", "company_boards"]}
    )
    assert (
        candidate.call("POST", f"/api/leads/searches/{saved['id']}/run", {})[0] == 403
    )
    assert used(cloud, team, "searches") == 1


# ---------------------------------------------------------------------------
# limits: stored leads and enrichments
# ---------------------------------------------------------------------------


def test_the_plan_limits_how_many_leads_are_stored(cloud, team):
    override(cloud, team, {"leads_stored": 3})
    candidate = rep(cloud, team)
    ok = candidate.call(
        "POST",
        "/api/leads/items",
        {"leads": [{"name": f"P{n}", "company": f"C{n}"} for n in range(3)]},
    )
    assert ok[0] == 201 and ok[1]["created"] == 3
    status, body = candidate.call(
        "POST", "/api/leads/items", {"leads": [{"name": "One more", "company": "X"}]}
    )
    assert (
        status == 402 and body["limit"] == "leads_stored" and body["limit_value"] == 3
    )
    status, body = candidate.call(
        "POST", "/api/leads/items/import-csv", {"csv": "Name,Company\nA,B\n"}
    )
    assert status == 402 and body["code"] == "plan_required"
    # deleting makes room again
    first = candidate.call("GET", "/api/leads/items")[1]["leads"][0]
    assert candidate.call("DELETE", f"/api/leads/items/{first['id']}")[0] == 200
    assert (
        candidate.call(
            "POST",
            "/api/leads/items",
            {"leads": [{"name": "Fits now", "company": "Y"}]},
        )[0]
        == 201
    )
    usage = cloud.dashboard.organization(team.owner)["plan_usage"]["leads"]
    assert usage["stored"] == 3 and usage["limit"] == 3


def test_a_search_that_would_overflow_the_plan_shows_results_without_saving_them(
    cloud, team
):
    override(cloud, team, {"leads_stored": 2})
    candidate = rep(cloud, team)
    status, body = candidate.call(
        "POST", "/api/leads/search", {**MAPS, "auto_add": True}
    )
    assert status == 200 and body["count"] == 4 and body["added"] == 0
    assert "fit in this plan" in candidate.header("X-Aira-Leads-Auto-Add")
    assert candidate.call("GET", "/api/leads/items")[1]["total"] == 0
    saved = candidate.call("POST", "/api/leads/items", {"leads": body["leads"][:2]})
    assert saved[0] == 201 and saved[1]["created"] == 2
    assert (
        candidate.call("POST", "/api/leads/items", {"leads": body["leads"][2:3]})[0]
        == 402
    )


class _Site(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):  # noqa: N802
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        body = b'<html><title>Acme</title><body><a href="mailto:hello@acme.test">mail</a></body></html>'
        status = 404 if self.path == "/robots.txt" else 200
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.hits = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    yield server
    server.shutdown()
    server.server_close()


def test_enrichment_is_metered_limited_and_only_reads_public_websites(
    cloud, team, site, monkeypatch
):
    override(cloud, team, {"lead_enrichments_per_month": 2})
    candidate = rep(cloud, team)
    lead = candidate.call(
        "POST",
        "/api/leads/items",
        {
            "leads": [
                {
                    "name": "Acme",
                    "company": "Acme",
                    "kind": "company",
                    "website": site.url,
                }
            ]
        },
    )[1]["leads"][0]
    bare = candidate.call(
        "POST",
        "/api/leads/items",
        {"leads": [{"name": "No Site", "company": "No Site", "kind": "company"}]},
    )[1]["leads"][0]
    path = lambda item: f"/api/leads/items/{item['id']}/enrich"  # noqa: E731
    # the hosted build never reads loopback or private addresses, whatever the host's environment says
    monkeypatch.setenv("AIRA_LEADS_ALLOW_LOCAL", "")
    status, body = candidate.call("POST", path(lead), {})
    assert status == 200 and not body["lead"]["enrichment"]["pages"] and site.hits == []
    assert any("public" in e for e in body["lead"]["enrichment"]["errors"])
    assert used(cloud, team, "lead_enrichments") == 0, (
        "nothing was read, so nothing is charged"
    )
    assert (
        candidate.call("POST", path(bare), {})[0] == 400
        and used(cloud, team, "lead_enrichments") == 0
    )
    # a test stand-in site is only reachable with the explicit local switch (set after the app started)
    monkeypatch.setenv("AIRA_LEADS_ALLOW_LOCAL", "1")
    assert (
        candidate.call("POST", path(lead), {})[0] == 200
        and used(cloud, team, "lead_enrichments") == 1
    )
    assert (
        candidate.call("POST", path(lead), {})[0] == 200
        and used(cloud, team, "lead_enrichments") == 2
    )
    status, body = candidate.call("POST", path(lead), {})
    assert (
        status == 402
        and body["limit"] == "lead_enrichments_per_month"
        and body["limit_value"] == 2
    )
    assert site.hits.count("/") == 2
    assert (
        cloud.dashboard.organization(team.owner)["plan_usage"]["leads"]["enrichments"]
        == 2
    )


def test_the_hosted_app_ignores_the_local_enrichment_switch_at_start(
    tmp_path, monkeypatch
):
    from cloud_support import make_app

    monkeypatch.setenv("AIRA_LEADS_ALLOW_LOCAL", "1")
    app = make_app(tmp_path)
    import os

    assert os.environ.get("AIRA_LEADS_ALLOW_LOCAL") is None
    app.close()


# ---------------------------------------------------------------------------
# data handling: export and deletion
# ---------------------------------------------------------------------------


def test_the_business_exports_its_leads_and_a_candidates_departure_leaves_them(
    cloud, team
):
    coach = rep(cloud, team)
    seed_workspace(cloud, team.org_id, team.candidate.member_id)
    coach.call(
        "POST",
        "/api/leads/items/import-csv",
        {"csv": "Name,Title,Company,Email\nJane Doe,VP Sales,Acme,jane@acme.test\n"},
    )
    status, body = sign_in(cloud, team.owner).call(
        "GET", "/api/leads/export?format=csv", raw=True
    )
    assert status == 200 and "Jane Doe" in (
        body.decode() if isinstance(body, bytes) else str(body)
    ), "the business takes its own pipeline with it"
    export = cloud.lifecycle.export_candidate(team.candidate, team.candidate.member_id)
    _row, blob = cloud.lifecycle.export_for(team.candidate, export["export_id"])
    assert b"Jane Doe" not in zipfile.ZipFile(io.BytesIO(blob)).read(
        "workspace.json"
    ), "the business's leads are not a candidate's data"
    assert cloud.workspaces.leads_total(team.org_id) == 1
    usage = cloud.dashboard.organization(team.owner)["plan_usage"]["leads"]
    assert usage["stored"] == 1
    directory = cloud.workspaces.candidate_dir(team.org_id, team.candidate.member_id)
    cloud.lifecycle.delete_candidate_workspace(team.owner, team.candidate.member_id)
    assert not directory.exists()
    assert cloud.workspaces.leads_total(team.org_id) == 1, (
        "a candidate leaving does not take the business's leads"
    )
    assert coach.call("GET", "/api/leads/items")[1]["total"] == 1
