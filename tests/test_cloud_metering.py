"""Pooled SerpApi searches and Aira API search overage: counted, limited and billed."""

from __future__ import annotations

import json

import pytest
from cloud_support import build_org, local_module, sign_in
from serpapi_stub import DEFAULT_KEY, SerpApiStub

from aira_cloud.config import DAY
from aira_cloud.worker import Worker


@pytest.fixture
def serpapi(monkeypatch):
    stub = SerpApiStub()
    stub.start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", stub.base_url)
    yield stub
    stub.stop()


def team_with_pool(cloud, name="Pool Org", slug="pool.test", *, overrides=None):
    cloud.config.pooled_serpapi_key = DEFAULT_KEY
    org = build_org(cloud, name, plan="team", seats=25, slug_email=slug)
    cloud.tenancy.update_policy(
        org.owner,
        {
            "allowed_sources": ["aira_jobs", "company_boards", "google_jobs"],
            "use_pooled_serpapi": True,
        },
    )
    if overrides:
        override(cloud, org, overrides)
    return org


def override(cloud, org, values):
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overrides = ? WHERE org_id = ?",
            (json.dumps(values), org.org_id),
        )
    cloud.entitlements.invalidate(org.org_id)


def search(browser, keywords):
    return browser.call(
        "POST",
        "/api/jobs/search",
        {"source": "google_jobs", "keywords": keywords, "location": "Seattle"},
    )


def used(cloud, org, metric):
    return cloud.usage.used(org.org_id, metric)


def test_searches_with_the_pooled_key_are_counted_apart_and_cache_hits_are_free(
    cloud, serpapi
):
    org = team_with_pool(cloud)
    candidate = sign_in(cloud, org.candidate)
    assert search(candidate, "cloud architect")[0] == 200
    assert (used(cloud, org, "pooled_searches"), used(cloud, org, "searches")) == (1, 1)
    assert search(candidate, "cloud architect")[0] == 200  # answered from the cache
    assert serpapi.count("/search.json") == 1
    assert used(cloud, org, "pooled_searches") == 1, (
        "a cache hit costs the pool nothing"
    )
    assert used(cloud, org, "searches") == 2, "the ordinary counter is unchanged"
    assert search(candidate, "data engineer")[0] == 200
    assert used(cloud, org, "pooled_searches") == 2
    usage = sign_in(cloud, org.owner).call("GET", "/api/org/usage")[1]
    assert usage["pooled_searches"] == {"used": 2, "limit": 2000, "overage": 0}


def test_an_organization_with_its_own_key_never_touches_the_pool(cloud, serpapi):
    org = team_with_pool(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY})[0] == 200
    assert search(sign_in(cloud, org.candidate), "cloud architect")[0] == 200
    assert used(cloud, org, "searches") == 1
    assert used(cloud, org, "pooled_searches") == 0


def test_the_pool_is_limited_by_the_plan_and_names_what_to_change(cloud, serpapi):
    org = team_with_pool(cloud, overrides={"pooled_searches_per_month": 2})
    candidate = sign_in(cloud, org.candidate)
    assert search(candidate, "cloud architect")[0] == 200
    assert search(candidate, "data engineer")[0] == 200
    status, body = search(candidate, "platform engineer")
    assert status == 402 and body["code"] == "plan_required", body
    assert body["limit"] == "pooled_searches_per_month" and body["limit_value"] == 2
    assert "pooled" in body["error"].lower()
    assert serpapi.count("/search.json") == 2, (
        "the refused search never reached SerpApi"
    )
    assert used(cloud, org, "pooled_searches") == 2


def test_past_the_limit_the_pool_keeps_working_and_is_billed_when_overage_is_on(
    cloud, serpapi
):
    org = team_with_pool(cloud, overrides={"pooled_searches_per_month": 2})
    cloud.billing.set_overage(org.owner, True)
    candidate = sign_in(cloud, org.candidate)
    for words in ("cloud architect", "data engineer", "platform engineer", "sre"):
        assert search(candidate, words)[0] == 200
    assert used(cloud, org, "pooled_searches") == 4
    assert used(cloud, org, "pooled_searches:overage") == 2
    cloud.clock.advance(seconds=35 * DAY)
    cloud.billing.process_due()
    cloud.billing.process_due()
    invoices = [i for i in cloud.billing.invoices(org.owner) if i["kind"] == "overage"]
    assert len(invoices) == 1
    lines = (
        json.loads(invoices[0]["lines"])
        if isinstance(invoices[0]["lines"], str)
        else invoices[0]["lines"]
    )
    assert any(
        "pooled SerpApi searches" in line["description"]
        and line["amount_cents"] == 2 * 4
        for line in lines
    ), lines


