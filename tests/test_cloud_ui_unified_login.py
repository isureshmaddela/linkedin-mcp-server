# ruff: noqa: F811  (the `served` fixture is imported from test_cloud_apps and used as a test argument)
"""One sign-in page for everyone, in a real browser (cloud-auth.js, cloud-platform.js, cloud-boot.js).

Members, organization administrators and Monosage's own operators sign in on the same page at the main address
(and at #/platform, which shows the same page); nothing on it says "Monosage staff". An operator continues to the
console through its second factor; a member lands on Home; someone who is both lands on the console and moves to
their apps and back from the account menu without signing in again. Sign out signs out of everything.
"""

from __future__ import annotations

import pytest
from cloud_support import PASSWORD, build_org, signup
from test_cloud_apps import served  # noqa: F401

from monosage_cloud import crypto

OPS = "ops@aira.test"


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


def _fresh(chromium, base: str, path: str = ""):
    context = chromium.new_context(viewport={"width": 1200, "height": 900})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base}/{path}", wait_until="load")
    page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
    return context, page, errors


def _sign_in(page, email: str, password: str = PASSWORD) -> None:
    form = page.locator("#authScreen form")
    form.locator("input[type=email]").fill(email)
    form.locator("input[type=password]").fill(password)
    form.locator("button[type=submit]").click()


def _assert_sign_in_page(page) -> None:
    screen = page.locator("#authScreen")
    assert page.locator("#caTitle").inner_text() == "Sign in"
    assert screen.locator("input[type=email]").count() == 1
    assert "Monosage staff" not in screen.inner_text()
    assert screen.locator("a[href='#/platform']").count() == 0
    assert page.locator("#cpEmail").count() == 0, "no separate operator form"


def _console(page) -> None:
    page.locator("#primaryNav a[data-section=ptenants]").wait_for(timeout=30000)
    assert page.locator("#authScreen").is_hidden()
    _wait(page, lambda p: _hash(p).startswith("#/platform"))


def _enrol(page, app) -> str:
    secret = page.locator("#cpSecret")
    secret.wait_for(timeout=30000)
    key = secret.inner_text().strip()
    page.locator("#cpEnrolCode").fill(crypto.totp_code(key, app.clock.now()))
    page.locator("#authScreen button[type=submit]").click()
    page.locator("#cpSaved").wait_for(timeout=30000)
    page.locator("#cpSaved").click()
    return key


def _verify(page, app, key: str) -> None:
    page.locator("#cpCode").wait_for(timeout=30000)
    app.clock.advance(seconds=30)
    page.locator("#cpCode").fill(crypto.totp_code(key, app.clock.now()))
    page.locator("#authScreen button[type=submit]").click()


def _account_menu(page):
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    return page.locator("#accountMenu")


def _signed_out_everywhere(page, base: str) -> None:
    for path in ("/api/auth/me", "/api/platform/me"):
        status = page.evaluate(
            "async (path) => (await fetch(path, {headers: {'X-Requested-With': 'aira'}})).status",
            path,
        )
        assert status == 401, path


