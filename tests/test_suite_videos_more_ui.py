"""Videos in a real browser, for what was found using it: buttons that change their words (Like, Subscribe, Watch
later) kept their old words; the player did not carry on where the person stopped; Studio's list of videos hid its
buttons on a phone. And the creator's new pages: chapters, support from viewers, analytics with a CSV."""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, until  # noqa: E402, F401
from test_suite_videos_ui import _account, _watching_page, record_webm  # noqa: E402
from videos_support import upload  # noqa: E402

from aira.suite import kit, public  # noqa: E402
from aira.suite.videos import db  # noqa: E402

import auto_apply_assistant as local  # noqa: E402


def _setup(browser, env):
    kit.forget_schema()
    db.forget()
    public.set_index(local._public_link_index)
    creator_context, creator = _account(browser, env, "maker@example.com")
    viewer_context, viewer = _account(browser, env, "fan@example.com")
    film = record_webm(creator_context, 160, 90, 12.0)
    file_id = upload(creator, "video", film)
    status, made = creator.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": file_id,
            "title": "Garden walk",
            "description": "0:00 Gate\n0:04 Roses\n0:08 Pond\nA short walk.",
            "duration": 12,
            "width": 160,
            "height": 90,
        },
    )
    assert status == 201, made
    return creator_context, creator, viewer_context, viewer, made["video"]


def test_buttons_change_their_words_and_the_player_carries_on(browser, env):
    creator_context, _creator, viewer_context, viewer, video = _setup(browser, env)
    problems: list[str] = []
    page = _watching_page(viewer_context, problems)
    # the viewer stopped at 0:06 last time
    assert (
        viewer.call(
            "POST",
            f"/api/videos/videos/{video['id']}/progress",
            {"position": 6, "watched": 0},
        )[0]
        == 200
    )
    go(page, env, f"videos?watch={video['id']}")
    page.wait_for_selector(".vid-resume")
    assert "Carrying on from 0:06" in page.locator(".vid-resume").inner_text()
    player = page.locator(".vid-player video")
    until(
        page,
        lambda: player.evaluate("v => v.readyState") >= 1,
        timeout=20,
        what="the video to load",
    )
    until(
        page,
        lambda: player.evaluate("v => v.currentTime") >= 5.9,
        timeout=10,
        what="the player to carry on",
    )
    # chapters from the description move the player
    assert page.locator(".vid-chapter").count() == 3
    page.locator(".vid-chapter", has_text="Pond").click()
    until(
        page,
        lambda: player.evaluate("v => v.currentTime") >= 8,
        timeout=10,
        what="the chapter",
    )
    until(
        page,
        lambda: (
            page.locator(".vid-chapter[aria-current='true']").count() == 1
            and "Pond" in page.locator(".vid-chapter[aria-current='true']").inner_text()
        ),
        what="the playing chapter to be marked",
    )
    # Like, Watch later and Subscribe keep their new words (Aira.withBusy put the old words back)
    like = page.locator("button[aria-pressed]").filter(
        has_text=re.compile(r"^Liked? \(")
    )
    assert like.inner_text() == "Like (0)"
    like.click()
    until(page, lambda: like.inner_text() == "Liked (1)", what="Liked (1)")
    assert like.get_attribute("aria-pressed") == "true"
    like.click()
    until(page, lambda: like.inner_text() == "Like (0)", what="Like (0)")
    later = page.locator("button[aria-pressed]").filter(
        has_text=re.compile(r"^(Watch later|Saved for later)$")
    )
    later.click()
    until(page, lambda: later.inner_text() == "Saved for later", what="Saved for later")
    subscribe = page.locator("button[aria-pressed]").filter(
        has_text=re.compile(r"^Subscribe")
    )
    subscribe.click()
    until(page, lambda: subscribe.inner_text() == "Subscribed", what="Subscribed")
    until(
        page,
        lambda: page.get_by_text("1 subscriber", exact=True).count() == 1,
        what="the subscriber count",
    )
    # the home page offers to carry on (the chapter played on to nearly the end, which counts as finished)
    player.evaluate("v => v.pause()")
    page.wait_for_timeout(500)
    assert (
        viewer.call(
            "POST",
            f"/api/videos/videos/{video['id']}/progress",
            {"position": 6, "watched": 0},
        )[0]
        == 200
    )
    go(page, env, "videos")
    page.wait_for_selector("text=Continue watching")
    assert not problems, problems
    viewer_context.close()
    creator_context.close()


