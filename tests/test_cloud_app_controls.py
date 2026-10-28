"""The superadmin's controls over the app catalog: an app switched off for every organization, and seat prices
changed per plan, with billing following both from each subscriber's next period."""

from __future__ import annotations

import pytest
from cloud_support import build_org, sign_in, signup
from test_cloud_apps import _open, served  # noqa: F401
from test_cloud_ui_billing import operator_browser

from aira_cloud import plans

TEAM_MARKETING = 400


def card(overview: dict, app_id: str) -> dict:
    return next(a for a in overview["apps"] if a["id"] == app_id)


def listed(operator, app_id: str) -> dict:
    status, body = operator.call("GET", "/api/platform/apps")
    assert status == 200, body
    return next(a for a in body["apps"] if a["id"] == app_id)


def addons(cloud, org) -> dict[str, int]:
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    return {a["app_id"]: a["amount_cents"] for a in record["addons"]}


def platform_actions(cloud) -> list[str]:
    with cloud.db.read() as connection:
        return [
            r[0]
            for r in connection.execute(
                "SELECT action FROM platform_audit_logs ORDER BY seq"
            )
        ]


def test_the_superadmin_changes_an_apps_price_per_plan(cloud):
    team = build_org(cloud, "Team", plan="team", seats=10, slug_email="t.test")
    other = build_org(cloud, "Other", plan="team", seats=10, slug_email="o.test")
    cloud.billing.subscribe_app(team.owner, "marketing")
    assert addons(cloud, team) == {"marketing": 10 * TEAM_MARKETING}
    operator = operator_browser(cloud)
    before = listed(operator, "marketing")
    assert before["enabled"] is True and before["priced"] is True
    assert (
        before["prices"] == before["default_prices"] == plans.APP_PRICING["marketing"]
    )
    assert before["paying"] == 1 and before["granted"] == 0
    assert listed(operator, "careers")["priced"] is False

    status, body = operator.call(
        "PUT",
        "/api/platform/apps/marketing",
        {"prices": {"team": 900}, "note": "new Leads features"},
    )
    assert status == 200, body
    assert body["prices"]["team"] == 900 and body["changed_plans"] == ["team"]
    assert body["billing"] == {"organizations": 1, "synced": 1, "failed": 0}
    # the subscriber's provider lines carry the new price, without proration: it bills from the next period
    record = cloud.billing.standin.subscription_record(f"sub_{team.org_id[:8]}")
    assert addons(cloud, team) == {"marketing": 9000} and record["prorate"] is False
    # quotes and the Apps page use it at once
    owner = sign_in(cloud, other.owner)
    status, overview = owner.call("GET", "/api/org/apps")
    assert card(overview, "marketing")["seat_cents_month"] == 900
    status, quote = owner.call("POST", "/api/org/apps/marketing/quote", {})
    assert status == 200 and quote["monthly_cents"] == 10 * 900
    assert "app.settings_update" in platform_actions(cloud)

    # 0 = included in the plan, null = not offered on it
    status, body = operator.call(
        "PUT", "/api/platform/apps/marketing", {"prices": {"team": 0, "starter": None}}
    )
    assert status == 200, body
    assert cloud.entitlements.resolve(other.org_id, fresh=True).has_app("marketing")
    assert cloud.app_settings.state("starter", "marketing") == "unavailable"
    assert cloud.app_settings.plan_for("marketing") == "team"
    assert addons(cloud, team) == {}, "an included app is not an add-on any more"

    # a price set back to the default is not stored as a change
    status, body = operator.call(
        "PUT",
        "/api/platform/apps/marketing",
        {"prices": {"team": TEAM_MARKETING, "starter": 500}},
    )
    assert status == 200 and body["changed_plans"] == []
    assert addons(cloud, team) == {"marketing": 10 * TEAM_MARKETING}


