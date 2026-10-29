# ruff: noqa: F811  (the `served` fixture is imported from test_cloud_apps and used as a test argument)
"""Spaces and role navigation in a real browser (auto_apply_assistant/cloud-spaces.js, cloud-core.js).

A member of an organization whose policy is "personal" opens the business apps in a space of their own and sees no
switcher; an administrator switches between their own apps and the organization's (X-Aira-Space: company is sent only
while the company is chosen, and the page says which space is open); an operator lands in the platform console and an
owner on Home; an owner's Careers opens their own workspace without asking first; Home shows what a member earned
this month.
"""

from __future__ import annotations

import pytest
from cloud_support import build_org, sign_in
from test_cloud_apps import _open, served  # noqa: F401
from test_cloud_ui_billing import operator_browser

from aira.suite import kit

COMPANY = {"X-Aira-Space": "company"}


def _chromium(playwright):
    try:
        return playwright.chromium.launch()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Chromium is not available: {error}")


def _wait(page, check, timeout: int = 30000) -> None:
    """Wait until ``check(page)`` holds (the page's CSP refuses wait_for_function's string predicates)."""
    deadline = timeout / 250
    while not check(page):
        deadline -= 1
        assert deadline > 0, f"timed out at {page.url}"
        page.wait_for_timeout(250)


def _hash(page) -> str:
    return page.evaluate("location.hash")


def _record_spaces(page) -> list[tuple[str, str]]:
    """(path, X-Aira-Space header) for every API request the page makes."""
    seen: list[tuple[str, str]] = []
    page.on(
        "request",
        lambda request: (
            seen.append(
                (
                    request.url.split("://", 1)[1].split("/", 1)[1],
                    request.headers.get("x-aira-space", ""),
                )
            )
            if "/api/" in request.url
            else None
        ),
    )
    return seen


def _add_product(browser, name: str, headers=None) -> None:
    status, data = browser.call(
        "POST",
        "/api/shop/products",
        {"name": name, "price_cents": 2500},
        headers=headers,
    )
    assert status == 201, data


def _real_problems(problems: list[str]) -> list[str]:
    # A member's Careers widgets may ask once for what their role does not have; nothing about spaces may fail.
    return [
        p
        for p in problems
        if "x-aira-space" in p.lower()
        or "/api/shop" in p
        or "/api/sites" in p
        or "/api/suite" in p
        or p.startswith("pageerror")
    ]


def test_a_member_runs_their_own_shop_and_sites_with_no_switcher(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, apps="all", apps_space="personal")
    member = sign_in(app, org.candidate)
    _add_product(member, "Hand-made mug")
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            context, page, problems = _open(chromium, base, member, "")
            seen = _record_spaces(page)
            page.goto(f"{base}/#/shop?tab=products")
            page.get_by_text("Hand-made mug").first.wait_for(timeout=30000)
            assert page.locator(".app-locked").count() == 0
            # Everything here is theirs: a badge says so, there is nothing to switch to
            badge = page.locator("#spaceSwitch .space-badge")
            badge.wait_for(timeout=10000)
            assert badge.inner_text().strip() == "Your business"
            assert page.locator("#spaceSwitch .space-opt").count() == 0
            assert page.locator(".space-strip").count() == 0

            page.goto(f"{base}/#/sites")
            page.wait_for_selector("#main .view-stage", timeout=30000)
            page.wait_for_timeout(800)
            assert page.locator(".app-locked").count() == 0
            assert page.locator("#spaceSwitch .space-opt").count() == 0
            assert not [path for path, space in seen if space], seen

            # The launcher lists the business apps for a member, and Home has their earnings
            page.click("#appSwitcherBtn")
            page.wait_for_selector("#appSwitcher:not([hidden])")
            tile = page.locator("#appSwitcher .app-tile[data-app=commerce]")
            assert tile.count() == 1 and tile.get_attribute("aria-disabled") is None
            page.keyboard.press("Escape")
            # Outside the business apps there is no switcher at all
            page.goto(f"{base}/#/today")
            page.locator(".space-earn").wait_for(timeout=30000)
            assert page.locator("#spaceSwitch").is_hidden()
            assert not _real_problems(problems), problems
            context.close()
        finally:
            chromium.close()


def test_an_admin_switches_to_the_company_space_and_back(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, apps="all", apps_space="personal")
    admin = sign_in(app, org.admin)
    _add_product(admin, "Admin side project")
    _add_product(admin, "Company merchandise", COMPANY)
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            context, page, problems = _open(chromium, base, admin, "")
            seen = _record_spaces(page)
            page.goto(f"{base}/#/shop?tab=products")
            page.get_by_text("Admin side project").first.wait_for(timeout=30000)
            switch = page.locator("#spaceSwitch")
            switch.wait_for(timeout=10000)
            mine = switch.locator(".space-opt[data-space=member]")
            company = switch.locator(".space-opt[data-space=company]")
            assert (
                mine.inner_text().strip() == "Mine"
                and mine.get_attribute("aria-pressed") == "true"
            )
            assert company.inner_text().strip() == "Acme Outplacement"
            assert page.get_by_text("Company merchandise").count() == 0
            assert not [path for path, space in seen if space], seen

            seen.clear()
            company.click()
            page.get_by_text("Company merchandise").first.wait_for(timeout=30000)
            assert company.get_attribute("aria-pressed") == "true"
            assert page.get_by_text("Admin side project").count() == 0
            strip = page.locator(".space-strip[data-space=company]")
            assert strip.is_visible() and "Acme Outplacement" in strip.inner_text()
            assert page.evaluate("document.body.dataset.space") == "company"
            shop_calls = [
                (path, space) for path, space in seen if path.startswith("api/shop")
            ]
            assert shop_calls and all(space == "company" for _, space in shop_calls), (
                seen
            )

            # The choice is remembered for this account; Home is never the company's
            page.reload()
            page.get_by_text("Company merchandise").first.wait_for(timeout=30000)
            seen.clear()
            page.goto(f"{base}/#/today")
            page.locator(".space-earn").wait_for(timeout=30000)
            assert not [path for path, space in seen if space], seen
            assert page.locator("#spaceSwitch").is_hidden()

            page.goto(f"{base}/#/shop?tab=products")
            page.get_by_text("Company merchandise").first.wait_for(timeout=30000)
            seen.clear()
            strip.locator("button", has_text="Switch to mine").click()
            page.get_by_text("Admin side project").first.wait_for(timeout=30000)
            assert mine.get_attribute("aria-pressed") == "true"
            assert page.locator(".space-strip[data-space=company]").count() == 0
            assert seen and not [path for path, space in seen if space], seen
            assert not _real_problems(problems), problems
            context.close()
        finally:
            chromium.close()


