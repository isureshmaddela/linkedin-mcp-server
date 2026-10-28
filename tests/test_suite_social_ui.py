"""Social in a real browser against the local server: the screen bugs found by using it stay fixed (no "null" on
group and profile pages, a half-typed comment survives the live feed, the chat sits beside the list on a wide
screen, reading older messages is not interrupted, leaving a chat stops its polling, no "Show older posts" on an
empty feed) and the Marketplace works at phone width."""

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


NULL_TEXT = "() => { const w = document.createTreeWalker(document.getElementById('main'), NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) { if (['null', 'undefined'].includes(n.textContent.trim())) return true; } return false; }"


def test_social_screens_stay_fixed_and_the_marketplace_fits_a_phone(browser, env):
    context, pat, problems = signed_in_page(browser, env)
    request = context.request
    owner = request.get(env.base + "/api/life/me", headers=headers(env)).json()[
        "profile"
    ]
    ana = request.post(
        env.base + "/api/life/profiles",
        data=json.dumps({"name": "Ana", "age_group": "adult"}),
        headers=headers(env, owner["id"]),
    ).json()["profile"]
    ana_header = request.post(
        env.base + "/api/life/unlock",
        data=json.dumps({"profile_id": ana["id"], "pin": ""}),
        headers=headers(env, owner["id"]),
    ).json()["header"]
    me = request.post(
        env.base + "/api/social/me",
        data=json.dumps({"handle": "pat"}),
        headers=headers(env, owner["id"]),
    ).json()["account"]
    friend = request.post(
        env.base + "/api/social/me",
        data=json.dumps({"handle": "ana"}),
        headers=headers(env, ana_header),
    ).json()["account"]
    request.post(
        env.base + f"/api/social/friends/{friend['id']}",
        data="{}",
        headers=headers(env, owner["id"]),
    )
    request.post(
        env.base + f"/api/social/friends/{me['id']}",
        data="{}",
        headers=headers(env, ana_header),
    )

    def as_ana(method, path, body=None):
        response = request.fetch(
            env.base + path,
            method=method,
            data=json.dumps(body or {}),
            headers=headers(env, ana_header),
        )
        assert response.status < 300, response.text()
        return response.json()

    pat.goto(f"{env.base}/#/social?tab=feed")
    pat.wait_for_selector(
        ".life-picker, textarea[aria-label='Your post']", timeout=15000
    )
    if pat.locator(".life-picker").count():
        pat.click(f".life-face:has-text('{owner['name']}')")
    pat.wait_for_selector("textarea[aria-label='Your post']")
    assert not pat.locator("button:has-text('Show older posts')").is_visible()

    # a half-typed comment survives the live feed bringing a new reaction to the same post
    post = as_ana("POST", "/api/social/posts", {"text": "Sunny garden day"})["post"]
    pat.reload()
    pat.wait_for_selector("article:has-text('Sunny garden day')")
    pat.click("article:has-text('Sunny garden day') button:has-text('Comment')")
    pat.click("input[aria-label^='Comment on']")
    pat.keyboard.type("Half typed")
    as_ana("POST", f"/api/social/posts/{post['id']}/react", {"emoji": "🎉"})
    until(
        pat,
        lambda: pat.locator("article button[aria-label='celebrate (1)']").count() == 1,
        timeout=15,
        what="the live reaction",
    )
    assert pat.locator("input[aria-label^='Comment on']").input_value() == "Half typed"
    assert pat.evaluate(
        "() => document.activeElement && document.activeElement.hasAttribute('data-comment-for')"
    )

    # a wide screen shows the chat beside the list; reading older messages is not interrupted
    chat = as_ana("POST", "/api/social/chats", {"person_id": me["id"]})["chat"]
    for n in range(30):
        as_ana(
            "POST", f"/api/social/chats/{chat['id']}/messages", {"text": f"Line {n}"}
        )
    pat.goto(f"{env.base}/#/social?tab=chats&chat={chat['id']}")
    pat.wait_for_selector("[role=log] >> text=Line 29")
    side, thread = (
        pat.locator("nav[aria-label=Chats]").bounding_box(),
        pat.locator("[role=log]").bounding_box(),
    )
    assert thread["x"] >= side["x"] + side["width"] - 5
    assert pat.locator("[role=log] >> text=Line 29").bounding_box()["height"] < 40
    pat.evaluate("() => { document.querySelector('[role=log]').scrollTop = 0; }")
    as_ana("POST", f"/api/social/chats/{chat['id']}/messages", {"text": "One more"})
    until(
        pat,
        lambda: pat.locator("[role=log] >> text=One more").count() == 1,
        timeout=15,
        what="the new message",
    )
    assert pat.evaluate("() => document.querySelector('[role=log]').scrollTop") < 50

    # leaving the chat for another tab stops its polling
    calls: list[str] = []
    pat.on("request", lambda r: calls.append(r.url) if "/messages" in r.url else None)
    pat.goto(f"{env.base}/#/social?tab=groups")
    pat.wait_for_timeout(5000)
    assert calls == []

    # no "null" on a group page or the profile
    group = request.post(
        env.base + "/api/social/groups",
        data=json.dumps({"name": "Garden club"}),
        headers=headers(env, owner["id"]),
    ).json()["group"]
    pat.goto(f"{env.base}/#/social?tab=groups&group={group['id']}")
    pat.wait_for_selector("button:has-text('Change group')")
    assert not pat.evaluate(NULL_TEXT)
    pat.goto(f"{env.base}/#/social?tab=profile")
    pat.wait_for_selector("button:has-text('Download my data')")
    assert not pat.evaluate(NULL_TEXT)

    # the Marketplace at phone width, in easy mode with extra large text
    request.post(
        env.base + "/api/social/market",
        data=json.dumps(
            {
                "title": "Child's bike, 16 inch, red with stabilisers",
                "price": "45",
                "place": "Riverside",
            }
        ),
        headers=headers(env, owner["id"]),
    )
    phone = browser.new_context(viewport={"width": 390, "height": 844})
    phone.request.post(
        env.base + "/api/auth/login",
        data=json.dumps({"email": "leads-ui@example.com", "password": PASSWORD}),
        headers=headers(env),
    )
    page = phone.new_page()
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    page.goto(f"{env.base}/#/social?tab=market")
    page.wait_for_selector(".life-picker", timeout=15000)
    page.click(".life-face:has-text('Ana')")
    page.wait_for_selector(".so-listing")
    page.click(".so-listing")
    page.wait_for_selector("button:has-text('Message the seller')")
    page.evaluate("() => document.body.classList.add('easy', 'text-xlarge')")
    page.wait_for_timeout(300)
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.click("button:has-text('Message the seller')")
    page.click(".modal button:has-text('Send')")
    page.wait_for_selector("text=See the listing")
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )

    pageerrors = [p for p in problems if p.startswith("pageerror")]
    assert not pageerrors, pageerrors
