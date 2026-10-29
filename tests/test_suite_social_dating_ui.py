"""Dating in a real browser against the local server: onboarding, Discover, a match and its chat, on a wide screen
(light) and a phone (dark), with nothing scrolling sideways. Screenshots go to D:/aira-test-tmp/dating-shots (or
AIRA_DATING_SHOTS) for a person to look at."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401

SHOTS = Path(os.environ.get("AIRA_DATING_SHOTS", "D:/aira-test-tmp/dating-shots"))


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


def headers(env):
    return {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }


# Made-up portraits for the photo links (the test has no network): a soft gradient, a sun and a figure.
PALETTES = {
    "pat1": ("#f9a8b8", "#c084fc"),
    "pat2": ("#fdba74", "#f472b6"),
    "sam1": ("#93c5fd", "#34d399"),
    "sam2": ("#a5b4fc", "#22d3ee"),
    "leo1": ("#fde68a", "#fb923c"),
}


def portrait(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split(".")[0]
    top, bottom = PALETTES.get(name, ("#e2e8f0", "#94a3b8"))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500">
<defs><linearGradient id="g" x1="0" y1="0" x2="0.4" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient></defs>
<rect width="400" height="500" fill="url(#g)"/><circle cx="300" cy="110" r="56" fill="#fff" opacity="0.35"/>
<circle cx="200" cy="215" r="78" fill="#fff" opacity="0.82"/><path d="M60 500c10-120 70-170 140-170s130 50 140 170z" fill="#fff" opacity="0.82"/>
</svg>"""


