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

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
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
    page.wait_for_selector("#main h1:has-text('Top stories')")
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


def test_search_hide_and_a_paid_newsletter_a_reader_pays_for(browser, env):
    """Search and follow, hide a source, then start a paid newsletter, pick stories, publish, and let a visitor on
    a phone subscribe, pay and open every story with the code the writer confirms."""
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    context.route(
        "https://img.example/**",
        lambda route: route.fulfill(status=404, body=b"", content_type="image/png"),
    )  # pictures that do not load must leave no empty box
    go(page, env, "news")
    page.wait_for_selector("article:has-text('Monsoon rains bring relief to farmers')")
    page.wait_for_timeout(300)
    assert page.locator("article img").count() == 0
    # Search, and follow what was searched.
    go(page, env, "news?tab=search")
    page.fill("#main input[type=search]", "solar power")
    page.keyboard.press("Enter")
    page.wait_for_selector("article:has-text('solar power makes the news today')")
    page.click("#main button:has-text('Follow this')")
    page.wait_for_selector("text=You follow this")
    # Hide a source; it is listed under settings and can be shown again.
    go(page, env, "news")
    page.click(
        "article:has-text('New metro line opens') button:has-text('Hide City News')"
    )
    page.wait_for_selector("article:has-text('New metro line opens')", state="detached")
    go(page, env, "news?tab=settings")
    page.click("li:has-text('City News') button:has-text('Show again')")
    page.wait_for_selector("text=You have not hidden any sources.")
    # Start a paid newsletter and put two stories in it, one with a note.
    go(page, env, "news?tab=newsletter")
    page.click("#main button:has-text('Start my newsletter')")
    page.fill(".modal input[name=name]", "Hyderabad This Week")
    page.fill(".modal input[name=price]", "49")
    page.click(".modal button:has-text('Start')")
    page.wait_for_selector("text=No stories yet")
    # A price that cannot be read is an error in the form, never a free newsletter.
    page.click("#main button:has-text('Change name or price')")
    page.fill(".modal input[name=price]", "Rs fifty")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".modal .form-error:has-text('is not a price')")
    page.fill(".modal input[name=price]", "")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(
        ".modal .form-error:has-text('0 to make the newsletter free')"
    )
    page.click(".modal button:has-text('Cancel')")
    page.wait_for_selector("#main p:has-text('a month')")
    go(page, env, "news")
    first = page.locator("article").nth(0)
    first.locator("button:has-text('Add to my newsletter')").click()
    page.fill(".modal textarea[name=note]", "Good news for farmers near us.")
    page.click(".modal button:has-text('Add to the next issue')")
    page.wait_for_selector(".modal", state="detached")
    page.locator("article").nth(1).locator(
        "button:has-text('Add to my newsletter')"
    ).click()
    page.click(".modal button:has-text('Add to the next issue')")
    page.wait_for_selector(".modal", state="detached")
    go(page, env, "news?tab=newsletter")
    page.wait_for_selector("li:has-text('Good news for farmers near us.')")
    page.click("#main button:has-text('Create link')")
    page.wait_for_selector("#main .suite-share-url")
    link = page.locator("#main .suite-share-url").inner_text()
    page.click("#main button:has-text('Publish this issue')")
    page.click(".modal button:has-text('Publish')")
    page.wait_for_selector("li:has-text('1. Hyderabad This Week, issue 1')")
    # A visitor on a phone sees a preview, subscribes and gets a reference to pay with.
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    guest = visitor.new_page()
    guest.goto(link)
    guest.wait_for_selector("h1:has-text('Hyderabad This Week')")
    guest.wait_for_selector("text=1 more story for subscribers.")
    guest.fill("input[autocomplete=name]", "Lakshmi")
    guest.locator(".pub-form input[autocomplete=email]").first.fill(
        "lakshmi@example.com"
    )
    guest.click("button:has-text('Subscribe')")
    guest.wait_for_selector("text=Thank you! Pay")
    assert not guest.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    # The writer sees the payment arrive and confirms it: the code opens every story.
    page.reload()
    page.wait_for_selector("#main button:has-text('Payment received')")
    page.click("#main button:has-text('Payment received')")
    page.wait_for_selector(".modal:has-text('Payment recorded')")
    code = page.evaluate("document.querySelector('.modal p strong').textContent")
    page.click(".modal button:has-text('Done')")
    page.wait_for_selector("text=Another month paid")
    # A wrong code: the message is under the form, and the next success replaces it.
    guest.fill("input[placeholder='ABCDE-FGHJK']", "AAAAA-AAAAA")
    guest.click("button:has-text('Open every story')")
    guest.wait_for_selector(".pub-card .pub-notice.bad")
    guest.fill("input[placeholder='ABCDE-FGHJK']", code)
    guest.click("button:has-text('Open every story')")
    guest.wait_for_selector("text=Good news for farmers near us.")
    guest.wait_for_selector("text=Every story is open")
    assert guest.locator(".pub-notice.bad").count() == 0
    visitor.close()
    # The newsletter tab fits a phone.
    page.set_viewport_size({"width": 390, "height": 844})
    go(page, env, "news?tab=newsletter")
    page.wait_for_selector("text=Readers")
    assert not page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    # The pictures answer 404 on purpose (the browser logs each one).
    left = [p for p in real_problems(problems) if "status of 404" not in p]
    assert not left, problems
    context.close()