def test_an_app_switched_off_is_closed_to_everyone_and_not_billed(cloud):
    trial = signup(cloud, "Trial Org", "owner@trial.test")
    team = build_org(cloud, "Team", plan="team", seats=10, slug_email="t.test")
    starter = build_org(cloud, "Starter", plan="starter", seats=5, slug_email="s.test")
    cloud.billing.subscribe_app(team.owner, "marketing")
    operator = operator_browser(cloud)
    assert (
        operator.call(
            "PUT",
            f"/api/platform/tenants/{starter.org_id}/apps/marketing",
            {"status": "granted"},
        )[0]
        == 200
    )
    status, body = operator.call(
        "PUT", "/api/platform/apps/marketing", {"enabled": False, "note": "incident"}
    )
    assert status == 200 and body["enabled"] is False, body
    for org_id in (trial["org"]["org_id"], team.org_id, starter.org_id):
        assert not cloud.entitlements.resolve(org_id, fresh=True).has_app("marketing")
    coach = sign_in(cloud, team.coach)
    status, body = coach.call("GET", "/api/leads/overview")
    assert status == 403 and body["code"] == "app_disabled", body
    assert "Marketing" in body["error"] and "data is kept" in body["error"]
    # not billed while off, but the organization's choice is kept
    assert addons(cloud, team) == {}
    assert cloud.billing.summary(team.org_id)["addons_monthly_cents"] == 0
    owner = sign_in(cloud, team.owner)
    status, overview = owner.call("GET", "/api/org/apps")
    assert card(overview, "marketing")["status"] == "disabled"
    status, body = owner.call("POST", "/api/org/apps/marketing/subscribe", {})
    assert status == 403 and body["code"] == "app_disabled"
    other = sign_in(cloud, starter.owner)
    status, body = other.call("POST", "/api/org/apps/api/quote", {})
    assert body.get("code") != "app_disabled", "only the switched-off app is closed"
    me = owner.call("GET", "/api/auth/me")[1]
    reason = next(a for a in me["apps"] if a["id"] == "marketing")["reason"]
    assert reason == "Switched off for now."

    # back on: everything returns, and the add-on is billed again
    status, body = operator.call("DELETE", "/api/platform/apps/marketing")
    assert status == 200 and body["enabled"] is True
    assert coach.call("GET", "/api/leads/overview")[0] == 200
    assert addons(cloud, team) == {"marketing": 10 * TEAM_MARKETING}
    assert cloud.entitlements.resolve(starter.org_id, fresh=True).has_app("marketing")
    assert "app.settings_reset" in platform_actions(cloud)
    assert operator.call("DELETE", "/api/platform/apps/marketing")[0] == 404


def test_what_the_controls_refuse_and_who_may_use_them(cloud):
    operator = operator_browser(cloud)
    status, body = operator.call(
        "PUT", "/api/platform/apps/careers", {"enabled": False}
    )
    assert status == 400 and "part of every plan" in body["error"]
    assert operator.call("PUT", "/api/platform/apps/nope", {"enabled": False})[0] == 404
    for prices in (
        {"team": -1},
        {"team": "400"},
        {"team": True},
        {"team": 100_001},
        {"gold": 100},
    ):
        status, body = operator.call(
            "PUT", "/api/platform/apps/marketing", {"prices": prices}
        )
        assert status == 400, (prices, body)
    status, _ = operator.call("PUT", "/api/platform/apps/marketing", {"enabled": "no"})
    assert status == 400
    # an old app id names the app it became
    status, body = operator.call(
        "PUT", "/api/platform/apps/shop", {"prices": {"team": 450}}
    )
    assert status == 200 and body["id"] == "commerce" and body["prices"]["team"] == 450
    support = operator_browser(cloud, "support@aira.test", "support")
    assert support.call("GET", "/api/platform/apps")[0] == 200
    assert (
        support.call("PUT", "/api/platform/apps/marketing", {"enabled": False})[0]
        == 403
    )
    assert support.call("DELETE", "/api/platform/apps/commerce")[0] == 403
    org = build_org(cloud, plan="team", seats=5)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/platform/apps")[0] == 401


