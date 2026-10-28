"""Videos in a real browser: the ways a creator earns (memberships with a video for members, a brand deal, the
income overview), a scheduled upload, Studio's filters, and a fix found using it: the watch page wrote "null" under
the player when there was nowhere to carry on from."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, until  # noqa: E402, F401
from test_suite_videos_more_ui import _setup  # noqa: E402
from test_suite_videos_ui import _watching_page, record_webm  # noqa: E402
from videos_support import upload  # noqa: E402

NULL_TEXT = """() => {
  const walker = document.createTreeWalker(document.querySelector("main") || document.body, NodeFilter.SHOW_TEXT);
  const found = [];
  while (walker.nextNode()) { if (walker.currentNode.textContent.trim() === "null") found.push(walker.currentNode.parentElement.className); }
  return found;
}"""


def test_the_watch_page_writes_no_null_under_the_player(browser, env):
    creator_context, _creator, viewer_context, _viewer, video = _setup(browser, env)
    problems: list[str] = []
    page = _watching_page(viewer_context, problems)
    go(page, env, f"videos?watch={video['id']}")
    page.wait_for_selector(".vid-player video")
    page.wait_for_selector("text=Comments")
    assert page.evaluate(NULL_TEXT) == []
    assert not problems, problems
    viewer_context.close()
    creator_context.close()


def test_a_fan_joins_a_creator_who_earns_from_members_and_a_brand(browser, env):
    creator_context, creator, viewer_context, _viewer, video = _setup(browser, env)
    problems: list[str] = []
    assert creator.call("POST", "/api/videos/earnings/terms", {})[0] == 200
    assert creator.call("PUT", "/api/videos/support", {"upi": "maker@okbank"})[0] == 200
    maker = _watching_page(creator_context, problems)
    # memberships are set up on the page (INR, so the UPI app gets the amount)
    go(maker, env, "videos?tab=earnings&part=memberships")
    maker.check("input[name=enabled]")
    maker.fill("input[name=price_cents]", "99")
    maker.select_option("select[name=currency]", "INR")
    maker.fill("textarea[name=perks]", "A recipe for members every week")
    maker.get_by_role("button", name="Save", exact=True).click()
    until(
        maker,
        lambda: (
            maker.locator("input[name=price_cents]").input_value() == "99.00"
            and maker.locator(".suite-kpi", has_text="A month from members").count()
            == 1
        ),
        what="the plan to be saved",
    )
    secret = creator.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": upload(
                creator, "video", record_webm(creator_context, 160, 90, 3.0)
            ),
            "title": "Members recipe",
            "duration": 3,
            "width": 160,
            "height": 90,
            "members_only": True,
        },
    )[1]["video"]
    # the fan, on a phone: the video is locked, they join and are shown how to pay
    fan = _watching_page(viewer_context, problems)
    fan.set_viewport_size({"width": 390, "height": 844})
    go(fan, env, f"videos?watch={secret['id']}")
    fan.wait_for_selector(".vid-locked")
    # regression: without a picture the words had only the picture's narrow column
    fan.set_viewport_size({"width": 1280, "height": 900})
    words = fan.locator(".vid-locked .suite-body").bounding_box()
    assert words and words["width"] > 600, words
    fan.set_viewport_size({"width": 390, "height": 844})
    assert fan.locator(".vid-player").count() == 0
    fan.get_by_role("button", name="Join for ₹99.00 a month").click()
    fan.select_option("select[name=months]", "3")
    fan.get_by_role("button", name="Continue to pay").click()
    fan.wait_for_selector("text=How to pay")
    reference = fan.locator(".vid-code").first.inner_text()
    assert reference.startswith("MEM-")
    href = fan.locator("a", has_text="Pay with a UPI app").get_attribute("href")
    assert href.startswith("upi://pay?pa=maker@okbank") and "am=297.00" in href
    fan.get_by_role("button", name="Done").click()
    go(fan, env, "videos?tab=library")
    fan.wait_for_selector(f"text={reference}")
    assert fan.evaluate("() => document.documentElement.scrollWidth") <= 390
    # the creator marks it received; a brand deal is added and paid
    go(maker, env, "videos?tab=earnings&part=memberships")
    maker.reload()  # the same address: open it afresh
    maker.wait_for_selector(f"text={reference}")
    maker.get_by_role("button", name="Received", exact=True).click()
    maker.wait_for_selector("text=Welcome your new member")
    go(maker, env, "videos?tab=earnings&part=deals")
    maker.get_by_role("button", name="New deal").click()
    maker.fill("input[name=brand]", "Spice Co")
    maker.fill("input[name=amount_cents]", "250")
    maker.select_option("select[name=status]", "agreed")
    maker.select_option("select[name=video_id]", label="Garden walk")
    maker.get_by_role("button", name="Add deal").click()
    maker.wait_for_selector(".vid-deal:has-text('Spice Co')")
    maker.get_by_role("button", name="Mark paid").click()
    maker.wait_for_selector("text=The money is in your income")
    # the overview has both, and the log as a spreadsheet
    go(maker, env, "videos?tab=earnings")
    maker.wait_for_selector("text=Income log")
    kpis = maker.locator(".suite-kpis").inner_text()
    assert "$250.00" in kpis and "₹297.00" in kpis
    log = maker.locator("section.suite-section", has_text="Income log").inner_text()
    assert "Spice Co" in log and "Fan" in log
    with maker.expect_download() as download:
        maker.get_by_role("button", name="Download CSV").click()
    assert download.value.suggested_filename == "videos-income.csv"
    # the fan now watches it; the video with the deal says it includes paid promotion
    go(fan, env, f"videos?watch={secret['id']}")
    fan.wait_for_selector(".vid-player video")
    go(fan, env, f"videos?watch={video['id']}")
    fan.wait_for_selector("text=Includes paid promotion")
    assert fan.evaluate("() => document.documentElement.scrollWidth") <= 390
    assert not problems, problems
    viewer_context.close()
    creator_context.close()


def test_a_scheduled_upload_waits_and_studio_finds_it(browser, env):
    creator_context, _creator, viewer_context, viewer, video = _setup(browser, env)
    problems: list[str] = []
    page = _watching_page(creator_context, problems)
    go(page, env, "videos?tab=studio")
    page.set_input_files(
        "input[aria-label='Choose a video']",
        files=[
            {
                "name": "premiere.webm",
                "mimeType": "video/webm",
                "buffer": record_webm(creator_context, 160, 90, 2.0),
            }
        ],
    )
    page.locator("p", has_text="premiere.webm").first.wait_for()
    assert page.locator("input[name=publish_at]").is_hidden()
    page.select_option("select[name=publish_when]", "later")
    page.fill(
        "input[name=publish_at]",
        (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT18:30"),
    )
    # the form's own button (patchright 1.58's .last times out when one element matches)
    page.locator("#vidUpload button:has-text('Upload')").first.click()
    page.wait_for_url("**watch=*", timeout=60000)
    page.wait_for_selector("text=Until then only you can watch it")
    scheduled_id = page.url.split("watch=")[1]
    # nobody else can open it yet
    assert viewer.call("GET", f"/api/videos/videos/{scheduled_id}")[0] == 404
    go(page, env, "videos?tab=studio")
    page.get_by_role("button", name="Scheduled", exact=True).click()
    until(
        page,
        lambda: (
            page.locator(".vid-mine-item").count() == 1
            and "premiere" in page.locator(".vid-mine-item").inner_text()
        ),
        what="the scheduled filter",
    )
    page.fill("input[aria-label='Search your videos']", "garden")
    page.get_by_role("button", name="Find").click()
    until(
        page,
        lambda: "No videos match" in page.locator("main").inner_text(),
        what="no scheduled garden video",
    )
    page.get_by_role("button", name="All", exact=True).click()
    until(
        page,
        lambda: (
            page.locator(".vid-mine-item").count() == 1
            and video["title"] in page.locator(".vid-mine-item").inner_text()
        ),
        what="the search",
    )
    assert not problems, problems
    viewer_context.close()
    creator_context.close()
