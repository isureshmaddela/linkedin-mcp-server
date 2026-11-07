"""Service in the Aira frame, in a real browser: "+ New" offers the app's own creates and opens their forms, the
search across the top suggests cases, customers and help articles and opens the one chosen, Enter searches every
case, and the tabs are folders in the left menu. No script errors along the way."""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

pytest.importorskip("patchright.sync_api")

from aira.suite import kit  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_service_ui import post  # noqa: E402

NEW_ITEMS = ["New case", "New help article", "New paid service", "New saved reply"]
FOLDERS = [
    "Inbox",
    "Customers",
    "Live chat",
    "Help centre",
    "Paid help",
    "Saved replies",
    "Settings",
    "Reports",
]


def open_service(page, env, hash_="service"):
    page.goto(f"{env.base}/service#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main [role='tablist']")


def search(page, text):
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)
    page.wait_for_selector("#appSearchList:not([hidden]) .shell-search-option")


def test_service_new_search_and_folders_in_the_frame(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env)
    post(
        context, env, "/api/suite/business", {"name": "Corner Shop", "timezone": "UTC"}
    )
    post(
        context,
        env,
        "/api/service/cases",
        {
            "subject": "Parcel missing",
            "customer_name": "Ana Lopez",
            "customer_email": "ana@example.com",
            "priority": "high",
        },
    )
    post(
        context,
        env,
        "/api/service/cases",
        {"subject": "Broken mug", "customer_name": "Ben Okafor"},
    )
    post(
        context,
        env,
        "/api/service/articles",
        {
            "title": "Parcel delivery times",
            "body": "Three days.",
            "status": "published",
        },
    )

    # The address names the app and the page head names the open folder, not the app again.
    open_service(page, env)
    assert re.search(r"/service#/service", page.url)
    assert page.locator("#main .page-head h1").inner_text() == "Inbox"

    # The left menu shows the tabs as folders with icons; a folder opens its tab.
    folders = page.locator("#primaryNav .shell-sublink")
    assert [t.strip() for t in folders.all_inner_texts()] == FOLDERS
    assert page.locator("#primaryNav .shell-sublink .shell-subdot").count() == 0
    page.click("#primaryNav .shell-sublink[data-tab='replies']")
    until(page, lambda: "tab=replies" in page.url, what="the Saved replies folder")
    page.wait_for_selector(
        "#main [role='tab'][aria-selected='true']:has-text('Saved replies')"
    )
    assert page.locator("#main .page-head h1").inner_text() == "Saved replies"
    assert (
        page.locator("#primaryNav .shell-sublink[aria-current='page']")
        .inner_text()
        .strip()
        == "Saved replies"
    )

    # "+ New" lists the app's creates; each opens that form, and the address forgets ?new= at once.
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    assert [
        t.strip()
        for t in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ] == NEW_ITEMS
    page.click(".shell-new-menu [role='menuitem']:has-text('New case')")
    page.wait_for_selector(".modal:has-text('New case') [name=subject]")
    assert "new=" not in page.url and "tab=inbox" in page.url
    page.fill(".modal [name=subject]", "Wrong size")
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(".drawer:has-text('Wrong size')")
    page.keyboard.press("Escape")

    for label, kind, title in [
        ("New help article", "help", "New article"),
        ("New paid service", "paid", "New paid service"),
        ("New saved reply", "replies", "New saved reply"),
    ]:
        page.click(".shell-new")
        page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")
        page.wait_for_selector(f".modal:has-text('{title}') [name=title]")
        assert f"tab={kind}" in page.url and "new=" not in page.url
        page.keyboard.press("Escape")
        page.wait_for_selector(".modal", state="detached")
    # Back does not open the form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # The search across the top: the app's own records, each opening itself.
    open_service(page, env, "service?tab=settings")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search cases, customers and help articles"
    )
    search(page, "parcel")
    options = page.locator("#appSearchList .shell-search-option")
    texts = options.all_inner_texts()
    assert any("Parcel missing" in t and "#1001" in t for t in texts), texts
    assert any("Parcel delivery times" in t and "Help article" in t for t in texts), (
        texts
    )
    page.locator(
        "#appSearchList .shell-search-option:has-text('Parcel missing')"
    ).click()
    page.wait_for_selector(".drawer:has-text('#1001 · Parcel missing')")
    assert "tab=inbox" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    search(page, "ana")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Customer')"
    ).first.click()
    page.wait_for_selector(".drawer:has-text('ana@example.com')")
    assert "tab=customers" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    search(page, "delivery")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Parcel delivery times')"
    ).click()
    page.wait_for_selector(".modal:has-text('Edit article')")
    assert page.locator(".modal [name=title]").input_value() == "Parcel delivery times"
    page.keyboard.press("Escape")

    # Enter shows every matching case in the inbox's own search.
    search(page, "mug")
    page.keyboard.press("Enter")
    until(page, lambda: "q=mug" in page.url, what="the search results")
    page.wait_for_selector(".sv-results:has-text('“mug”')")
    page.wait_for_selector("tr.suite-row:has-text('Broken mug')")
    assert page.locator("tr.suite-row").count() == 1
    assert page.locator("#main input[aria-label='Search cases']").input_value() == "mug"

    assert not problems, problems


def test_service_on_a_phone_keeps_new_case_and_fits(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env, width=390, height=844)
    post(
        context,
        env,
        "/api/service/cases",
        {
            "subject": "A very long subject about a parcel that never came",
            "customer_name": "Ana Lopez",
        },
    )
    open_service(page, env)
    page.wait_for_selector("tr.suite-row")
    # The menu (and its "+ New") is folded away on a phone, so the page keeps New case; the case number and the
    # customer ride under the subject.
    assert page.locator("#main .sv-phone-new").is_visible()
    assert page.locator(".sv-case-meta").first.is_visible()
    assert "Ana Lopez" in page.locator(".sv-case-meta").first.inner_text()
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    page.click("#main .sv-phone-new")
    page.wait_for_selector(".modal:has-text('New case')")
    assert not problems, problems
