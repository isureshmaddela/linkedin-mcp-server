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
    add_member,
    build_org,
    local_module,
    seed_workspace,
    sign_in,
    signup,
)
from serpapi_stub import DEFAULT_KEY, SerpApiStub

from aira.leads import service as leads_service
from aira_cloud import plans
from aira_cloud.agent import AgentServer
from aira_cloud.gateway import Gateway
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


def trial_candidate(cloud, domain):
    """A candidate in a trial organization (a trial has two staff seats, so no full cast)."""
    owner = signup(cloud, "Trial Org", f"owner@{domain}")["owner"]
    return add_member(cloud, owner, f"cand@{domain}", "candidate")


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


def opt_in(cloud, org):
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE workspaces SET linkedin_opt_in_at = ? WHERE candidate_id = ?",
            (cloud.clock.now(), org.candidate.member_id),
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
        assert rule.scope == "candidate", name
        assert rule.feature in {"leads", "leads_outreach"}, name
        assert rule.permission in {"leads.read", "leads.write", "leads.outreach"}, name
        if rule.permission != "leads.outreach":
            assert rule.write == (rule.permission == "leads.write"), name


def test_leads_is_an_app_the_trial_includes_and_paid_plans_turn_on(cloud):
    candidate = sign_in(cloud, trial_candidate(cloud, "trial.test"))
    assert candidate.call("GET", "/api/leads/overview")[0] == 200, (
        "the trial includes every app"
    )
    for plan_id in ("starter", "team"):
        assert "leads" not in plans.DEFAULT_PLANS[plan_id]["entitlements"]["features"]
        assert plans.app_state(plan_id, "leads") == "addon"
    assert "leads" in plans.DEFAULT_PLANS["enterprise"]["entitlements"]["features"]
    starter = build_org(
        cloud, "Starter Org", plan="starter", seats=5, slug_email="starter.test"
    )
    status, body = sign_in(cloud, starter.candidate).call("GET", "/api/leads/overview")
    assert status == 402 and body["code"] == "app_required" and body["app"] == "leads"
    cloud.billing.subscribe_app(starter.owner, "leads")
    assert (
        sign_in(cloud, starter.candidate).call("GET", "/api/leads/overview")[0] == 200
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


def test_only_the_candidate_and_a_coach_with_act_scope_use_leads(cloud, serpapi):
    org = build_org(
        cloud, "Role Org", plan="team", seats=25, slug_email="roles.test", apps="all"
    )
    configure(cloud, org)
    candidate = sign_in(cloud, org.candidate)
    created = candidate.call(
        "POST",
        "/api/leads/items/import-csv",
        {"csv": "Name,Company\nSecret Person,Hidden Co\n"},
    )
    assert created[0] == 201
    cid = org.candidate.member_id
    for who in (org.owner, org.admin, org.auditor, org.billing):
        browser = sign_in(cloud, who)
        status, body = browser.call("GET", "/api/leads/items", as_candidate=cid)
        assert status == 403, (who.role, status, body)
        assert (
            browser.call(
                "POST",
                "/api/leads/items/import-csv",
                {"csv": "Name\nx\n"},
                as_candidate=cid,
            )[0]
            == 403
        )
    coach = sign_in(cloud, org.coach)
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, cid, "view")
    assert coach.call("GET", "/api/leads/items", as_candidate=cid)[0] == 403, (
        "a view scope sees progress, not contacts"
    )
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, cid, "act")
    status, body = coach.call("GET", "/api/leads/items", as_candidate=cid)
    assert status == 200 and body["total"] == 1
    assert (
        coach.call("GET", "/api/leads/items", as_candidate=org.other.member_id)[0]
        == 403
    )
    assert (
        candidate.call("GET", "/api/leads/items", as_candidate=org.other.member_id)[0]
        == 403
    )
    assert not any(org.owner.member_id in json.dumps(x) for x in [body])


