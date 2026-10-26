"""Family in a real browser against the local server: two people on two devices chat live, the feed updates,
an event is added, and a relative writes on the public board which an adult approves. Every tab renders without
page errors, and the chat fits a phone in easy mode with extra large text."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def headers(env, profile=""):
    out = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    if profile:
        out["X-Aira-Profile"] = profile
    return out


def watch(page, problems):
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))


def test_two_people_chat_live_and_a_relative_writes_on_the_board(browser, env):
    pat_context, pat, problems = signed_in_page(browser, env)
    owner = pat_context.request.get(
        env.base + "/api/life/me", headers=headers(env)
    ).json()["profile"]
    added = pat_context.request.post(
        env.base + "/api/life/profiles",
        data=json.dumps({"name": "Ana", "age_group": "adult"}),
        headers=headers(env, owner["id"]),
    )
    assert added.status == 201, added.text()
    pat.goto(f"{env.base}/#/family?tab=chat")
    # Pat's browser was told who it is before Ana existed? If not, the picker asks: Pat taps their name.
    pat.wait_for_selector(".life-picker, [aria-label^='Messages in']", timeout=15000)
    if pat.locator(".life-picker").count():
        pat.click(f".life-face:has-text('{owner['name']}')")
    pat.wait_for_selector("[aria-label^='Messages in']", timeout=15000)

    # Ana signs in on another device and picks herself.
    ana_context = browser.new_context(viewport={"width": 390, "height": 844})
    ana = ana_context.new_page()
    watch(ana, problems)
    login = ana_context.request.post(
        env.base + "/api/auth/login",
        data=json.dumps({"email": "leads-ui@example.com", "password": PASSWORD}),
        headers=headers(env),
    )
    assert login.status == 200, login.text()
    ana.goto(f"{env.base}/#/family?tab=chat")
    ana.wait_for_selector(".life-picker", timeout=15000)
    ana.click(".life-face:has-text('Ana')")
    ana.wait_for_selector("[aria-label^='Messages in']", timeout=15000)

    # Pat writes; Ana sees it without reloading (live), answers; Pat sees the answer.
    pat.fill("textarea[aria-label='Your message']", "Dinner at seven?")
    pat.click("button:has-text('Send')")
    until(
        ana,
        lambda: ana.locator("text=Dinner at seven?").count() > 0,
        timeout=12,
        what="the message to reach Ana",
    )
    ana.fill("textarea[aria-label='Your message']", "Yes please!")
    ana.keyboard.press("Enter")
    until(
        pat,
        lambda: pat.locator("text=Yes please!").count() > 0,
        timeout=12,
        what="the reply to reach Pat",
    )
    until(
        pat,
        lambda: pat.locator("text=Seen by Ana").count() > 0,
        timeout=12,
        what="the read receipt",
    )

    # The chat fits a phone in easy mode with extra large text.
    ana.evaluate("document.body.classList.add('easy', 'text-xlarge')")
    ana.wait_for_timeout(300)
    assert ana.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")

    # A post on the feed reaches Ana live.
    pat.goto(f"{env.base}/#/family?tab=feed")
    pat.wait_for_selector("textarea[aria-label='Your post']")
    pat.fill("textarea[aria-label='Your post']", "Look at the garden!")
    pat.click("button:has-text('Post')")
    pat.wait_for_selector("article:has-text('Look at the garden!')")
    ana.goto(f"{env.base}/#/family?tab=feed")
    ana.wait_for_selector("article:has-text('Look at the garden!')", timeout=15000)
    ana.click("article:has-text('Look at the garden!') button[aria-label^='love']")
    until(
        ana,
        lambda: (
            ana.locator(
                "article:has-text('Look at the garden!') button[aria-pressed='true']"
            ).count()
            == 1
        ),
        what="the reaction",
    )

    # Pat adds an event; Ana says she is going.
    pat.goto(f"{env.base}/#/family?tab=calendar")
    pat.click("button:has-text('Add an event')")
    pat.fill(".modal input[name=title]", "Sunday lunch")
    pat.fill(".modal input[name=starts_at]", "2030-06-02T13:00")
    pat.fill(".modal input[name=place]", "Grandma's")
    pat.click(".modal button:has-text('Save')")
    pat.wait_for_selector("article:has-text('Sunday lunch')")
    ana.goto(f"{env.base}/#/family?tab=calendar")
    ana.wait_for_selector("article:has-text('Sunday lunch')")
    ana.click("article:has-text('Sunday lunch') button:has-text('Going')")

    def going() -> str:
        text = ana.locator("article:has-text('Sunday lunch')").inner_text()
        return next(
            (line for line in text.splitlines() if line.startswith("Going:")), ""
        )

    until(
        ana, lambda: owner["name"] in going() and "Ana" in going(), what="Ana's answer"
    )

    # The board: Pat makes the link; a relative writes; Pat approves; the relative sees it.
    pat.goto(f"{env.base}/#/family?tab=board")
    pat.click("button:has-text('Create link')")
    pat.wait_for_selector(".suite-share-url")
    board_url = pat.locator(".suite-share-url").inner_text()
    relative_context = browser.new_context(viewport={"width": 390, "height": 844})
    relative = relative_context.new_page()
    watch(relative, problems)
    relative.goto(board_url)
    relative.wait_for_selector("text=Look at the garden!")
    relative.fill("input[autocomplete=name]", "Aunt May")
    relative.fill("textarea", "Lovely garden!")
    relative.click("button:has-text('Send to the family')")
    relative.wait_for_selector(".pub-notice.good")
    pat.reload()  # the same address: reload to fetch what is waiting
    pat.wait_for_selector("text=Aunt May")
    pat.click("button:has-text('Approve')")
    pat.wait_for_selector("text=Nothing waiting.")
    relative.reload()
    relative.wait_for_selector("text=Lovely garden!")
    assert relative.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 2"
    )

    # Every tab renders.
    for tab in ("feed", "chat", "calendar", "dates", "board"):
        pat.goto(f"{env.base}/#/family?tab={tab}")
        pat.wait_for_selector("#main h1:has-text('Family')")
        pat.wait_for_timeout(300)
        assert pat.locator("#main .error-box").count() == 0, tab

    pageerrors = [p for p in problems if p.startswith("pageerror")]
    assert not pageerrors, pageerrors