def test_a_failed_pooled_search_gives_its_search_back(cloud, serpapi):
    org = team_with_pool(cloud, overrides={"pooled_searches_per_month": 5})
    candidate = sign_in(cloud, org.candidate)
    status, _ = search(candidate, "__server_error__")
    assert status >= 400
    assert used(cloud, org, "pooled_searches") == 0, "nothing was charged for a failure"


def test_console_search_overage_is_billed_for_api_searches(cloud, serpapi):
    org = build_org(cloud, "Api Overage", plan="team", seats=25, slug_email="api.test")
    override(cloud, org, {"api_searches_per_month": 1})
    cloud.billing.set_overage(org.owner, True)
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY})[0] == 200
    for query in ("coffee", "tea", "cocoa"):
        status, _ = owner.call(
            "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": query}}
        )
        assert status == 200
    assert used(cloud, org, "api_searches") == 3
    assert used(cloud, org, "api_searches:overage") == 2
    cloud.clock.advance(seconds=35 * DAY)
    cloud.billing.process_due()
    invoice = next(
        i for i in cloud.billing.invoices(org.owner) if i["kind"] == "overage"
    )
    lines = (
        json.loads(invoice["lines"])
        if isinstance(invoice["lines"], str)
        else invoice["lines"]
    )
    assert any(
        "Aira API searches" in line["description"] and line["amount_cents"] == 2 * 2
        for line in lines
    ), lines


def test_plan_versions_published_before_these_limits_still_get_them(cloud):
    org = build_org(cloud, "Old Plan", plan="team", seats=25, slug_email="old.test")
    with cloud.db.write() as connection:
        for table in ("plans", "plan_versions"):
            row = connection.execute(
                f"SELECT entitlements, pricing FROM {table} WHERE plan_id = 'team'"
            ).fetchone()
            entitlements = json.loads(row["entitlements"])
            pricing = json.loads(row["pricing"])
            entitlements.pop("pooled_searches_per_month", None)
            pricing["overage"].pop("api_search_cents", None)
            pricing["overage"].pop("pooled_search_cents", None)
            connection.execute(
                f"UPDATE {table} SET entitlements = ?, pricing = ? WHERE plan_id = 'team'",
                (json.dumps(entitlements), json.dumps(pricing)),
            )
    cloud.entitlements.invalidate(org.org_id)
    assert (
        cloud.entitlements.resolve(org.org_id).limit("pooled_searches_per_month")
        == 2000
    )
    cloud.usage.record(org.org_id, "api_searches:overage", 3)
    cloud.usage.record(org.org_id, "pooled_searches:overage", 5)
    cloud.billing.set_overage(org.owner, True)
    cloud.clock.advance(seconds=35 * DAY)
    cloud.billing.process_due()
    invoice = next(
        i for i in cloud.billing.invoices(org.owner) if i["kind"] == "overage"
    )
    assert invoice["amount_cents"] == 3 * 2 + 5 * 4, (
        "the shipped rates apply to old plan versions"
    )


def test_a_starter_organization_cannot_use_the_pool(cloud, serpapi):
    cloud.config.pooled_serpapi_key = DEFAULT_KEY
    org = build_org(
        cloud, "Starter Pool", plan="starter", seats=10, slug_email="st.test"
    )
    with pytest.raises(Exception) as caught:  # the policy switch needs the plan feature
        cloud.tenancy.update_policy(org.owner, {"use_pooled_serpapi": True})
    assert getattr(caught.value, "status", 0) == 402


def test_monitors_run_with_the_pool_are_metered_by_the_credits_they_use(cloud, serpapi):
    org = team_with_pool(cloud, "Monitor Pool", "mon.test")
    owner = sign_in(cloud, org.owner)
    status, saved = owner.call(
        "POST",
        "/api/airaapi/saved",
        {
            "name": "Architects",
            "engine": "google_jobs",
            "params": {"q": "cloud architect"},
        },
    )
    assert status == 201, saved
    status, monitor = owner.call(
        "POST",
        "/api/airaapi/monitors",
        {"saved_id": saved["id"], "interval_minutes": 60},
    )
    assert status == 201, monitor
    with cloud.workspaces.bind_org_api(org.org_id):
        with local_module()._connect() as connection:
            connection.execute(
                "UPDATE airaapi_monitors SET next_run_at = '2000-01-01T00:00:00+00:00'"
            )
            connection.commit()
    assert cloud.scheduler.enqueue_due()["monitors.org"] == 1
    Worker(cloud, threads=1, run_tick=False).run_once()
    assert serpapi.count("/search.json") == 1
    assert used(cloud, org, "api_searches") == 1
    assert used(cloud, org, "pooled_searches") == 1, (
        "the organization's monitor spent a pooled credit"
    )