def test_the_console_changes_a_price_and_switches_an_app_off_in_a_real_browser(served):  # noqa: F811
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, plan="team", seats=5)
    with playwright_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        try:
            context, page, problems = _open(
                chromium, base, operator_browser(app), "#/platform/apps"
            )
            row = page.locator("tr", has_text="Marketing").first
            row.wait_for(timeout=30000)
            assert "$4.00 / seat" in row.inner_text() and "Business" in row.inner_text()
            assert (
                "Part of every plan"
                in page.locator("tr", has_text="Careers").first.inner_text()
            )
            row.locator("button", has_text="Prices").click()
            page.wait_for_selector(".modal", timeout=10000)
            page.get_by_label("Team price per seat per month (USD)").fill("9")
            page.locator(".modal .actions button", has_text="Save prices").click()
            page.locator("tr", has_text="$9.00 / seat").first.wait_for(timeout=30000)
            assert app.app_settings.price("team", "marketing") == 900
            assert (
                "was $4.00 / seat"
                in page.locator("tr", has_text="Marketing").first.inner_text()
            )
            page.locator("tr", has_text="Marketing").first.locator(
                "button", has_text="Switch off"
            ).click()
            page.wait_for_selector(".modal textarea", timeout=10000)
            page.locator(".modal textarea").fill("provider incident")
            page.locator(".modal .actions button", has_text="Switch off").click()
            page.locator("tr", has_text="OFF for everyone").first.wait_for(
                timeout=30000
            )
            assert not app.app_settings.enabled("marketing")
            # the member probe of /api/auth/me answers 401 while only the console is signed in
            assert not [p for p in problems if "/api/auth/me" not in p], problems
            context.close()
            context, page, problems = _open(
                chromium, base, sign_in(app, org.owner), "#/org/apps"
            )
            card = page.locator(".ca-app[data-app=marketing][data-status=disabled]")
            card.wait_for(timeout=30000)
            assert (
                "Paused" in card.inner_text()
                and "Switched off by Monosage" in card.inner_text()
            )
            assert card.locator("button[data-action]").count() == 0
            assert not problems, problems
            context.close()
        finally:
            chromium.close()


def test_a_link_to_an_app_that_is_not_on_shows_its_closed_page(served):  # noqa: F811
    """#/social or #/academy for an organization without the app, or a role without it, explains why and what to do
    instead of drawing the app and failing with an error box."""
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, plan="team", seats=5)
    app.billing.subscribe_app(org.owner, "marketing")

    def closed(page, kind: str):
        locked = page.locator(f".app-locked[data-kind={kind}]")
        locked.wait_for(timeout=30000)
        assert page.locator(".error-box").count() == 0
        return locked.inner_text()

    def problems_left(problems):
        # until the app list arrives a view may ask once and be told 402 app_required (or, for the app switched off
        # for everyone, 403 app_disabled); nothing else may fail
        return [
            p
            for p in problems
            if not p.startswith("402 ")
            and not (p.startswith("403 ") and "/api/marketing/" in p)
        ]

    with playwright_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        try:
            context, page, problems = _open(
                chromium, base, sign_in(app, org.candidate), "#/social"
            )
            text = closed(page, "off")
            assert "Social is not turned on" in text and "Ask an owner" in text
            page.goto(f"{base}/#/academy?tab=overview")
            text = closed(page, "off")
            assert "Courses & Events is not turned on" in text
            assert page.locator("text=New course").count() == 0
            page.goto(f"{base}/#/leads")  # Marketing is on, but Leads is the business's
            assert "Marketing is not part of your role" in closed(page, "role")
            assert not problems_left(problems), problems
            context.close()

            context, page, problems = _open(
                chromium, base, sign_in(app, org.owner), "#/academy?tab=overview"
            )
            closed(page, "off")
            assert page.locator(".app-locked a[href='#/org/apps']").count() == 1
            app.app_settings.update("marketing", enabled=False)
            app.entitlements.invalidate()
            page.goto(f"{base}/#/marketing")
            page.reload()
            assert "Marketing is switched off for now" in closed(page, "disabled")
            assert not problems_left(problems), problems
            context.close()
        finally:
            chromium.close()