def test_a_viewer_supports_a_creator_who_sees_it_in_analytics_on_a_phone(browser, env):
    creator_context, creator, viewer_context, viewer, video = _setup(browser, env)
    problems: list[str] = []
    assert creator.call("POST", "/api/videos/earnings/terms", {})[0] == 200
    maker = _watching_page(creator_context, problems)
    go(maker, env, "videos?tab=earnings&part=support")
    maker.wait_for_selector("text=Support from viewers")
    maker.check("input[name=enabled]")
    maker.fill("input[name=link]", "http://pay.example/maker")
    maker.get_by_role("button", name="Save", exact=True).click()
    maker.wait_for_selector("text=must start with https://")
    problems.clear()  # the refused save is a 400 on purpose
    maker.fill("input[name=link]", "https://pay.example/maker")
    maker.get_by_role("button", name="Save", exact=True).click()
    until(
        maker,
        lambda: maker.locator("text=No support yet").count() == 1,
        what="support to be saved",
    )
    # the viewer pledges from the watch page and is shown where to pay
    fan = _watching_page(viewer_context, problems)
    go(fan, env, f"videos?watch={video['id']}")
    fan.get_by_role("button", name="Support", exact=True).click()
    fan.fill("textarea[name=message]", "Lovely roses")
    fan.get_by_role("button", name="Continue to pay").click()
    fan.wait_for_selector("text=How to pay")
    reference = fan.locator(".vid-code").first.inner_text()
    assert reference.startswith("TIP-")
    assert (
        fan.locator("a", has_text="Open their payment page").get_attribute("href")
        == "https://pay.example/maker"
    )
    # the creator marks it received, on a phone, and sees it in analytics
    phone = creator_context.new_page()
    phone.set_viewport_size({"width": 390, "height": 844})
    phone.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    go(phone, env, "videos?tab=earnings&part=support")
    phone.wait_for_selector(f"text={reference}")
    phone.get_by_role("button", name="Received", exact=True).click()
    phone.wait_for_selector("text=Marked received")
    go(phone, env, "videos?tab=analytics")
    phone.wait_for_selector(".vid-bars")
    assert "$5.00" in phone.locator(".suite-kpis").inner_text()
    assert phone.evaluate("() => document.documentElement.scrollWidth") <= 390
    with phone.expect_download() as download:
        phone.get_by_role("button", name="Download CSV").click()
    assert download.value.suggested_filename == "videos-analytics-28-days.csv"
    # Studio's list keeps Edit, Share and Delete on screen at phone width
    go(phone, env, "videos?tab=studio")
    phone.wait_for_selector(".vid-mine-item")
    for name in ("Edit", "Share", "Delete"):
        box = (
            phone.locator(".vid-mine-item")
            .first.get_by_role("button", name=name, exact=True)
            .bounding_box()
        )
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, (name, box)
    assert phone.evaluate("() => document.documentElement.scrollWidth") <= 390
    # the edit form has the language
    phone.locator(".vid-mine-item").first.get_by_role(
        "button", name="Edit", exact=True
    ).click()
    phone.wait_for_selector("select[name=language]")
    assert not [p for p in problems if "status of 400" not in p], problems
    viewer_context.close()
    creator_context.close()


def test_a_shared_video_plays_for_someone_without_an_account(browser, env):
    """Regression: the public page read the wrong part of the answer, so every shared video said it was not
    available. Also the copyright form's statements: each box sits beside its sentence."""
    creator_context, creator, viewer_context, _viewer, video = _setup(browser, env)
    status, made = creator.call("POST", f"/api/videos/videos/{video['id']}/link", {})
    assert status == 201, made
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    problems: list[str] = []
    page = _watching_page(visitor, problems)
    page.goto(env.base + made["link"]["path"])
    page.wait_for_selector("h1:has-text('Garden walk')")
    player = page.locator(".pub-card video")
    until(
        page,
        lambda: player.evaluate("v => v.readyState") >= 1,
        timeout=20,
        what="the shared video to load",
    )
    page.get_by_role("button", name="0:08 Pond").click()
    until(
        page,
        lambda: player.evaluate("v => v.currentTime") >= 8,
        timeout=10,
        what="the chapter",
    )
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    page.get_by_role("link", name="Report a copyright problem").click()
    page.wait_for_selector("h1:has-text('Report a copyright problem')")
    assert page.locator("#cpVideo").input_value() == video["id"]
    box = page.locator("#cpGoodFaith").bounding_box()
    words = page.get_by_text("I believe in good faith").bounding_box()
    assert box and words and box["width"] <= 30 and words["x"] > box["x"] + box["width"]
    assert not problems, problems
    visitor.close()
    viewer_context.close()
    creator_context.close()
