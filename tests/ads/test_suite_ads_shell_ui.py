"""Ads in the Aira frame, in a real browser: "+ New" offers a campaign, an ad and recorded sales and opens their
forms, the search across the top suggests campaigns and ads and opens the one chosen, Enter shows every match on
Campaigns, and the tabs are folders in the left menu. A phone keeps New campaign on the page and fits its width. No
script errors along the way."""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

pytest.importorskip("patchright.sync_api")

from videos_support import PNG, upload  # noqa: E402

from aira.suite import kit  # noqa: E402
from aira.suite.videos import db  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_videos_ui import Requester  # noqa: E402

FOLDERS = ["Overview", "Campaigns", "Reports", "Balance", "Admin"]
NEW_ITEMS = ["New campaign", "New ad", "Record sales", "Record a payment"]


def campaign(me, name, url):
    status, body = me.call(
        "POST",
        "/api/ads/campaigns",
        {
            "name": name,
            "objective": "views",
            "bid_cents": 200,
            "total_budget_cents": 5000,
            "daily_budget_cents": 1000,
            "landing_url": url,
        },
    )
    assert status == 201, body
    return body["campaign"]


def seed(context, env):
    me = Requester(context, env.base)
    me.call("POST", "/api/suite/business", {"name": "Corner Bakery", "timezone": "UTC"})
    sweets = campaign(me, "Diwali sweets", "https://bakery.example/diwali")
    cakes = campaign(me, "Wedding cakes", "https://bakery.example/cakes")
    status, body = me.call(
        "POST",
        f"/api/ads/campaigns/{cakes['id']}/creatives",
        {
            "kind": "banner",
            "headline": "Three-tier almond cake",
            "cta": "Shop now",
            "file_id": upload(me, "image", PNG, base="/api/ads"),
        },
    )
    assert status == 201, body
    return me, sweets, cakes, body["creative"]


def open_ads(page, env, hash_="ads"):
    page.goto(f"{env.base}/ads#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main [role='tablist']")


def search(page, text, expect=""):
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)
    page.wait_for_selector(
        f"#appSearchList:not([hidden]) .shell-search-option:has-text('{expect or text}')"
    )


def new_item(page, label):
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")


