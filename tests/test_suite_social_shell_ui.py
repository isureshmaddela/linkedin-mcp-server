"""Social in the Aira frame, in a real browser: "+ New" opens the app's create forms (a post, a story, a chat, a
Marketplace listing, a family event), the search box across the top finds people, chats, groups, pages, listings and
the family (and Enter lists them), the tabs of Social and Family are folders in the left menu, and the pages fit a
phone in light and dark."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
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


def seeded(browser, env, **size):
    """Pat (the owner) and Ana (a second adult in the household) are friends on Social, with a chat, a group, a page,
    a listing and a family event."""
    context, page, problems = signed_in_page(browser, env, **size)
    # name the address of a refused request (the console only says "403")
    page.on(
        "response",
        lambda r: problems.append(f"{r.status} {r.url}") if r.status >= 400 else None,
    )
    request = context.request

    def call(path, body, profile):
        response = request.post(
            env.base + path, data=json.dumps(body), headers=headers(env, profile)
        )
        assert response.status < 300, response.text()
        return response.json()

    owner = request.get(env.base + "/api/life/me", headers=headers(env)).json()[
        "profile"
    ]
    ana = call(
        "/api/life/profiles", {"name": "Ana", "age_group": "adult"}, owner["id"]
    )["profile"]
    ana_header = call(
        "/api/life/unlock", {"profile_id": ana["id"], "pin": ""}, owner["id"]
    )["header"]
    me = call(
        "/api/social/me", {"handle": "pat", "display_name": "Pat Rivera"}, owner["id"]
    )["account"]
    friend = call(
        "/api/social/me", {"handle": "ana", "display_name": "Ana Silva"}, ana_header
    )["account"]
    call(f"/api/social/friends/{friend['id']}", {}, owner["id"])
    call(f"/api/social/friends/{me['id']}", {}, ana_header)
    chat = call("/api/social/chats", {"person_id": me["id"]}, ana_header)["chat"]
    call(
        f"/api/social/chats/{chat['id']}/messages",
        {"text": "Lunch on Sunday?"},
        ana_header,
    )
    call(f"/api/social/chats/{chat['id']}/read", {}, owner["id"])
    group = call(
        "/api/social/groups",
        {"name": "Garden club", "visibility": "public"},
        owner["id"],
    )["group"]
    listing = call(
        "/api/social/market",
        {"title": "Garden bench", "price": "45", "place": "Riverside"},
        ana_header,
    )["listing"]
    event = call(
        "/api/family/events",
        {"title": "Sunday lunch at Grandma's", "starts_at": "2026-11-01T12:00:00Z"},
        owner["id"],
    )["event"]
    return (
        context,
        page,
        problems,
        {
            "owner": owner,
            "friend": friend,
            "chat": chat,
            "group": group,
            "listing": listing,
            "event": event,
        },
    )


def open_app(page, env, owner, problems, hash_="social"):
    page.goto(f"{env.base}/social#/{hash_}")
    page.wait_for_selector(".life-picker, #main .page-head h1", timeout=20000)
    if (
        page.locator(".life-picker").count()
        and page.locator(".life-picker").is_visible()
    ):
        page.click(f".life-face:has-text('{owner['name']}')")
        # A household with two people asks who is using Aira first: GET /api/life/me answers 403 until then.
        refused = [
            p for p in problems if p.startswith("403 ") and p.endswith("/api/life/me")
        ]
        for line in refused:
            problems.remove(line)
            console = [
                p for p in problems if p.startswith("console.error") and "403" in p
            ]
            if console:
                problems.remove(console[0])
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_load_state("networkidle")


def menu_items(page):
    return [
        item.strip()
        for item in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ]


def search_and_enter(page, box, words):
    """Type, let the suggestions arrive, then press Enter (an Enter inside the shell's 160 ms typing pause would let
    the late suggestions open over the results page)."""
    box.fill(words)
    page.wait_for_selector(
        "#appSearchList [role='option']:has-text('Search Social for')"
    )
    box.press("Enter")


def pick_new(page, label):
    page.click(".shell-new")
    page.locator(".shell-new-menu [role='menuitem']", has_text=label).click()


def test_new_search_and_folders_in_the_frame(browser, env):
    context, page, problems, made = seeded(browser, env)
    open_app(page, env, made["owner"], problems)
    # The top bar says only Monosage; the app is named for screen readers and in its menu below.
    assert page.locator("#appBrand").inner_text().strip() == "Monosage"
    assert page.locator("#appBrand").get_attribute("aria-label") == "Monosage Social: home"
    # The page is named after its tab, not the app again.
    assert page.locator("#main .page-head h1").inner_text().strip() == "Feed"

    # "+ New": the app's main creates, each opening its form; the address forgets the ask.
    page.click(".shell-new")
    assert menu_items(page) == [
        "Post",
        "Story",
        "Chat",
        "Marketplace listing",
        "Family event",
    ]
    page.keyboard.press("Escape")
    pick_new(page, "Story")
    page.wait_for_selector(".modal h2:has-text('Add a story')")
    until(page, lambda: "new=" not in page.url, what="the address to drop new=")
    page.keyboard.press("Escape")
    page.goto(f"{env.base}/social#/social?tab=groups")
    page.wait_for_selector("#main h1:has-text('Groups')")
    pick_new(page, "Post")
    until(
        page,
        lambda: "tab=feed" in page.url and "new=" not in page.url,
        what="the feed without new=",
    )
    until(
        page,
        lambda: page.evaluate(
            "() => document.activeElement && document.activeElement.getAttribute('aria-label') === 'Your post'"
        ),
        what="the composer to take the focus",
    )
    pick_new(page, "Chat")
    page.wait_for_selector(".modal h2:has-text('New chat')")
    assert page.locator(".modal label:has-text('Ana Silva')").count() == 1
    assert "tab=chats" in page.url and "new=" not in page.url
    page.keyboard.press("Escape")
    pick_new(page, "Marketplace listing")
    page.wait_for_selector(".modal h2:has-text('Sell something')")
    until(
        page,
        lambda: "tab=market" in page.url and "new=" not in page.url,
        what="the Marketplace without new=",
    )
    page.keyboard.press("Escape")
    pick_new(page, "Family event")
    page.wait_for_selector(".modal h2:has-text('Add an event')")
    until(
        page,
        lambda: "#/family?tab=calendar" in page.url and "new=" not in page.url,
        what="the family calendar without new=",
    )
    page.keyboard.press("Escape")
    # Back does not open the form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert page.locator(".modal").count() == 0

    # The search box: the app's records as you type; choosing one opens it.
    box = page.locator("#appSearchInput")
    assert "people" in (box.get_attribute("placeholder") or "").lower()
    box.click()
    box.fill("ana")
    page.wait_for_selector("#appSearchList [role='option']:has-text('@ana')")
    options = page.locator("#appSearchList [role='option']").all_inner_texts()
    assert any("Ana Silva" in text and "Friend" in text for text in options), options
    assert any("Chat" in text and "Lunch on Sunday?" in text for text in options), (
        options
    )
    page.locator("#appSearchList [role='option']", has_text="@ana").click()
    page.wait_for_selector("#main h2:has-text('Ana Silva')")
    assert f"person={made['friend']['id']}" in page.url
    box.fill("bench")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Marketplace')")
    page.locator("#appSearchList [role='option']", has_text="Garden bench").click()
    page.wait_for_selector("button:has-text('Message the seller')")
    assert f"listing={made['listing']['id']}" in page.url
    box.fill("grandma")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Family calendar')")
    page.locator("#appSearchList [role='option']", has_text="Sunday lunch").click()
    page.wait_for_selector("#main article.fa-picked:has-text('Sunday lunch')")
    box.fill("garden")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Group')")
    page.locator("#appSearchList [role='option']", has_text="Garden club").click()
    page.wait_for_selector("button:has-text('Change group')")

    # Enter: every match on one page, by kind.
    search_and_enter(page, box, "garden")
    page.wait_for_selector("#main h1:has-text('Results for')")
    page.wait_for_selector("#main h2:has-text('Groups (1)')")
    assert page.locator("#main h2:has-text('Marketplace (1)')").count() == 1
    assert "tab=search" in page.url and "q=garden" in page.url
    page.locator("#main a", has_text="Garden club").click()
    page.wait_for_selector("button:has-text('Change group')")
    search_and_enter(page, box, "nothing-like-this")
    page.wait_for_selector("#main .empty:has-text('Nothing in Social matches')")
    # A #tag lists its posts on the feed.
    search_and_enter(page, box, "#garden")
    page.wait_for_selector("#main h2:has-text('#garden')")

    # The left menu: Social's tabs as folders with real icons; a folder opens its tab.
    page.goto(f"{env.base}/social#/social?tab=feed")
    page.wait_for_selector("#main h1:has-text('Feed')")
    folders = page.locator(".shell-subnav[data-section='social'] .shell-sublink")
    names = [name.strip() for name in folders.all_inner_texts()]
    assert names[:6] == ["Feed", "Chats", "Friends", "Groups", "Pages", "Marketplace"]
    assert any(name.startswith("Notifications") for name in names), names
    assert page.locator(".shell-subnav .shell-sublink .shell-subdot").count() == 0
    folders.filter(has_text="Pages").click()
    page.wait_for_selector("#main h1:has-text('Pages')")
    assert "tab=pages" in page.url
    assert folders.filter(has_text="Pages").get_attribute("aria-current") == "page"
    # Family's folders, once Family is open.
    page.locator("#primaryNav a[data-section='family']").click()
    page.wait_for_selector("#main h1:has-text('Family feed')")
    family = page.locator(".shell-subnav[data-section='family'] .shell-sublink")
    assert [name.strip() for name in family.all_inner_texts()][:3] == [
        "Feed",
        "Chat",
        "Calendar",
    ]
    family.filter(has_text="Wish lists").click()
    page.wait_for_selector("#main h1:has-text('Wish lists')")
    assert "#/family?tab=wishes" in page.url
    # On a wide screen the menu's folders stand in for the page's own tab bar.
    assert not page.locator("#main .fa-page > .tabs").is_visible()

    # A teen sells nothing on the Marketplace, so "+ New" does not offer it; a child also has no Marketplace.
    offered = page.evaluate(
        "() => { const life = window.Aira.life; const was = life.me; life.me = { age_group: 'teen' };"
        " const labels = window.Aira.appNew.social().map((item) => item.label); life.me = was; return labels; }",
        isolated_context=False,
    )
    assert "Marketplace listing" not in offered and "Post" in offered
    assert not problems, problems
    context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_social_fits_a_phone(browser, env, dark):
    context, page, problems, made = seeded(
        browser, env, width=390, height=844, dark=dark
    )
    for hash_ in (
        "social?tab=feed",
        "social?tab=chats",
        f"social?tab=chats&chat={made['chat']['id']}",
        "social?tab=friends",
        "social?tab=market",
        "social?tab=search&q=garden",
        "family?tab=feed",
        "family?tab=calendar",
    ):
        open_app(page, env, made["owner"], problems, hash_)
        page.wait_for_timeout(300)
        wide = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert wide <= 1, (hash_, wide)
    # A phone keeps the page's own tab bar (the menu is a sheet).
    assert page.locator("#main .fa-page > .tabs").is_visible()
    assert not problems, problems
    context.close()
