"""Social's new screens in a real browser: the left rail and the phone's bottom bar (light and dark, no sideways
scroll), "+ Create", a photo uploaded from the device (with a filter and a description), voting in a poll, replying
to a comment, saving into a collection, replying to a story, Explore, the profile's photo grid and Notifications.
Screenshots go to D:/aira-test-tmp/social-shots (or $SOCIAL_SHOTS) for a person to look at."""

# ruff: noqa: F811
from __future__ import annotations

import base64
import json
import os
import struct
import zlib
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401

SHOTS = Path(os.environ.get("SOCIAL_SHOTS", "D:/aira-test-tmp/social-shots"))


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


def picture(width=480, height=360, hue=0) -> bytes:
    """A colourful PNG the browser can show (a gradient), made without any imaging library."""
    rows = []
    for y in range(height):
        line = bytearray(b"\x00")
        for x in range(width):
            r = (x * 255 // width + hue) % 256
            g = (y * 255 // height + hue // 2) % 256
            b = (200 - (x + y) * 120 // (width + height) + hue) % 256
            line += bytes((r, g, b))
        rows.append(bytes(line))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00taken at 51.50N 0.12W")
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + chunk(b"IEND", b"")
    )


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)


def seeded(browser, env, **size):
    """Pat (the owner) and Ana (another adult of the household) are friends; Ana has public photo posts with
    #tags, a poll, a post with a comment, and a story."""
    context, page, problems = signed_in_page(browser, env, **size)
    request = context.request

    def call(method, path, body, profile):
        if method == "GET":
            response = request.get(env.base + path, headers=headers(env, profile))
        else:
            response = request.fetch(
                env.base + path,
                method=method,
                data=json.dumps(body),
                headers=headers(env, profile),
            )
        assert response.status < 300, (path, response.text())
        return response.json()

    owner = request.get(env.base + "/api/life/me", headers=headers(env)).json()[
        "profile"
    ]
    ana = call(
        "POST", "/api/life/profiles", {"name": "Ana", "age_group": "adult"}, owner["id"]
    )["profile"]
    ana_header = call(
        "POST", "/api/life/unlock", {"profile_id": ana["id"], "pin": ""}, owner["id"]
    )["header"]
    me = call(
        "POST",
        "/api/social/me",
        {"handle": "pat", "display_name": "Pat Rivera"},
        owner["id"],
    )["account"]
    friend = call(
        "POST",
        "/api/social/me",
        {"handle": "ana", "display_name": "Ana Silva", "avatar": "🌻"},
        ana_header,
    )["account"]
    call("POST", f"/api/social/friends/{friend['id']}", {}, owner["id"])
    call("POST", f"/api/social/friends/{me['id']}", {}, ana_header)

    def photo(hue):
        data = base64.b64encode(picture(hue=hue)).decode()
        return call("POST", "/api/social/photos", {"data": data}, ana_header)["photo"][
            "ref"
        ]

    garden = call(
        "POST",
        "/api/social/posts",
        {
            "text": "First tomatoes of the season #garden #summer",
            "audience": "public",
            "photos": [photo(10), photo(90), photo(170)],
            "alts": ["Red tomatoes in a bowl", "", ""],
            "feeling": "proud",
            "place": "Riverside allotment",
        },
        ana_header,
    )["post"]
    call(
        "POST",
        "/api/social/posts",
        {
            "text": "Sunset walk by the river #summer",
            "audience": "public",
            "photos": [photo(200)],
            "filter": "warm",
        },
        ana_header,
    )
    poll = call(
        "POST",
        "/api/social/posts",
        {
            "text": "Picnic on Saturday: where shall we go?",
            "poll": {"options": ["The park", "The beach", "The hills"], "days": 3},
        },
        ana_header,
    )["post"]
    chat_post = call(
        "POST",
        "/api/social/posts",
        {"text": "Who wants my sourdough recipe?"},
        ana_header,
    )["post"]
    comment = call(
        "POST",
        f"/api/social/posts/{chat_post['id']}/comments",
        {"text": "Me please!"},
        ana_header,
    )["comment"]
    call(
        "POST",
        "/api/social/stories",
        {"text": "Baking day 🍞", "color": "#db2777"},
        ana_header,
    )
    call("POST", f"/api/social/posts/{garden['id']}/react", {"emoji": "❤️"}, owner["id"])
    return (
        context,
        page,
        problems,
        {
            "owner": owner,
            "ana_header": ana_header,
            "me": me,
            "friend": friend,
            "garden": garden,
            "poll": poll,
            "chat_post": chat_post,
            "comment": comment,
            "call": call,
        },
    )


def open_social(page, env, owner, hash_="social?tab=feed"):
    page.goto(f"{env.base}/social#/{hash_}")
    page.wait_for_selector(".life-picker, #main .page-head h1", timeout=20000)
    if (
        page.locator(".life-picker").count()
        and page.locator(".life-picker").is_visible()
    ):
        page.click(f".life-face:has-text('{owner['name']}')")
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_load_state("networkidle")


def no_sideways_scroll(page):
    return (
        page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        <= 1
    )


def test_social_desktop_rail_create_photos_polls_replies_saves_stories_explore_profile_notifications(
    browser, env
):
    context, page, problems, made = seeded(browser, env)
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    owner = made["owner"]
    open_social(page, env, owner)
    page.wait_for_selector("article:has-text('First tomatoes')")
    assert page.locator("#main .page-head h1").inner_text().strip() == "Home"

    # the rail: every section with its icon, in the frame's left menu
    folders = page.locator(".shell-subnav[data-section='social'] .shell-sublink")
    names = [name.strip() for name in folders.all_inner_texts()]
    for wanted in (
        "Home",
        "Explore",
        "Reels",
        "Chats",
        "Friends",
        "Groups",
        "Pages",
        "Marketplace",
        "Saved",
        "Profile",
    ):
        assert any(name.startswith(wanted) for name in names), (wanted, names)
    assert any(name.startswith("Notifications") for name in names)
    assert page.locator(
        ".shell-subnav[data-section='social'] .shell-sublink svg"
    ).count() == len(names)
    # a carousel of three photos, with the description, the feeling and the place
    garden = page.locator("article:has-text('First tomatoes')")
    assert garden.locator(".so-carousel .so-slide").count() == 3
    assert garden.locator("img[alt='Red tomatoes in a bowl']").count() == 1
    assert (
        "feeling proud" in garden.inner_text()
        and "Riverside allotment" in garden.inner_text()
    )
    until(
        page,
        lambda: page.evaluate(
            "() => [...document.querySelectorAll('.so-carousel img')].some((img) => img.naturalWidth > 0)"
        ),
        what="the photos to load",
    )
    shot(page, "01-home-desktop")

    # "+ Create" offers everything to make
    page.click(".so-create-btn")
    page.wait_for_selector(".modal h2:has-text('Create')")
    offered = [
        text.split("\n")[0].strip()
        for text in page.locator(".so-create-item").all_inner_texts()
    ]
    assert (
        offered[:5] == ["Post", "Photo", "Story", "Reel", "Poll"]
        and "Listing" in offered
    )
    shot(page, "02-create-menu")
    page.keyboard.press("Escape")

    # a photo from the device: shrunk, uploaded, described, filtered and posted
    with page.expect_file_chooser() as chooser:
        page.click(".so-composer button[aria-label='Photo']")
    chooser.value.set_files(
        files=[
            {"name": "garden.png", "mimeType": "image/png", "buffer": picture(hue=60)}
        ]
    )
    page.wait_for_selector(".so-composer .so-thumb img")
    page.fill(".so-composer .so-thumb-alt", "Our garden gate")
    page.click(".so-filter-chip:has-text('Vintage')")
    page.fill("textarea[aria-label='Your post']", "New gate, painted today")
    shot(page, "03-composer-photo")
    page.click(".so-composer button:has-text('Post')")
    page.wait_for_selector(
        "article:has-text('New gate, painted today') img[alt='Our garden gate']"
    )
    mine = page.locator("article:has-text('New gate, painted today')")
    assert "/api/social/photos/" in mine.locator(
        "img[alt='Our garden gate']"
    ).get_attribute("src")
    assert mine.locator(".so-media.so-filter-vintage").count() == 1
    until(
        page,
        lambda: (
            mine.locator("img[alt='Our garden gate']").evaluate(
                "img => img.naturalWidth"
            )
            > 0
        ),
        what="the uploaded photo to show",
    )

    # vote in a poll: the results show once you voted
    poll = page.locator("article:has-text('Picnic on Saturday')")
    poll.locator("button.so-poll-choice:has-text('The beach')").click()
    poll.locator(".so-poll-bar.mine:has-text('The beach')").wait_for()
    assert "100%" in poll.inner_text()
    shot(page, "04-poll-voted")

    # reply to a comment: the reply sits under it
    recipe = page.locator("article:has-text('sourdough recipe')")
    recipe.locator("button.so-action:has-text('Comment')").click()
    recipe.locator(
        ".so-comment:has-text('Me please!') button:has-text('Reply')"
    ).first.click()
    reply_box = recipe.locator(".so-reply-box input")
    reply_box.fill("I'll bring you a loaf too")
    reply_box.press("Enter")
    recipe.locator(".so-replies .so-comment:has-text('bring you a loaf')").wait_for()
    recipe.locator(
        ".so-comment:has-text('Me please!') button[aria-label^='Like']"
    ).first.click()
    recipe.locator(
        ".so-comment:has-text('Me please!') button[aria-pressed='true']"
    ).first.wait_for()
    shot(page, "05-comment-reply")

    # save a post into a new collection, then find it under Saved
    garden.locator("button.so-action:has-text('Save')").click()
    page.wait_for_selector(".modal h2:has-text('Save to a collection')")
    page.fill(".modal input[aria-label='Name of a new collection']", "Garden ideas")
    page.click(".modal button:has-text('Create and save')")
    page.wait_for_selector(
        "article:has-text('First tomatoes') button.so-action[aria-pressed='true']"
    )

    # reply to a story: a private message to its author
    page.click('.so-stories button[aria-label^="Ana Silva\'s story"]')
    page.wait_for_selector(".so-story-modal .so-story-card:has-text('Baking day')")
    shot(page, "06-story-viewer")
    page.click(".so-story-react button[aria-label='React 🔥']")
    page.fill("input[data-story-reply]", "Save me a slice!")
    page.press("input[data-story-reply]", "Enter")
    page.wait_for_selector(".toast:has-text('Sent to Ana Silva')")
    page.keyboard.press("Escape")
    chats = made["call"]("GET", "/api/social/chats", None, made["ana_header"])["chats"]
    assert any(c["last"] and "Save me a slice" in c["last"]["text"] for c in chats), (
        chats
    )

    # Saved: the collection with its post
    page.goto(f"{env.base}/social#/social?tab=saved")
    page.wait_for_selector(".so-collection:has-text('Garden ideas')")
    shot(page, "07-saved")
    page.click(".so-collection:has-text('Garden ideas')")
    page.wait_for_selector("article:has-text('First tomatoes')")

    # Explore: popular posts as a grid, #tags, people, and a search
    page.goto(f"{env.base}/social#/social?tab=explore")
    page.wait_for_selector(".so-grid .so-tile")
    assert page.locator(".so-chip-link:has-text('#summer')").count() == 1
    shot(page, "08-explore")
    page.fill("input[aria-label='Search Social']", "sunset")
    page.press("input[aria-label='Search Social']", "Enter")
    page.wait_for_selector("#main h2:has-text('Posts (1)')")

    # Reels: the Videos app's reels (none yet) and how to make one
    page.goto(f"{env.base}/social#/social?tab=reels")
    page.wait_for_selector("#main .empty:has-text('No reels yet'), #main .so-reels")
    assert page.locator("#main a[href*='videos?tab=studio&new=reel']").count() >= 1
    shot(page, "09-reels")

    # the profile: cover photo from the device, counts, and the photo grid
    page.goto(f"{env.base}/social#/social?tab=profile")
    page.wait_for_selector(".so-profile .so-profile-name")
    with page.expect_file_chooser() as chooser:
        page.click(".so-cover-btn")
    chooser.value.set_files(
        files=[
            {
                "name": "cover.png",
                "mimeType": "image/png",
                "buffer": picture(900, 300, hue=120),
            }
        ]
    )
    page.wait_for_selector(".so-cover[style*='/api/social/photos/']")
    page.click(".so-subtabs button:has-text('Photos')")
    page.wait_for_selector(".so-grid .so-tile img")
    shot(page, "10-profile-photos")
    # Ana's profile from Pat's side: a close friend, her highlights row, her grid
    page.goto(f"{env.base}/social#/social?tab=profile&person={made['friend']['id']}")
    page.wait_for_selector(".so-profile-name:has-text('Ana Silva')")
    page.click(".so-profile button:has-text('Add to close friends')")
    page.wait_for_selector(".so-profile .so-chip-star")

    # Notifications: Ana reacts and replies; Pat filters, opens one (it is read)
    made["call"](
        "POST",
        "/api/social/posts",
        {"text": "Hello @pat, see you Saturday"},
        made["ana_header"],
    )
    page.goto(f"{env.base}/social#/social?tab=notifications")
    page.wait_for_selector(".so-note.unread")
    shot(page, "11-notifications")
    page.click(".so-chip-btn:has-text('Mentions')")
    page.wait_for_selector(".so-note:has-text('mentioned you')")
    assert page.locator(".so-note.unread:has-text('mentioned you')").count() == 1
    page.click(".so-note:has-text('mentioned you') .so-note-link")
    page.wait_for_selector("article:has-text('see you Saturday')")
    page.goto(f"{env.base}/social#/social?tab=notifications&show=mentions")
    page.wait_for_selector(".so-note:has-text('mentioned you')")
    assert page.locator(".so-note.unread").count() == 0
    page.goto(f"{env.base}/social#/social?tab=notifications")
    page.wait_for_selector(".so-note")
    if page.locator("button:has-text('Mark all as read')").count():
        page.click("button:has-text('Mark all as read')")
        until(
            page,
            lambda: page.locator(".so-note.unread").count() == 0,
            what="everything to be read",
        )

    pageerrors = [p for p in problems if p.startswith("pageerror")]
    assert not pageerrors, pageerrors
    context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_social_phone_bottom_bar_and_more(browser, env, dark):
    context, page, problems, made = seeded(
        browser, env, width=390, height=844, dark=dark
    )
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    open_social(page, env, made["owner"])
    page.wait_for_selector("article:has-text('First tomatoes')")
    bar = page.locator(".so-bottom")
    assert bar.is_visible()
    labels = [
        t.splitlines()[0].strip()
        for t in bar.locator(".so-bottom-item").all_inner_texts()
    ]
    assert labels == ["Home", "Explore", "Reels", "Chats", "Alerts", "More"], (
        labels
    )
    assert (
        bar.locator(
            ".so-bottom-item[aria-label='Notifications'] .so-bottom-count"
        ).count()
        == 1
    )
    assert not page.locator("#bottomNav").is_visible()
    assert no_sideways_scroll(page)
    shot(page, f"20-phone-home-{'dark' if dark else 'light'}")
    bar.locator(".so-bottom-item:has-text('More')").click()
    page.wait_for_selector(".modal h2:has-text('More in Social')")
    assert page.locator(".so-more-item:has-text('Saved')").count() == 1
    shot(page, f"21-phone-more-{'dark' if dark else 'light'}")
    page.click(".so-more-item:has-text('Saved')")
    page.wait_for_selector("#main h1:has-text('Saved')")
    for hash_ in (
        "social?tab=explore",
        "social?tab=reels",
        "social?tab=notifications",
        "social?tab=profile",
        "social?tab=chats",
        "social?tab=friends",
        f"social?tab=feed&post={made['garden']['id']}",
    ):
        page.goto(f"{env.base}/social#/{hash_}")
        page.wait_for_selector("#main .page-head h1")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        assert no_sideways_scroll(page), hash_
        if hash_ in ("social?tab=explore", "social?tab=profile"):
            shot(page, f"22-phone-{hash_.split('=')[1]}-{'dark' if dark else 'light'}")
    pageerrors = [p for p in problems if p.startswith("pageerror")]
    assert not pageerrors, pageerrors
    context.close()
