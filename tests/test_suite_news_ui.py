"""News in a real browser against the single-user server: headlines render, an article is saved, a topic is opened,
the language is switched to Telugu, a keyword is followed, the briefing is read aloud, and easy mode with
extra-large text fits a phone. Feeds come from tests/news_stub.py (no network)."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from news_stub import NewsStub  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page  # noqa: E402, F401

from aira import suite  # noqa: E402
from aira.suite import kit  # noqa: E402
from aira.suite.news import feeds  # noqa: E402


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def news(monkeypatch):
    kit.forget_schema()
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    server = NewsStub().start()
    monkeypatch.setenv(feeds.GOOGLE_BASE_ENV, server.base_url)
    monkeypatch.setenv(feeds.ALLOW_LOCAL_ENV, "1")
    yield server
    server.stop()
    suite.set_gateway(previous)


def real_problems(problems):
    return [p for p in problems if "401" not in p]


def test_news_headlines_saving_topics_language_and_phone(browser, env):
    context, page, problems = signed_in_page(browser, env)
    # The stub's article images live at img.example: answer them with a 1x1 PNG.
    pixel = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
    context.route(
        "https://img.example/**",
        lambda route: route.fulfill(status=200, body=pixel, content_type="image/png"),
    )
    # Speech is recorded on the page (an attribute: the test reads it from its own script world), not played.
    page.add_init_script(
        "if (window.speechSynthesis) { window.speechSynthesis.speak = (u) => document.documentElement.setAttribute('data-spoken', u.text); }"
    )
    go(page, env, "news")
    page.wait_for_selector("#main h1:has-text('News')")
    page.wait_for_selector("article:has-text('Monsoon rains bring relief to farmers')")
    # an image only when it is https; the article text has no HTML
    assert page.locator("article img").count() >= 1
    assert "<" not in page.locator("article").first.inner_text()
    # save the first article, then find it under Saved
    first = page.locator("article").first
    title = first.locator("h2").inner_text()
    first.locator("button:has-text('Save')").click()
    page.wait_for_selector("article >> nth=0 >> button:has-text('Saved')")
    go(page, env, "news?tab=saved")
    page.wait_for_selector(f"article:has-text('{title}')")
    # a topic
    go(page, env, "news?tab=topics")
    page.click("#main button[role=tab]:has-text('Technology')")
    page.wait_for_selector("article:has-text('New phone has a week-long battery')")
    # Listen to today's headlines (speech is captured, not played)
    page.click('#main button:has-text("Listen to today\'s headlines")')
    page.wait_for_selector("html[data-spoken]", state="attached")
    assert "1. " in page.get_attribute(
        "html", "data-spoken"
    )  # the briefing lists the headlines
    # switch the news to Telugu
    go(page, env, "news?tab=settings")
    page.wait_for_selector("text=Language and country")
    page.select_option("select[aria-label='News language']", "te")
    page.click("#main button:has-text('Save settings')")
    page.wait_for_selector(
        "text=Saved. Your news is now in the language and topics you chose."
    )
    go(page, env, "news")
    page.wait_for_selector("article:has-text('రైతులకు వర్షాలతో ఊరట')")
    # follow a word
    go(page, env, "news?tab=settings")
    page.click("#main button:has-text('Follow a word or topic')")
    page.fill(".modal input[name=query]", "metro")
    page.click(".modal button:has-text('Follow')")
    page.wait_for_selector("li:has-text('metro')")
    go(page, env, "news?tab=foryou")
    page.wait_for_selector("article:has-text('metro makes the news today')")
    # easy mode, extra-large text, phone width: nothing spills sideways
    context.request.post(
        env.base + "/api/life/prefs",
        data=json.dumps({"easy_mode": True, "text_size": "xlarge"}),
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    go(page, env, "news")
    page.wait_for_selector("article")
    page.wait_for_timeout(500)
    assert not page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    assert not real_problems(problems), problems
    context.close()