def png(width=200, height=250) -> bytes:
    """A small real PNG (a warm gradient) for the upload from the device."""
    import struct
    import zlib

    rows = b"".join(
        b"\x00"
        + b"".join(
            bytes((240, 120 + y * 100 // height, 90 + x * 120 // width))
            for x in range(width)
        )
        for y in range(height)
    )

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def photos_offline(context):
    context.route(
        "https://img.example/**",
        lambda route: route.fulfill(
            status=200, content_type="image/svg+xml", body=portrait(route.request.url)
        ),
    )


def no_sideways_scroll(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(350)  # transitions settle
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def register(browser, env, email, *, width=390, height=844, dark=True):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme="dark" if dark else "light",
    )
    made = context.request.post(
        env.base + "/api/auth/register",
        data=json.dumps({"email": email, "password": PASSWORD}),
        headers=headers(env),
    )
    assert made.status == 201, made.text()
    photos_offline(context)
    page = context.new_page()
    page.set_default_timeout(60_000)
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    return context, page, problems


def call(context, env, method, path, body=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body or {}),
        headers=headers(env),
    )
    assert response.status < 300, (path, response.status, response.text())
    return response.json()


def onboard(
    page,
    *,
    name,
    birthday,
    gender,
    show_me,
    photo,
    interests,
    phone=False,
    prefix="",
    upload=False,
):
    page.wait_for_selector(".sd-hero")
    assert page.locator(".sd-subnav").count() == 0
    shot(page, f"{prefix}01-welcome")
    page.click("button:has-text('Create my dating profile')")
    page.wait_for_selector("#sdStepTitle:has-text('About you')")
    page.fill("input[autocomplete='given-name']", name)
    page.fill(".sd-wizard input[type='date']", birthday)
    page.click(f".sd-option[data-value='{gender}']")
    page.fill("input[autocomplete='address-level2']", "Riverside")
    # Next without the 18+ confirmation is refused, in words
    page.click(".sd-step-actions button:has-text('Next')")
    page.wait_for_selector(".sd-wizard .form-error:has-text('18 or over')")
    page.check(".sd-confirm input")
    shot(page, f"{prefix}02-step-about")
    if phone:
        assert no_sideways_scroll(page)
    page.click(".sd-step-actions button:has-text('Next')")
    page.wait_for_selector("#sdStepTitle:has-text('Photos')")
    if upload:  # a photo from the device, through Social's upload
        with page.expect_file_chooser() as chooser:
            page.click(".sd-add-photo button:has-text('Upload photos')")
        chooser.value.set_files(
            {"name": "me.png", "mimeType": "image/png", "buffer": png()}
        )
        until(
            page,
            lambda: (
                page.locator(".sd-thumb img[src^='/api/social/photos/']").count() == 1
            ),
            timeout=15,
            what="the uploaded photo",
        )
    for link in photo:
        page.fill("input[aria-label='Photo link']", link)
        page.click(".sd-add-photo button:has-text('Add')")
    page.fill(
        ".sd-wizard textarea",
        f"{name} here: early riser, terrible at chess, great at pancakes.",
    )
    until(
        page,
        lambda: (
            page.locator(".sd-thumb img").count() == len(photo) + (1 if upload else 0)
        ),
        what="the photos",
    )
    shot(page, f"{prefix}03-step-photos")
    page.click(".sd-step-actions button:has-text('Next')")
    page.wait_for_selector("#sdStepTitle:has-text('Three prompts')")
    answers = {
        "slow_sunday": "Pancakes, the market, then a long walk by the river",
        "talk_hours": "Old maps and where they were wrong",
        "first_date": "Ice cream and a bookshop wander",
    }
    for i, (prompt, answer) in enumerate(answers.items(), start=1):
        page.select_option(f"select[aria-label='Prompt {i}']", prompt)
        page.fill(f"textarea[aria-label='Your answer to prompt {i}']", answer)
    shot(page, f"{prefix}04-step-prompts")
    if phone:
        assert no_sideways_scroll(page)
    page.click(".sd-step-actions button:has-text('Next')")
    page.wait_for_selector("#sdStepTitle:has-text('meet')")
    page.click(".sd-option[data-value='relationship']")
    page.click(f".sd-option[data-value='{show_me}']")
    for interest in interests:
        page.click(f".sd-option[data-value='{interest}']")
    shot(page, f"{prefix}05-step-meet")
    if phone:
        assert no_sideways_scroll(page)
    page.click("button:has-text('Start meeting people')")
    page.wait_for_selector(".sd-subnav")


def test_dating_onboarding_discover_match_and_chat_on_a_wide_screen_and_a_phone(
    browser, env
):
    # Sam sets up Dating on a phone, in dark mode
    sam_ctx, sam, sam_problems = register(browser, env, "dating-sam@example.com")
    call(
        sam_ctx,
        env,
        "POST",
        "/api/social/me",
        {"handle": "samdating", "display_name": "Sam Rivers"},
    )
    sam.goto(f"{env.base}/#/social?tab=dating")
    onboard(
        sam,
        name="Sam",
        birthday="1993-04-18",
        gender="man",
        show_me="women",
        photo=["https://img.example/sam1.svg", "https://img.example/sam2.svg"],
        interests=["hiking", "coffee", "reading"],
        phone=True,
        prefix="phone-dark-",
    )
    sam.wait_for_selector(".sd-empty:has-text('all caught up')")
    assert no_sideways_scroll(sam)

    # Leo (another account) likes whoever comes along; made through the API
    leo_ctx, _leo_page, _ = register(
        browser, env, "dating-leo@example.com", width=800, height=600, dark=False
    )
    call(leo_ctx, env, "POST", "/api/social/me", {"handle": "leodating"})
    call(
        leo_ctx,
        env,
        "PUT",
        "/api/social/dating/me",
        {
            "first_name": "Leo",
            "birthday": "1990-09-02",
            "confirm_adult": True,
            "gender": "man",
            "show_me": "women",
            "city": "Hillside",
            "photos": ["https://img.example/leo1.svg"],
            "interests": ["coffee", "football"],
            "looking_for": "casual",
            "prompts": [
                {"id": "small_joy", "answer": "The first coffee of the day"},
                {"id": "comfort", "answer": "Paddington 2"},
                {"id": "get_along", "answer": "Like terrible puns"},
            ],
        },
    )

    # Pat sets up Dating on a wide screen, in light mode
    pat_ctx, pat, pat_problems = signed_in_page(browser, env)
    photos_offline(pat_ctx)
    call(
        pat_ctx,
        env,
        "POST",
        "/api/social/me",
        {"handle": "patdating", "display_name": "Pat Example"},
    )
    pat.goto(f"{env.base}/#/social?tab=dating")
    pat.wait_for_selector(".life-picker, .sd-hero", timeout=20000)
    onboard(
        pat,
        name="Pat",
        birthday="1995-06-30",
        gender="woman",
        show_me="men",
        photo=["https://img.example/pat2.svg"],
        interests=["hiking", "coffee", "baking"],
        prefix="desktop-",
        upload=True,
    )
    # Discover: Sam first (more in common), then Leo
    pat.wait_for_selector(".sd-card .sd-name:has-text('Sam')")
    assert pat.locator(".sd-chip.sd-shared").count() == 2  # hiking and coffee
    assert (
        "30 likes and 3 stars left today" in pat.locator(".sd-allowance").inner_text()
    )
    shot(pat, "desktop-06-discover")
    pat.click("button[aria-label='Like Sam']")
    pat.wait_for_selector(".sd-card .sd-name:has-text('Leo')")
    # a pass, and Undo brings Leo back
    pat.click("button[aria-label='Pass on Leo']")
    pat.wait_for_selector(".sd-empty:has-text('all caught up')")
    pat.click("button:has-text('Undo last pass')")
    pat.wait_for_selector(".sd-card .sd-name:has-text('Leo')")
    pat.keyboard.press("ArrowLeft")  # the arrow keys answer too
    pat.wait_for_selector(".sd-empty:has-text('all caught up')")

    # Sam sees one like, meets Pat and likes her back: a match
    sam.goto(f"{env.base}/#/social?tab=dating&dv=likes")
    sam.wait_for_selector(".sd-likes .sd-big:has-text('1')")
    assert (
        sam.locator(".sd-hint").count() == 1
        and "Pat" not in sam.locator(".sd-likes").inner_text()
    )
    shot(sam, "phone-dark-06-likes-you")
    sam.click("button:has-text('Meet them')")
    sam.wait_for_selector(".sd-ribbon:has-text('Likes you')")
    sam.wait_for_selector(".sd-card .sd-name:has-text('Pat')")
    # Pat's uploaded photo comes first and loads for Sam (a signed address, not the stored reference)
    first = sam.locator(".sd-card .sd-photo img").first
    assert first.get_attribute("src").startswith("/api/social/photos/")
    until(
        sam,
        lambda: first.evaluate("img => img.complete && img.naturalWidth > 0"),
        timeout=15,
        what="Pat's photo to load",
    )
    shot(sam, "phone-dark-07-discover")
    assert no_sideways_scroll(sam)
    sam.click("button[aria-label='Like Pat']")
    sam.wait_for_selector(".sd-celebrate-box:has-text('You both said yes!')")
    shot(sam, "phone-dark-08-match")
    sam.click(".modal button:has-text('Message Pat')")
    sam.wait_for_selector(".sd-chat .sd-idea")
    shot(sam, "phone-dark-09-chat-ideas")
    assert no_sideways_scroll(sam)
    sam.locator(".sd-idea").first.click()
    assert "slow Sunday" in sam.locator(".sd-composer textarea").input_value()
    sam.click(".sd-send")
    sam.wait_for_selector(".sd-msg.mine")
    assert sam.locator(".sd-ideas").is_hidden()

    # Pat finds the match and the message, and answers; Sam sees it arrive live
    pat.goto(f"{env.base}/#/social?tab=dating&dv=matches")
    pat.wait_for_selector(".sd-convo:has-text('Sam')")
    assert pat.locator(".sd-unread").inner_text() == "1"
    shot(pat, "desktop-07-matches")
    pat.click(".sd-convo:has-text('Sam')")
    pat.wait_for_selector(".sd-msg:not(.mine) .sd-bubble-msg:has-text('slow Sunday')")
    pat.fill(
        ".sd-composer textarea", "Ha! Pancakes first, always. Then the river walk?"
    )
    pat.keyboard.press("Enter")
    pat.wait_for_selector(".sd-msg.mine:has-text('Pancakes first')")
    shot(pat, "desktop-08-chat")
    until(
        sam,
        lambda: (
            sam.locator(".sd-msg:not(.mine):has-text('Pancakes first')").count() == 1
        ),
        timeout=15,
        what="Pat's answer on Sam's phone",
    )
    shot(sam, "phone-dark-10-chat")
    assert no_sideways_scroll(sam)
    # the dating chat is not in Social's chats list
    chats = call(pat_ctx, env, "GET", "/api/social/chats")
    assert chats["chats"] == []

    # Pat's profile page, and the safety tips
    pat.goto(f"{env.base}/#/social?tab=dating&dv=profile")
    pat.wait_for_selector(".sd-status:has-text('Active')")
    shot(pat, "desktop-09-profile")
    pat.click(".sd-status button:has-text('Pause')")
    pat.wait_for_selector(".sd-status.paused")
    pat.click(".sd-status button:has-text('Turn back on')")
    pat.wait_for_selector(".sd-status:not(.paused)")
    pat.goto(f"{env.base}/#/social?tab=dating&dv=safety")
    pat.wait_for_selector(".sd-tips")
    shot(pat, "desktop-10-safety")
    sam.goto(f"{env.base}/#/social?tab=dating&dv=profile")
    sam.wait_for_selector(".sd-status")
    shot(sam, "phone-dark-11-profile")
    assert no_sideways_scroll(sam)

    # unmatch from the phone: the chat is gone for both
    sam.goto(f"{env.base}/#/social?tab=dating&dv=matches")
    sam.click(".sd-convo:has-text('Pat')")
    sam.click(".sd-chat-tools button:has-text('Unmatch')")
    sam.click(".modal button:has-text('Unmatch')")
    sam.wait_for_selector(".sd-empty:has-text('No matches yet')")
    assert call(pat_ctx, env, "GET", "/api/social/dating/matches")["matches"] == []

    errors = [p for p in sam_problems + pat_problems if p.startswith("pageerror")]
    assert not errors, errors


def test_children_and_teens_have_no_dating_tab(browser, env):
    context, page, problems = signed_in_page(browser, env)
    request = context.request
    owner = request.get(env.base + "/api/life/me", headers=headers(env)).json()[
        "profile"
    ]
    teen = request.post(
        env.base + "/api/life/profiles",
        data=json.dumps({"name": "Teo", "age_group": "teen"}),
        headers={**headers(env), "X-Aira-Profile": owner["id"]},
    ).json()["profile"]
    teen_header = request.post(
        env.base + "/api/life/unlock",
        data=json.dumps({"profile_id": teen["id"], "pin": ""}),
        headers={**headers(env), "X-Aira-Profile": owner["id"]},
    ).json()["header"]
    request.post(
        env.base + "/api/social/me",
        data=json.dumps({"handle": "teodating"}),
        headers={**headers(env), "X-Aira-Profile": teen_header},
    )
    page.goto(f"{env.base}/#/social?tab=dating")
    page.wait_for_selector(".life-picker", timeout=20000)
    page.click(".life-face:has-text('Teo')")
    page.wait_for_selector(".so-page")
    # no Dating tab, and asking for it opens the feed
    assert page.locator(".so-page .tabs button:has-text('Dating')").count() == 0
    assert page.locator(".sd-root").count() == 0
    errors = [p for p in problems if p.startswith("pageerror")]
    assert not errors, errors
