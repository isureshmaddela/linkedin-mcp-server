"""Sites in a real browser against the local server and a stand-in model.

Describe a site on the Build page, watch the builder's steps and the code stream in, see the site run in the
sandboxed preview (its script and its stylesheet work), edit a file and save it, change the site by chatting, restore
an earlier version from History, publish it and open the public link, switch screen sizes, use the model picker and
templates, and use the workspace on a phone in dark mode with no sideways scroll. Fails on any script error.
Screenshots go to ``SITES_SHOTS`` (default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from sites_stubs import EDIT_ANSWER, SitesStub  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401

from aira.suite import kit  # noqa: E402

SHOTS = Path(os.environ["SITES_SHOTS"]) if os.environ.get("SITES_SHOTS") else None


def shot(page, name: str) -> None:
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"))


@pytest.fixture
def model():
    server = SitesStub().start()
    yield server
    server.stop()


def call(context, env, method, path, body=None):
    reply = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body or {}),
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert reply.ok, reply.text()
    return reply.json()


def app_problems(page, problems):
    """Script errors of Monosage itself. The site in the preview is the person's own page: what it logs (a file it
    asks for that a change removed) is not an error of the app."""
    sites: list[str] = []
    page.on(
        "console",
        lambda m: (
            sites.append(m.text)
            if m.type == "error" and "/api/public/" in (m.location or {}).get("url", "")
            else None
        ),
    )
    return lambda: [
        p for p in problems if p.replace("console.error: ", "") not in sites
    ]


def frame_text(page, selector):
    return page.frame_locator(".sb-frame").locator(selector).inner_text(timeout=20_000)


def test_describe_watch_preview_edit_change_restore_and_publish(browser, env, model):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env)
    errors = app_problems(page, problems)
    call(
        context,
        env,
        "POST",
        "/api/sites/providers/custom",
        {
            "base_url": f"{model.base}/v1",
            "model": "stub-model-a",
            "api_key": "sk-ui-key-4321",
            "default": True,
        },
    )
    model.delay, model.chunk = 0.03, 24
    page.goto(f"{env.base}/sites#/sites")
    page.wait_for_selector(".sb-hero")
    assert page.locator(".sb-hero-title").inner_text() == "What do you want to build?"
    assert page.locator(".sb-template").count() == 12
    art = page.locator(".sb-template-art").first.evaluate(
        "node => getComputedStyle(node).backgroundImage"
    )
    assert "gradient" in art, art  # the card's color comes from a CSS custom property
    assert (
        "Any OpenAI-compatible server"
        in page.locator(".sb-composer .sb-chip").first.inner_text()
    )
    shot(page, "01-build")

    page.locator(".sb-input").fill(
        "A bakery website called Stub Bakery with a menu and a contact form"
    )
    page.locator(".sb-send").click()
    page.wait_for_selector(".sb-ws")
    page.wait_for_selector(".sb-agent .sb-step")
    until(
        page,
        lambda: (
            page.locator(".sb-agent .sb-step-label").filter(has_text="Writing").count()
            > 0
            or page.locator(".sb-agent").count() == 0
        ),
        timeout=20,
        what="a file being written",
    )
    shot(page, "02-building")
    page.wait_for_selector(
        ".sb-msg.bot .sb-reply:has-text('Built Stub Bakery')", timeout=30_000
    )
    assert page.locator(".sb-agent").count() == 0
    assert (
        page.locator(".sb-msg.user .sb-bubble")
        .first.inner_text()
        .startswith("A bakery website")
    )
    assert sorted(page.locator(".sb-msg.bot .sb-file-chip").all_inner_texts()) == [
        "# styles.css",
        "ƒ app.js",
        "◇ index.html",
    ]
    assert "Version 1" in page.locator(".sb-status").inner_text()

    # The preview runs the site in its sandbox: the script ran and the stylesheet (a root link) loaded.
    page.locator(".sb-ptab:has-text('Preview')").click()
    assert frame_text(page, "#title") == "Stub Bakery"
    title = page.frame_locator(".sb-frame").locator("#title")
    until(
        page,
        lambda: title.get_attribute("data-ready") == "yes",
        timeout=15,
        what="the site's script to run",
    )
    assert title.evaluate("node => getComputedStyle(node).color") == "rgb(200, 80, 20)"
    assert not page.locator(".sb-panel-body").inner_text().strip().startswith("null")
    sandboxed = page.locator(".sb-frame").get_attribute("sandbox")
    assert "allow-scripts" in sandboxed and "allow-same-origin" not in sandboxed
    shot(page, "03-preview")

    # Screen sizes.
    page.locator(".sb-devices button[aria-label='Phone']").click()
    assert page.locator(".sb-stage.phone").count() == 1
    page.locator(".sb-devices button[aria-label='Desktop']").click()

    # Code: edit a file, save with Ctrl+S, see it in the preview.
    page.locator(".sb-ptab:has-text('Code')").click()
    names = page.locator(".sb-tree-item .sb-tree-name").all_inner_texts()
    assert names == ["index.html", "app.js", "styles.css"]
    page.locator(".sb-tree-item:has-text('styles.css')").click()
    editor = page.locator(".sb-code-area")
    assert "rgb(200, 80, 20)" in editor.input_value()
    editor.fill("h1 { color: rgb(10, 20, 200); }\n")
    assert page.locator(".sb-editor-head .sb-unsaved").is_visible()
    editor.press("Control+s")
    page.wait_for_selector(".toast:has-text('Saved styles.css')")
    assert "Version 2" in page.locator(".sb-status").inner_text()
    page.locator(".sb-ptab:has-text('Preview')").click()
    title = page.frame_locator(".sb-frame").locator("#title")
    until(
        page,
        lambda: (
            title.evaluate("node => getComputedStyle(node).color") == "rgb(10, 20, 200)"
        ),
        timeout=15,
        what="the saved style",
    )
    shot(page, "04-code")

    # Change it by chatting: a new page, a deleted file.
    model.answer, model.delay = EDIT_ANSWER, 0
    page.locator(".sb-chat .sb-input").fill("Green heading and an about page")
    page.locator(".sb-chat .sb-input").press("Enter")
    page.wait_for_selector(
        ".sb-msg.bot .sb-reply:has-text('About page')", timeout=30_000
    )
    page.locator(".sb-ptab:has-text('Preview')").click()
    pages = page.locator(".sb-tools select").locator("option").all_inner_texts()
    assert pages == ["about.html", "index.html"]
    page.locator(".sb-tools select").select_option("about.html")
    assert frame_text(page, "h1") == "About us"

    # History: restore the first version.
    page.locator(".sb-ptab:has-text('History')").click()
    assert page.locator(".sb-version").count() == 3
    shot(page, "05-history")
    page.locator(
        ".sb-version:has(.sb-vnum:text-is('v1')) button:has-text('Restore')"
    ).click()
    page.locator(".modal button:has-text('Restore')").click()
    page.wait_for_selector(".toast:has-text('This is version 4')")
    assert page.locator(".sb-version").count() == 4

    # Publish and open the public link in a new tab.
    page.locator(".sb-ws-head button:has-text('Publish')").click()
    page.locator(".sb-deploy-card button:has-text('Publish')").first.click()
    page.wait_for_selector(".sb-live-box .sb-url")
    url = page.locator(".sb-live-box .sb-url").inner_text()
    assert "/api/public/" in url and url.endswith("/w/")
    assert page.locator(".sb-live-link").inner_text().startswith("● Live")
    assert page.locator(".sb-cloud").count() >= 10
    shot(page, "06-deploy")
    visitor = context.new_page()
    visitor.goto(url)
    assert visitor.locator("#title").inner_text() == "Stub Bakery"
    visitor.close()

    # Back to Build: the site is listed with a live thumbnail.
    page.locator(".sb-back").click()
    page.wait_for_selector(".sb-card")
    assert page.locator(".sb-card strong").first.inner_text() == "Stub Bakery"
    assert page.locator(".sb-card .sb-live").count() == 1
    assert errors() == [], (errors(), problems)
    context.close()


def test_model_picker_templates_and_the_phone_layout(browser, env, model):
    kit.forget_schema()
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    page.goto(f"{env.base}/sites#/sites")
    page.wait_for_selector(".sb-hero")
    chip = page.locator(".sb-composer .sb-picker .sb-chip")
    assert "Monosage designer" in chip.inner_text()
    chip.click()
    page.wait_for_selector(".sb-pop")
    groups = page.locator(".sb-pop-title").all_inner_texts()
    assert [g.lower() for g in groups] == ["on this computer", "in the cloud"]
    labels = page.locator(".sb-pop-name").all_inner_texts()
    assert {
        "Monosage designer",
        "Ollama (on this computer)",
        "Claude (Anthropic)",
        "Gemini (Google)",
        "OpenAI",
    } <= set(labels)
    # Claude needs a key: choosing it opens its set-up form.
    page.locator(".sb-pop-row:has-text('Claude')").click()
    page.wait_for_selector(".modal:has-text('Claude (Anthropic)')")
    assert page.locator(".modal input[type='password']").count() == 1
    page.locator(".modal button:has-text('Cancel')").click()
    hero = page.locator(".sb-hero")
    assert hero.evaluate("node => node.scrollLeft") == 0
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    chip.click()
    box = page.locator(".sb-pop").bounding_box()
    assert (
        box["x"] >= 0
        and box["x"] + box["width"] <= 390
        and box["y"] + box["height"] <= 844 - 60
    ), box  # a sheet above the bottom bar
    page.keyboard.press("Escape")
    shot(page, "07-phone-build")

    page.locator(".sb-template:has-text('Restaurant')").click()
    page.wait_for_selector(".sb-ws")
    assert page.locator(".sb-switch").is_visible()
    page.wait_for_selector(".sb-msg.bot .sb-reply:has-text('template')")
    page.locator(".sb-switch button:has-text('Site')").click()
    assert frame_text(page, ".logo").strip().endswith("Bella Cucina")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    shot(page, "08-phone-site")
    page.locator(".sb-switch button:has-text('Chat')").click()
    page.locator(".sb-chat .sb-input").fill("use teal and add a FAQ")
    page.locator(".sb-chat .sb-input").press("Enter")
    page.wait_for_selector(
        ".sb-msg.bot .sb-reply:has-text('Added a faq section')", timeout=20_000
    )
    texts = page.locator(".sb-messages .sb-msg").all_inner_texts()
    assert "use teal and add a FAQ" in texts[-2] and "Added a faq section" in texts[-1]
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    shot(page, "09-phone-chat")
    assert problems == [], problems
    context.close()


def test_the_frame_new_and_search_and_models_page(browser, env, model):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env)
    call(context, env, "POST", "/api/sites/projects", {"template": "portfolio"})
    page.goto(f"{env.base}/sites#/sites?tab=models")
    page.wait_for_selector(".sb-model")
    assert page.locator(".sb-model").count() == 8
    assert page.locator(".sb-model.default strong").inner_text() == "Monosage designer"
    page.locator(".sb-model:has-text('Ollama') button:has-text('Set up')").click()
    page.locator(".modal input[name='base_url']").fill(model.base)
    page.locator(".modal input[name='model']").fill("stub-coder:7b")
    page.locator(".modal input[name='default']").check()
    page.locator(".modal button:has-text('Save and test')").click()
    page.wait_for_selector(".toast:has-text('Ready to build')")
    page.wait_for_selector(".sb-model.default:has-text('Ollama')")
    shot(page, "10-models")

    box = page.locator("#appSearchInput")
    box.fill("Ava")
    page.wait_for_selector(".shell-search-option:has-text('Ava Chen')")
    page.locator(".shell-search-option:has-text('Ava Chen')").click()
    page.wait_for_selector(".sb-ws")
    assert page.locator(".sb-ws-name").inner_text() == "Ava Chen"
    assert problems == [], problems
    context.close()