def test_one_page_wrong_password_and_an_operator_through_mfa_to_the_console(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    app.identity.create_operator(OPS, PASSWORD, role="platform_admin")
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            context, page, errors = _fresh(chromium, base)
            _assert_sign_in_page(page)
            # the console's address shows the same page
            page.goto(f"{base}/#/platform")
            page.reload()
            page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
            _assert_sign_in_page(page)

            _sign_in(page, OPS, "not the password at all")
            notice = page.locator("#authScreen .ca-notice")
            _wait(page, lambda p: "incorrect" in notice.inner_text())
            assert notice.inner_text() == "Email or password is incorrect."

            # an operator: set up the second factor, then the console
            _sign_in(page, OPS)
            key = _enrol(page, app)
            _console(page)
            assert page.locator("#accountBtnEmail").inner_text() == OPS
            # sign out signs out of everything and shows the sign-in page again
            menu = _account_menu(page)
            assert menu.locator("a[href='#/today']").count() == 0, (
                "no apps of their own"
            )
            menu.locator("#logoutBtn").click()
            page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
            _assert_sign_in_page(page)
            _signed_out_everywhere(page, base)

            # the next time: the code from the authenticator, from the main address (an app's address too)
            page.goto(f"{base}/careers")
            page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
            _sign_in(page, OPS)
            _verify(page, app, key)
            _console(page)
            assert not errors, errors
            context.close()
        finally:
            chromium.close()


def test_members_land_on_home_by_role_and_the_organization_picker(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, plan="starter", seats=10, apps_space="personal")
    signup(
        app, "Second Org", org.coach.email
    )  # the coach also owns another organization
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            # the owner: Home, with the organization's pages in the account menu, and no console
            context, page, errors = _fresh(chromium, base)
            _sign_in(page, org.owner.email)
            _wait(page, lambda p: _hash(p) == "#/today")
            menu = _account_menu(page)
            assert menu.locator("a[href='#/org']").count() == 1
            assert menu.locator("a[href='#/platform']").count() == 0
            _signed_in = page.evaluate(
                "async () => (await fetch('/api/platform/me', {headers: {'X-Requested-With': 'aira'}})).status"
            )
            assert _signed_in == 401
            menu.locator("#logoutBtn").click()
            page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
            _signed_out_everywhere(page, base)
            context.close()

            # a member in two organizations picks one, then lands on Home
            context, page, errors = _fresh(chromium, base)
            _sign_in(page, org.coach.email)
            picker = page.locator("#caOrg")
            picker.wait_for(timeout=30000)
            assert {o.strip() for o in picker.locator("option").all_inner_texts()} == {
                "Acme Outplacement",
                "Second Org",
            }
            picker.select_option(label="Acme Outplacement")
            page.locator("#authScreen input[type=password]").fill(PASSWORD)
            page.locator("#authScreen button[type=submit]").click()
            _wait(page, lambda p: _hash(p) == "#/today")
            assert page.locator("#accountMeta .account-org").inner_text() == (
                "Acme Outplacement"
            )
            menu = _account_menu(page)
            assert menu.locator("a[href='#/org']").count() == 0
            assert menu.locator("a[href='#/platform']").count() == 0
            assert not errors, errors
            context.close()
        finally:
            chromium.close()


def test_someone_who_is_both_lands_on_the_console_and_moves_to_their_apps_and_back(
    served,
):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, plan="starter", seats=10, apps_space="personal")
    app.identity.create_operator(org.owner.email, PASSWORD, role="platform_admin")
    with playwright_api.sync_playwright() as playwright:
        chromium = _chromium(playwright)
        try:
            context, page, errors = _fresh(chromium, base)
            _sign_in(page, org.owner.email)
            _enrol(page, app)
            _console(page)
            # the console's account menu leads to their own apps, without signing in again
            menu = _account_menu(page)
            apps = menu.locator("a[href='#/today']")
            assert apps.count() == 1 and apps.inner_text().strip() == "Your apps"
            apps.click()
            _wait(page, lambda p: _hash(p) == "#/today")
            page.locator("#accountMeta .account-role").wait_for(
                state="attached", timeout=30000
            )
            assert page.locator("#authScreen").is_hidden()
            assert page.locator("#accountMeta .account-role").inner_text() == "Owner"
            # and the apps' account menu leads back to the console
            menu = _account_menu(page)
            assert menu.locator("a[href='#/org']").count() == 1
            console = menu.locator("a[href='#/platform']")
            assert (
                console.count() == 1 and console.inner_text().strip() == "Admin console"
            )
            console.click()
            _console(page)
            # the main address opens the console for them on this browser
            page.goto(f"{base}/")
            _console(page)
            # sign out signs out of both
            _account_menu(page).locator("#logoutBtn").click()
            page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
            _signed_out_everywhere(page, base)
            page.goto(f"{base}/")
            page.locator("#authScreen .cloud-auth").wait_for(timeout=30000)
            _assert_sign_in_page(page)
            assert not errors, errors
            context.close()
        finally:
            chromium.close()
