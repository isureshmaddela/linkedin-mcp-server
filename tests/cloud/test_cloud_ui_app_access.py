# ruff: noqa: F811  (the `served` fixture is imported from test_cloud_apps and used as a test argument)
"""Who can use which app, in a real browser (cloud-apps.js, cloud-people.js, app.js).

An owner limits Health to staff on the Apps page ("Who can use it"); a candidate then does not see it in the
launcher, and opening it by its address shows a friendly page instead of an error. The owner gives it back to that
one person from their row on the Members page.
"""

from __future__ import annotations

import pytest
from cloud_support import build_org, sign_in
from test_cloud_apps import _open, served  # noqa: F401


def _chromium(playwright):
    try:
        return playwright.chromium.launch()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"Chromium is not available: {error}")


def test_an_owner_limits_an_app_and_a_member_without_it_sees_a_friendly_page(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, apps="all", apps_space="personal")
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            # the owner: Health is for everyone until they choose the roles
            context, page, problems = _open(
                chromium, base, sign_in(app, org.owner), "#/org/apps"
            )
            card = page.locator(".ca-app[data-app=care]")
            card.wait_for(timeout=30000)
            assert card.locator(".ca-access-who").inner_text().strip() == "Everyone"
            card.locator("button[data-access=change]").click()
            page.wait_for_selector(".modal .ca-access-form", timeout=10000)
            page.locator(".modal input[id='ca-everyone-care']").uncheck()
            page.locator(".modal input[id='ca-role-care-candidate']").uncheck()
            page.locator(".modal .actions button", has_text="Save").click()
            page.locator(
                ".ca-app[data-app=care] .ca-access-who", has_text="Billing contact"
            ).wait_for(timeout=30000)
            assert "Candidate" not in card.locator(".ca-access-who").inner_text()
            assert app.tenancy.policy(org.org_id)["app_roles"]["care"] == [
                "owner",
                "admin",
                "coach",
                "billing",
                "auditor",
            ]
            # Careers keeps candidates: their box cannot be cleared
            careers = page.locator(".ca-app[data-app=careers]")
            careers.locator("button[data-access=change]").click()
            page.wait_for_selector(
                ".modal .ca-access-form[data-app=careers]", timeout=10000
            )
            page.locator(".modal input[id='ca-everyone-careers']").uncheck()
            assert page.locator(
                ".modal input[id='ca-role-careers-candidate']"
            ).is_disabled()
            page.locator(".modal .actions button", has_text="Cancel").click()
            assert not problems, problems
            context.close()

            # the candidate: no Health in the launcher; its address shows why
            candidate = sign_in(app, org.candidate)
            context, page, problems = _open(chromium, base, candidate, "#/today")
            page.wait_for_selector("#main .view-stage", timeout=30000)
            page.click("#appSwitcherBtn")
            page.wait_for_selector("#appSwitcher:not([hidden])")
            assert page.locator("#appSwitcher .app-tile[data-app=learn]").count() == 1
            assert page.locator("#appSwitcher .app-tile[data-app=care]").count() == 0
            page.keyboard.press("Escape")
            page.goto(f"{base}/#/care")
            locked = page.locator(".app-locked[data-kind=denied]")
            locked.wait_for(timeout=30000)
            text = locked.inner_text()
            assert "You do not have Health" in text
            assert (
                "Your organization has not given you Health. Ask an owner or admin."
                in text
            )
            assert not [p for p in problems if p.startswith("pageerror")], problems
            context.close()

            # the owner gives Health back to that one person from the Members page
            context, page, problems = _open(
                chromium, base, sign_in(app, org.owner), "#/org/members"
            )
            row = page.locator(".adm-item", has_text=org.candidate.email).first
            row.wait_for(timeout=30000)
            row.locator("button[data-action=member-apps]").click()
            select = page.locator(".modal select[data-app=care]")
            select.wait_for(timeout=10000)
            assert page.locator(".modal select[data-app=careers]").is_disabled()
            select.select_option("allow")
            page.locator(".modal .actions button", has_text="Save").click()
            page.wait_for_selector(".modal", state="detached", timeout=30000)
            assert app.tenancy.policy(org.org_id)["member_apps"][
                org.candidate.member_id
            ] == {
                "allow": ["care"],
                "deny": [],
            }
            assert not problems, problems
            context.close()
            assert candidate.call("GET", "/api/care/medicines")[0] == 200
        finally:
            chromium.close()