def test_another_organizations_lead_ids_are_not_found(cloud, serpapi):
    a = build_org(
        cloud, "Org A", plan="team", seats=25, slug_email="a.test", apps="all"
    )
    b = build_org(
        cloud, "Org B", plan="team", seats=25, slug_email="b.test", apps="all"
    )
    for org in (a, b):
        configure(cloud, org, key=False)
    ca, cb = sign_in(cloud, a.candidate), sign_in(cloud, b.candidate)
    saved = ca.call(
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
        status, _ = cb.call(method, path, body)
        assert status == 404, (method, path, status)
    # the same id sent as another organization's candidate is refused before any data is read
    assert cb.call(
        "GET", f"/api/leads/items/{lead_id}", as_candidate=a.candidate.member_id
    )[0] in (403, 404)
    assert cb.call("GET", "/api/leads/items")[1]["total"] == 0


# ---------------------------------------------------------------------------
# Google sources: gates and metering
# ---------------------------------------------------------------------------


def test_google_lead_searches_follow_the_organizations_source_policy_and_kill_switch(
    cloud, team
):
    candidate = sign_in(cloud, team.candidate)
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
    candidate = sign_in(cloud, team.candidate)
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
        apps=["leads"],
    )
    cloud.tenancy.update_policy(starter.owner, {"allowed_sources": ALL_GOOGLE})
    sign_in(cloud, starter.owner).call(
        "POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY}
    )
    sc = sign_in(cloud, starter.candidate)
    assert sc.call("POST", "/api/leads/search", MAPS)[0] == 200
    status, body = sc.call(
        "POST", "/api/leads/search", {"source": "sales_navigator", "url": SALES_NAV_URL}
    )
    assert (
        status == 402
        and body["required_plan"] == "team"
        and "Linkedin" in body["error"].title()
    )


def test_company_news_signals_are_gated_and_metered_like_other_google_searches(
    cloud, team, serpapi
):
    candidate = sign_in(cloud, team.candidate)
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
        sign_in(cloud, trial_candidate(cloud, "tr.test")).call(
            "POST", f"/api/leads/items/{'0' * 32}/signals", {}
        )[0]
        == 402
    )


def test_a_lead_search_with_the_pooled_key_spends_the_pooled_allowance(cloud, serpapi):
    cloud.config.pooled_serpapi_key = DEFAULT_KEY
    org = build_org(
        cloud, "Pool Org", plan="team", seats=25, slug_email="pool.test", apps="all"
    )
    cloud.tenancy.update_policy(
        org.owner, {"allowed_sources": ALL_GOOGLE, "use_pooled_serpapi": True}
    )
    candidate = sign_in(cloud, org.candidate)
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


def test_sales_navigator_needs_every_linkedin_gate(cloud, team, bridge):
    candidate = sign_in(cloud, team.candidate)
    status, body = nav_search(candidate)
    assert status == 403 and "turned LinkedIn on" in body["error"], (
        "the candidate must have opted in themselves"
    )
    assert bridge.calls == []
    opt_in(cloud, team)
    cloud.tenancy.update_policy(team.owner, {"linkedin_allowed": False})
    status, body = nav_search(candidate)
    assert status == 403 and "policy" in body["error"] and bridge.calls == []
    cloud.tenancy.update_policy(
        team.owner,
        {"linkedin_allowed": True, "allowed_sources": [*ALL_GOOGLE, "linkedin"]},
    )
    status, body = nav_search(candidate)
    assert (
        status == 200
        and body["count"] == 1
        and body["leads"][0]["name"] == "Maria Lopez"
    )
    assert (
        bridge.calls[0]["tool"] == "search_sales_navigator"
        and used(cloud, team, "searches") == 1
    )
    assert (
        nav_search(candidate, dry_run=True)[1]["dry_run"]
        and len(bridge.calls) == 1
        and used(cloud, team, "searches") == 1
    )
    with cloud.db.write() as connection:
        connection.execute(
            "INSERT INTO blocked_sources (source, reason, blocked_at) VALUES ('linkedin', 'abuse review', ?)",
            (cloud.clock.now(),),
        )
    status, body = nav_search(candidate)
    assert status == 403 and "switched off" in body["error"] and len(bridge.calls) == 1
    assert used(cloud, team, "searches") == 1