def test_ads_new_search_and_folders_in_the_frame(browser, env):
    kit.forget_schema()
    db.forget()
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    _me, sweets, cakes, creative = seed(context, env)

    # The address names the app and the page head names the open folder, not the app again.
    open_ads(page, env)
    assert re.search(r"/ads#/ads", page.url)
    assert page.locator("#main .page-head h1").inner_text() == "Overview"
    assert "Corner Bakery" in page.locator("#main .page-head p").inner_text()
    # "+ New" is in the menu, so the page's own New campaign is for phones only.
    assert not page.locator("#main .ads-phone-new").is_visible()

    # The left menu shows the tabs as folders with icons; a folder opens its tab.
    folders = page.locator("#primaryNav .shell-sublink")
    assert [t.strip() for t in folders.all_inner_texts()] == FOLDERS
    assert page.locator("#primaryNav .shell-sublink .shell-subdot").count() == 0
    page.click("#primaryNav .shell-sublink[data-tab='reports']")
    until(page, lambda: "tab=reports" in page.url, what="the Reports folder")
    page.wait_for_selector(
        "#main [role='tab'][aria-selected='true']:has-text('Reports')"
    )
    assert page.locator("#main .page-head h1").inner_text() == "Reports"
    page.wait_for_selector("text=Nothing to report yet")
    assert (
        page.locator("#primaryNav .shell-sublink[aria-current='page']")
        .inner_text()
        .strip()
        == "Reports"
    )

    # "+ New" lists the app's creates (the computer's owner may also record a payment).
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    assert [
        t.strip()
        for t in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ] == NEW_ITEMS
    page.keyboard.press("Escape")

    # New campaign opens the campaign form on Campaigns, and the address forgets ?new= at once.
    new_item(page, "New campaign")
    page.wait_for_selector(".modal:has-text('New campaign') [name=name]")
    assert "tab=campaigns" in page.url and "new=" not in page.url
    page.fill(".modal [name=name]", "Birthday treats")
    page.fill(".modal [name=landing_url]", "https://bakery.example/treats")
    page.locator(".modal .actions button.primary").click()
    page.wait_for_selector("#main .page-head h1:has-text('Birthday treats')")
    assert "campaign=" in page.url

    # On a campaign's page, New ad goes to that campaign's Add an ad form.
    new_item(page, "New ad")
    until(
        page,
        lambda: page.evaluate(
            "() => document.activeElement && document.activeElement.getAttribute('aria-label') === 'Kind of ad'"
        ),
        what="the Add an ad form",
    )
    assert "new=" not in page.url
    # Record sales opens the campaign's sales form.
    new_item(page, "Record sales")
    page.wait_for_selector(
        ".modal:has-text('Record sales from this campaign') [name=orders]"
    )
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    # From the list, New ad asks which campaign first, then opens it at the form.
    open_ads(page, env, "ads?tab=campaigns")
    new_item(page, "New ad")
    page.wait_for_selector(".modal:has-text('Which campaign is the ad for?')")
    page.select_option(".modal [name=campaign]", sweets["id"])
    page.locator(".modal .actions button.primary").click()
    page.wait_for_selector("#main .page-head h1:has-text('Diwali sweets')")
    page.wait_for_selector("#adsAddAd")
    assert "new=" not in page.url

    # Record a payment opens the owner's payment form on Admin.
    new_item(page, "Record a payment")
    page.wait_for_selector(".modal:has-text('Record a payment') [name=reference]")
    assert "tab=admin" in page.url and "new=" not in page.url
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")
    # Back does not open a form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # The search across the top: campaigns and ads, each opening itself.
    open_ads(page, env, "ads?tab=balance")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search campaigns and ads"
    )
    search(page, "wedding", "Wedding cakes")
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any("Wedding cakes" in t and "Campaign" in t for t in texts), texts
    page.locator(
        "#appSearchList .shell-search-option:has-text('Wedding cakes')"
    ).first.click()
    page.wait_for_selector("#main .page-head h1:has-text('Wedding cakes')")
    assert f"campaign={cakes['id']}" in page.url

    search(page, "almond", "Three-tier almond cake")
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any(
        "Three-tier almond cake" in t and "Ad" in t and "Wedding cakes" in t
        for t in texts
    ), texts
    page.locator(
        "#appSearchList .shell-search-option:has-text('Three-tier almond cake')"
    ).click()
    page.wait_for_selector(f"#ad-{creative['id']}.ads-found")
    until(
        page,
        lambda: page.evaluate(
            f"() => document.activeElement && document.activeElement.id === 'ad-{creative['id']}'"
        ),
        what="the ad to be shown",
    )

    # Enter shows every match on Campaigns: the campaign list and the ads, with the query in the page's own search.
    search(page, "cake")
    page.keyboard.press("Enter")
    until(page, lambda: "q=cake" in page.url, what="the search results")
    page.wait_for_selector(".ads-results:has-text('“cake”')")
    page.wait_for_selector(".ads-campaigns tr.suite-row:has-text('Wedding cakes')")
    assert page.locator(".ads-campaigns").first.locator("tr.suite-row").count() == 1
    page.wait_for_selector("text=Ads that match (1)")
    assert (
        page.locator("#main input[aria-label='Search campaigns and ads']").input_value()
        == "cake"
    )

    assert not problems, problems
    context.close()


def test_ads_on_a_phone_keeps_new_campaign_and_fits(browser, env):
    kit.forget_schema()
    db.forget()
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    open_ads(page, env, "ads?tab=campaigns")
    page.wait_for_selector("tr.suite-row")
    # The menu (and its "+ New") is folded away on a phone, so the page keeps New campaign; each campaign's status
    # and spending ride under its name instead of in columns.
    assert page.locator("#main .ads-phone-new").is_visible()
    meta = page.locator(".ads-campaigns .ads-row-meta").first
    assert meta.is_visible() and "of $50.00" in meta.inner_text()
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    page.click("#main .ads-phone-new")
    page.wait_for_selector(".modal:has-text('New campaign')")
    assert not problems, problems
    context.close()