def test_where_each_role_lands_and_what_the_account_menu_says(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, apps="all", apps_space="personal")
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            # The platform operator: the console, also from the main address once it was used on this browser
            context, page, problems = _open(
                chromium, base, operator_browser(app), "#/platform"
            )
            page.locator("#primaryNav a[data-section=ptenants]").wait_for(timeout=30000)
            page.goto(f"{base}/")
            _wait(page, lambda p: _hash(p).startswith("#/platform"))
            page.locator("#primaryNav a[data-section=ptenants]").wait_for(timeout=30000)
            assert page.locator("#authScreen").is_hidden()
            context.close()

            # The owner: Home, with the way into the organization
            context, page, problems = _open(chromium, base, sign_in(app, org.owner), "")
            _wait(page, lambda p: _hash(p) == "#/today")
            card = page.locator(".space-org")
            card.wait_for(timeout=30000)
            assert "Acme Outplacement" in card.inner_text()
            assert card.locator("a[href='#/org']").count() == 1
            page.click("#accountBtn")
            page.wait_for_selector("#accountMenu:not([hidden])")
            assert page.locator("#accountMeta .account-role").inner_text() == "Owner"
            assert (
                page.locator("#accountMeta .account-org").inner_text()
                == "Acme Outplacement"
            )
            link = page.locator("#accountMenu a[href='#/org']")
            assert link.count() == 1 and link.inner_text().strip() == "Organization"
            link.click()
            _wait(page, lambda p: _hash(p).startswith("#/org"))
            page.locator(".adm-page").wait_for(timeout=30000)
            context.close()

            # A coach: Home too, and the menu leads to their candidates (not an organization page they cannot open)
            context, page, problems = _open(chromium, base, sign_in(app, org.coach), "")
            _wait(page, lambda p: _hash(p) == "#/today")
            page.click("#accountBtn")
            page.wait_for_selector("#accountMenu:not([hidden])")
            assert page.locator("#accountMeta .account-role").inner_text() == "Coach"
            assert page.locator("#accountMenu a[href='#/coach']").count() == 1
            assert page.locator("#accountMenu a[href='#/org']").count() == 0
            context.close()
        finally:
            chromium.close()


def test_an_owner_opens_careers_in_their_own_workspace_without_asking(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, apps="all", apps_space="personal")
    owner = sign_in(app, org.owner)
    status, data = owner.call("POST", "/api/workspace/own")
    assert status in (200, 201), data
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            context, page, problems = _open(chromium, base, owner, "#/jobs")
            banner = page.locator("#cloudActing .cloud-banner")
            banner.wait_for(timeout=30000)
            assert "Your own workspace" in banner.inner_text()
            assert page.get_by_text("Open a workspace first").count() == 0
            assert page.locator("[data-kind=workspace]").count() == 0
            # A candidate is still one click away
            pick = banner.locator("a", has_text="Choose a candidate")
            assert pick.count() == 1
            pick.click()
            _wait(page, lambda p: _hash(p) == "#/org/candidates")
            _wait(page, lambda p: p.locator("#cloudActing .cloud-banner").count() == 0)
            context.close()
        finally:
            chromium.close()


def test_home_shows_what_the_member_earned_this_month(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, apps="all", apps_space="personal")
    member = sign_in(app, org.candidate)
    assert member.call("GET", "/api/shop/products")[0] == 200  # opens their space
    connection = app.workspaces.member_apps_connection(
        org.org_id, org.candidate.member_id
    )
    try:
        kit.record_revenue(
            connection,
            "shop",
            source_type="order",
            source_id="o-1",
            amount_cents=4200,
            label="Mug",
        )
        connection.commit()
    finally:
        connection.close()
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            context, page, problems = _open(chromium, base, member, "#/today")
            card = page.locator(".space-earn")
            card.wait_for(timeout=30000)
            _wait(page, lambda p: "42" in p.locator(".space-earn-value").inner_text())
            assert "Your earnings this month" in card.inner_text()
            card.locator("a[href='#/hub']").click()
            _wait(page, lambda p: _hash(p) == "#/hub")
            page.get_by_text("Revenue this month").first.wait_for(timeout=30000)
            assert page.locator(".app-locked").count() == 0
            assert "Not available" not in page.locator("#main").inner_text()
            assert not _real_problems(problems), problems
            context.close()
        finally:
            chromium.close()