def test_a_sales_navigator_search_runs_on_the_worker_that_holds_the_session(
    cloud, team, bridge
):
    opt_in(cloud, team)
    agent = AgentServer(cloud, Gateway(cloud), port=0).start()
    cloud.queue.register_worker("w-1", "apply", "host", 4, 0, agent.url)
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE workspaces SET linkedin_host = ? WHERE candidate_id = ?",
            (agent.url, team.candidate.member_id),
        )
    candidate = sign_in(cloud, team.candidate)
    status, body = nav_search(candidate, auto_add=True)
    assert status == 200 and body["added"] == 1
    assert [c["where"] for c in bridge.calls] == ["agent"], (
        "read in the browser on the host that holds the session"
    )
    # a saved Sales Navigator search is forwarded the same way, and is gated by the same rules
    saved = candidate.call(
        "POST",
        "/api/leads/searches",
        {
            "name": "Directors",
            "source": "sales_navigator",
            "params": {"url": SALES_NAV_URL},
        },
    )[1]["search"]
    status, body = candidate.call("POST", f"/api/leads/searches/{saved['id']}/run", {})
    assert status == 200 and [c["where"] for c in bridge.calls] == ["agent", "agent"]
    cloud.tenancy.update_policy(team.owner, {"linkedin_allowed": False})
    status, body = candidate.call("POST", f"/api/leads/searches/{saved['id']}/run", {})
    assert status == 403 and len(bridge.calls) == 2
    # a Google search never goes to the agent
    cloud.tenancy.update_policy(team.owner, {"linkedin_allowed": True})
    assert (
        candidate.call("POST", "/api/leads/search", MAPS)[0] == 200
        and len(bridge.calls) == 2
    )
    agent.stop()


def test_a_saved_google_search_is_gated_by_its_source_and_metered(cloud, team):
    candidate = sign_in(cloud, team.candidate)
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
    candidate = sign_in(cloud, team.candidate)
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
    candidate = sign_in(cloud, team.candidate)
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
    candidate = sign_in(cloud, team.candidate)
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


def test_leads_are_in_exports_and_are_removed_with_the_workspace(cloud, team):
    candidate = sign_in(cloud, team.candidate)
    seed_workspace(cloud, team.org_id, team.candidate.member_id)
    candidate.call(
        "POST",
        "/api/leads/items/import-csv",
        {"csv": "Name,Title,Company,Email\nJane Doe,VP Sales,Acme,jane@acme.test\n"},
    )
    export = cloud.lifecycle.export_candidate(team.candidate, team.candidate.member_id)
    _row, blob = cloud.lifecycle.export_for(team.candidate, export["export_id"])
    data = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("workspace.json"))
    assert [r["name"] for r in data["tables"]["leads"]] == ["Jane Doe"]
    assert data["tables"]["lead_lists"], "lists travel with the leads"
    org_export = cloud.lifecycle.export_organization(team.owner)
    _row, blob = cloud.lifecycle.export_for(team.owner, org_export["export_id"])
    inner = zipfile.ZipFile(io.BytesIO(blob)).read(
        f"candidates/{team.candidate.member_id}.zip"
    )
    staff_copy = json.loads(zipfile.ZipFile(io.BytesIO(inner)).read("workspace.json"))
    assert b"Jane Doe" not in json.dumps(staff_copy).encode()
    assert not any(name.startswith("lead") for name in staff_copy["tables"]), (
        "leads are third parties' personal data kept for the candidate: an owner's export never holds them"
    )
    admin_export = cloud.lifecycle.export_candidate(
        team.admin, team.candidate.member_id
    )
    _row, blob = cloud.lifecycle.export_for(team.admin, admin_export["export_id"])
    assert b"Jane Doe" not in zipfile.ZipFile(io.BytesIO(blob)).read("workspace.json")
    directory = cloud.workspaces.candidate_dir(team.org_id, team.candidate.member_id)
    assert (directory / "workspace.sqlite3").exists()
    cloud.lifecycle.delete_candidate_workspace(team.owner, team.candidate.member_id)
    assert not directory.exists(), "the database that held the leads is gone"
    assert cloud.workspaces.leads_total(team.org_id) == 0
