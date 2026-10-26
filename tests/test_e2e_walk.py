"""The whole application in a real browser: every page of every app, for every role, in both builds.

Each person signs in, opens every app's home and collects the sections its sidebar offers, then visits each one and
records what went wrong: a script error, a console error, a failed request, or a page that shows an error box.
The hosted organization has every app turned on, so nothing is hidden behind the closed-app page; the platform
console is walked by a platform admin. A page that is closed to a role on purpose (the closed-app page, "Not
available") is fine; a page that breaks is not."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from cloud_support import build_org, sign_in  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_cloud_apps import served  # noqa: E402, F401
from test_cloud_ui_billing import operator_browser  # noqa: E402
from test_leads_ui import browser  # noqa: E402, F401

from aira.apps import APPS  # noqa: E402

# Every app's home section, so each app's sidebar gets read.
HOMES = [app.home for app in APPS]
# Sections a sidebar does not list but people reach from elsewhere.
EXTRA = ["today", "display", "hub", "settings"]
STRICT = os.environ.get("AIRA_E2E_REPORT") != "1"


def _watch(page) -> list[str]:
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: (
            problems.append(f"console.{message.type}: {message.text}")
            if message.type == "error" and "Failed to load resource" not in message.text
            else None
        ),
    )
    page.on(
        "response",
        lambda response: (
            problems.append(
                f"{response.status} {response.request.method} {response.url}"
            )
            if response.status >= 400
            else None
        ),
    )
    return problems


def _sidebar(page) -> list[str]:
    return page.eval_on_selector_all(
        "#primaryNav a[data-section]",
        "links => links.map((link) => link.getAttribute('href') || '')",
    )


def _state(page) -> str:
    if page.locator(".error-box").count():
        return "error box: " + page.locator(".error-box").first.inner_text()[
            :160
        ].replace("\n", " ")
    if page.locator(".app-locked").count():
        return "closed:" + (
            page.locator(".app-locked").first.get_attribute("data-kind") or ""
        )
    return "ok"


def _walk(
    page, base: str, problems: list[str], *, expected=lambda url, line: False
) -> dict[str, list[str]]:
    """Visit every app home to learn the sidebars, then every section found; returns page -> problems."""
    hrefs: list[str] = []
    for section in [*HOMES, *EXTRA]:
        page.goto(f"{base}/#/{section}")
        page.wait_for_load_state("networkidle")
        for href in [f"#/{section}", *_sidebar(page)]:
            if href.startswith("#/") and href not in hrefs:
                hrefs.append(href)
    found: dict[str, list[str]] = {}
    for href in hrefs:
        problems.clear()
        page.goto(f"{base}/{href}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        state = _state(page)
        bad = [line for line in problems if not expected(href, line)]
        if state.startswith("error box"):
            bad.append(state)
        print(f"  {href:<28} {state:<14} {' | '.join(bad)[:400]}")
        if bad:
            found[href] = bad
    return found


def _report(found: dict[str, dict[str, list[str]]]) -> None:
    broken = {who: pages for who, pages in found.items() if pages}
    print(json.dumps(broken, indent=1)[:6000])
    if STRICT:
        assert not broken, broken


def test_every_page_of_the_hosted_build_for_every_role(served, browser):
    app, base = served
    org = build_org(app, "Every App Inc", plan="team", seats=10, apps="all")
    found = {}
    for role in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        context = browser.new_context(viewport={"width": 1280, "height": 860})
        jar = sign_in(app, getattr(org, role))
        context.add_cookies(
            [{"name": k, "value": v, "url": base} for k, v in jar.cookies.items()]
        )
        page = context.new_page()
        problems = _watch(page)
        print(f"### {role}")
        found[role] = _walk(page, base, problems)
        context.close()
    _report(found)


def test_every_page_of_the_platform_console(served, browser):
    app, base = served
    build_org(app, "Every App Inc", plan="team", seats=10, apps="all")
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    jar = operator_browser(app)
    context.add_cookies(
        [{"name": k, "value": v, "url": base} for k, v in jar.cookies.items()]
    )
    page = context.new_page()
    problems = _watch(page)
    page.goto(f"{base}/#/platform/tenants")
    page.wait_for_load_state("networkidle")
    found = {}
    for href in _sidebar(page):
        problems.clear()
        page.goto(f"{base}/{href}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        # the member boot asks /api/auth/me once and is told 401 while only the console is signed in
        bad = [line for line in problems if "/api/auth/me" not in line]
        state = _state(page)
        if state.startswith("error box"):
            bad.append(state)
        print(f"  {href:<28} {state:<14} {' | '.join(bad)[:400]}")
        if bad:
            found[href] = bad
    context.close()
    _report({"operator": found})


def test_every_page_of_the_local_build(env, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    registered = context.request.post(
        env.base + "/api/auth/register",
        data=json.dumps({"email": "walk@example.com", "password": PASSWORD}),
        headers=headers,
    )
    assert registered.status == 201, registered.text()
    page = context.new_page()
    problems = _watch(page)
    print("### local owner")
    found = _walk(page, env.base, problems)
    context.close()
    _report({"local": found})


def test_a_hosted_member_uploads_a_video_and_another_organization_watches_it(
    served, browser
):
    """The hosted path of Videos in a real browser: the upload screen, signed media through the gateway (CSP
    media-src), and one catalog across organizations."""
    from test_suite_videos_ui import record_webm

    app, base = served
    maker = build_org(
        app,
        "Maker Studio",
        plan="team",
        seats=5,
        slug_email="maker.test",
        apps=["videos"],
    )
    viewer = build_org(
        app,
        "Viewer Club",
        plan="team",
        seats=5,
        slug_email="viewer.test",
        apps=["videos"],
    )

    def page_for(principal):
        context = browser.new_context(viewport={"width": 1280, "height": 860})
        jar = sign_in(app, principal)
        context.add_cookies(
            [{"name": k, "value": v, "url": base} for k, v in jar.cookies.items()]
        )
        page = context.new_page()
        return context, page, _watch(page)

    def plays(page):
        player = page.locator(".vid-player video")
        for _ in range(300):
            if player.count() and player.evaluate("v => v.readyState") >= 2:
                return player
            page.wait_for_timeout(100)
        raise AssertionError("the video did not load in the player")

    context, page, problems = page_for(maker.candidate)
    clip = record_webm(context, 320, 180, 3.0)
    page.goto(f"{base}/#/videos?tab=studio")
    page.wait_for_selector("text=Upload a video", timeout=30000)
    page.set_input_files(
        "input[aria-label='Choose a video']",
        files=[{"name": "harbour.webm", "mimeType": "video/webm", "buffer": clip}],
    )
    page.locator("p", has_text="harbour.webm").first.wait_for(timeout=20000)
    page.fill("input[name=title]", "Harbour at dusk")
    page.locator("select[name=kind]").select_option("video")
    page.locator("button:has-text('Upload')").last.click()
    page.wait_for_url("**watch=*", timeout=60000)
    page.wait_for_selector("h1:has-text('Harbour at dusk')")
    assert "/api/videos/media/" in plays(page).evaluate("v => v.currentSrc")
    assert not problems, problems
    context.close()

    context, page, problems = page_for(viewer.candidate)
    page.goto(f"{base}/#/videos")
    card = page.locator(".vid-card", has_text="Harbour at dusk")
    card.first.wait_for(timeout=30000)
    card.first.click()
    page.wait_for_selector("h1:has-text('Harbour at dusk')", timeout=30000)
    plays(page)
    assert not problems, problems
    context.close()
