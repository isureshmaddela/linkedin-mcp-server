"""Developers in the Aira frame, in a real browser against the single-user server and the stand-in SerpApi: "+ New"
offers the console's create actions and opens their forms, the search across the top finds the console's own records
(rank projects, saved searches, stored searches, engines) and Enter shows the matching stored searches, the
console's tabs are folders in the left menu, the Connected apps page offers a next step, and phones fit."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_cloud_apps import _open, served  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = (
    Path(os.environ["API_SHELL_SHOTS"]) if os.environ.get("API_SHELL_SHOTS") else None
)
NO_OVERFLOW = "() => document.documentElement.scrollWidth <= window.innerWidth + 1"


def shot(page, name):
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"))


def call(context, env, method, path, body=None, expect=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert response.status < 300 if expect is None else response.status == expect, (
        path,
        response.status,
        response.text(),
    )
    return response.json()


def real_problems(problems):
    # The page asks who is signed in before the session cookie is used; that first 401 is expected.
    return [p for p in problems if "401" not in p]


def seed(context, env):
    call(
        context,
        env,
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "coffee grinders"}},
    )
    call(
        context,
        env,
        "POST",
        "/api/airaapi/saved",
        {
            "name": "Coffee prices weekly",
            "engine": "google",
            "params": {"q": "coffee prices"},
            "tags": ["coffee"],
        },
    )
    return call(
        context,
        env,
        "POST",
        "/api/airaapi/rank",
        {
            "name": "Coffee shop site",
            "domain": "example.com",
            "client_name": "Bean Co",
            "keywords": "coffee beans",
            "interval_minutes": 0,
            "monthly_fee": 90,
        },
        expect=201,
    )


def open_new(page, label):
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")


def test_new_search_and_folders_in_the_aira_frame(browser, env):
    context, page, problems = signed_in_page(browser, env)
    project = seed(context, env)
    go(page, env, "airaapi?tab=overview")
    page.wait_for_selector("#main h1:has-text('Overview')")
    assert page.evaluate("() => location.pathname") == "/developers"

    # the console's tabs are folders in the left menu, each opening its tab; the strip under the heading is for phones
    folders = page.locator("#primaryNav .shell-sublink")
    assert folders.count() == 12
    assert page.locator("#primaryNav .shell-sublink svg").count() == 12
    assert not page.locator(".aa-subnav").is_visible()
    page.click("#primaryNav .shell-sublink[data-tab='history']")
    page.wait_for_selector("#main h1:has-text('History')")
    assert "tab=history" in page.url
    assert (
        page.locator("#primaryNav .shell-sublink[data-tab='history']").get_attribute(
            "aria-current"
        )
        == "page"
    )
    shot(page, "folders")

    # "+ New" offers the console's create actions; each opens its own form, once
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    labels = [
        text.strip()
        for text in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ]
    assert labels == [
        "New search",
        "New saved search",
        "New monitor",
        "New rank project",
        "New API key",
    ]
    shot(page, "new-menu")
    page.keyboard.press("Escape")

    open_new(page, "New rank project")
    page.wait_for_selector(".modal h2:has-text('New rank project')")
    until(page, lambda: "new=" not in page.url, what="?new= to leave the address")
    shot(page, "new-rank")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    open_new(page, "New saved search")
    page.wait_for_selector(".modal h2:has-text('New saved search')")
    until(page, lambda: "new=" not in page.url, what="?new= to leave the address")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    open_new(page, "New monitor")
    page.wait_for_selector(".modal h2:has-text('New monitor')")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    open_new(page, "New API key")
    page.wait_for_selector("#aaKeyName")
    until(
        page,
        lambda: (
            page.evaluate("() => document.activeElement && document.activeElement.id")
            == "aaKeyName"
        ),
        what="the key name to take focus",
    )

    open_new(page, "New search")
    page.wait_for_selector("#main h1:has-text('Playground')")
    page.wait_for_selector(".pg-form-host input")
    until(page, lambda: "new=" not in page.url, what="?new= to leave the address")

    # the search across the top finds the console's own records and opens them
    page.click("#appSearchInput")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search searches, engines and projects"
    )
    page.keyboard.type("coffee")
    page.wait_for_selector(
        "#appSearchList [role='option']:has-text('Coffee shop site')"
    )
    options = page.locator("#appSearchList [role='option']").all_inner_texts()
    assert any("Coffee prices weekly" in text for text in options)
    assert any("coffee grinders" in text for text in options)
    shot(page, "search")
    page.click("#appSearchList [role='option']:has-text('Coffee shop site')")
    page.wait_for_selector("#main h2:has-text('Coffee shop site')")
    assert f"project={project['id']}" in page.url

    page.fill("#appSearchInput", "duckduck")
    page.wait_for_selector("#appSearchList [role='option']:has-text('DuckDuckGo')")
    page.click("#appSearchList [role='option']:has-text('DuckDuckGo')")
    until(
        page,
        lambda: "tab=playground" in page.url and "engine=duckduckgo" in page.url,
        what="the engine in the Playground",
    )

    # Enter shows the stored searches that match, on History
    page.fill("#appSearchInput", "grinders")
    page.wait_for_selector("#appSearchList [role='option']")
    page.keyboard.press("Enter")
    page.wait_for_selector("#main h1:has-text('History')")
    assert "q=grinders" in page.url
    assert page.input_value("#aaHistSearch") == "grinders"
    page.wait_for_selector(".aa-table .aa-title:has-text('coffee grinders')")
    assert page.locator(".aa-table tbody tr").count() == 1
    shot(page, "results")

    # a search that matches nothing offers a next step
    page.fill("#appSearchInput", "zzqx")
    page.keyboard.press("Enter")
    page.wait_for_selector(".empty:has-text('No stored search matches')")
    page.wait_for_selector(".empty a:has-text('Find engines')")

    assert real_problems(problems) == []
    context.close()


def test_connected_apps_and_phones(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    go(page, env, "developers")
    page.wait_for_selector("#main h1:has-text('Connected apps')")
    page.wait_for_selector(".empty a:has-text('Create an API key')")
    page.click(".empty a:has-text('Create an API key')")
    page.wait_for_selector("#aaKeyName")
    for tab in ("overview", "history", "monitors", "rank", "keys", "earnings"):
        go(page, env, f"airaapi?tab={tab}")
        page.wait_for_selector("#aaContent > :not(.skeleton)")
        page.wait_for_timeout(300)
        # on a phone the tabs are the strip under the heading, scrolled to the open one
        assert page.locator(".aa-subnav").is_visible()
        assert page.locator(".aa-subnav a[aria-current='page']").is_visible(), tab
        assert page.evaluate(NO_OVERFLOW), tab
        shot(page, f"phone-{tab}")
    assert real_problems(problems) == []
    context.close()


def test_hosted_new_follows_the_role_and_opens_the_token_form(served, browser):
    from cloud_support import build_org, sign_in, subscribe_apps

    app, base = served
    org = build_org(app, plan="team", seats=25)
    subscribe_apps(app, org.owner, ["api"])

    context, page, problems = _open(
        browser, base, sign_in(app, org.owner), "#/developers?tab=tokens&new=token"
    )
    page.wait_for_selector(".modal h2:has-text('New access token')", timeout=30000)
    until(page, lambda: "new=" not in page.url, what="?new= to leave the address")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")
    # Connected apps' tabs are menu folders too
    assert (
        page.locator(
            "#primaryNav .shell-subnav[data-section='developers'] .shell-sublink"
        ).count()
        == 6
    )
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    labels = [
        text.strip()
        for text in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ]
    assert labels == [
        "New search",
        "New saved search",
        "New monitor",
        "New rank project",
        "New API key",
        "New access token",
    ]
    assert problems == []
    context.close()

    # an auditor reads the console but changes nothing: only a personal token of their own
    context, page, problems = _open(
        browser, base, sign_in(app, org.auditor), "#/developers"
    )
    page.wait_for_selector(".shell-new", timeout=30000)
    assert page.locator(".shell-new").inner_text().strip() == "New access token"
    assert page.get_attribute(".shell-new", "aria-haspopup") is None
    context.close()
