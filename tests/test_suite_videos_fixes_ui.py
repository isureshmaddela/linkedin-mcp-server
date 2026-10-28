"""Videos in a real browser: the journeys a tester found broken in the ways a creator earns.

* The upload form offers "Only for my members" only when memberships are on, and a time that has passed is
  refused before anything is uploaded.
* Share offers no public page for a video for members.
* "Not received" on a received membership payment asks first, and a mistake can be put right.
* The keyboard stays on an Earnings part or filter button after pressing it.
* At phone width the 12-month income table fits (one card per month).
"""

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

from aira.suite.videos import db, store  # noqa: E402


def _real(problems):
    """Console errors other than the 400 the page answers on purpose."""
    return [item for item in problems if "status of 400" not in item]


def _files():
    with db.connect() as sc:
        return store.rows(sc, "SELECT id FROM vi_files")


def test_the_upload_form_checks_before_sending_the_file(browser, env):
    creator_context, creator, viewer_context, _viewer, _video = _setup(browser, env)
    problems: list[str] = []
    assert creator.call("POST", "/api/videos/earnings/terms", {})[0] == 200
    page = _watching_page(creator_context, problems)
    go(page, env, "videos?tab=studio")
    page.set_input_files(
        "input[aria-label='Choose a video']",
        files=[
            {
                "name": "slip.webm",
                "mimeType": "video/webm",
                "buffer": record_webm(creator_context, 160, 90, 2.0),
            }
        ],
    )
    page.locator("p", has_text="slip.webm").first.wait_for()
    # memberships are off: the box is not offered
    assert page.locator("input[name=members_only]").count() == 0
    before = len(_files())
    # yesterday by mistake: refused at once, nothing uploaded
    page.select_option("select[name=publish_when]", "later")
    assert page.locator("input[name=publish_at]").get_attribute("min")
    page.fill(
        "input[name=publish_at]",
        (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT10:00"),
    )
    # the form's own button (patchright 1.58's .last times out when one element matches)
    page.locator("#vidUpload button:has-text('Upload')").first.click()
    page.wait_for_selector("text=That time has already passed")
    assert len(_files()) == before
    assert page.locator(".vid-progress").is_hidden()
    # with memberships on, the box is there
    assert creator.call("PUT", "/api/videos/support", {"upi": "maker@okbank"})[0] == 200
    assert (
        creator.call(
            "PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 300}
        )[0]
        == 200
    )
    page.reload()
    page.wait_for_selector("input[name=members_only]", state="attached")
    assert not _real(problems), problems
    viewer_context.close()
    creator_context.close()


def test_share_offers_no_public_page_for_a_video_for_members(browser, env):
    creator_context, creator, viewer_context, _viewer, _video = _setup(browser, env)
    problems: list[str] = []
    creator.call("POST", "/api/videos/earnings/terms", {})
    creator.call("PUT", "/api/videos/support", {"upi": "maker@okbank"})
    creator.call(
        "PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 300}
    )
    status, made = creator.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": upload(
                creator, "video", record_webm(creator_context, 160, 90, 2.0)
            ),
            "title": "Members only cut",
            "duration": 2,
            "width": 160,
            "height": 90,
            "members_only": True,
        },
    )
    assert status == 201, made
    page = _watching_page(creator_context, problems)
    go(page, env, "videos?tab=studio")
    item = page.locator(".vid-mine-item", has_text="Members only cut")
    item.get_by_role("button", name="Share").click()
    page.wait_for_selector("text=Videos for members have no page for everyone")
    assert page.get_by_role("button", name="Create link").count() == 0
    assert not _real(problems), problems
    viewer_context.close()
    creator_context.close()


def test_not_received_asks_first_and_a_mistake_can_be_put_right(browser, env):
    creator_context, creator, viewer_context, viewer, _video = _setup(browser, env)
    problems: list[str] = []
    creator.call("POST", "/api/videos/earnings/terms", {})
    creator.call("PUT", "/api/videos/support", {"upi": "maker@okbank"})
    creator.call(
        "PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 300}
    )
    channel = creator.call("GET", "/api/videos/me")[1]["channel"]["id"]
    status, joined = viewer.call(
        "POST", f"/api/videos/channels/{channel}/join", {"months": 3}
    )
    assert status == 201, joined
    payment = joined["payment"]["id"]
    assert (
        creator.call(
            "POST", f"/api/videos/memberships/{payment}", {"action": "received"}
        )[0]
        == 200
    )
    page = _watching_page(creator_context, problems)
    go(page, env, "videos?tab=earnings&part=memberships")
    page.wait_for_selector("text=Received, thank you")
    # a misclick: it asks first; Cancel leaves the member paid
    page.get_by_role("button", name="Not received", exact=True).click()
    page.wait_for_selector("text=Mark this payment not received?")
    page.get_by_role("button", name="Cancel").click()
    assert (
        viewer.call("GET", "/api/videos/memberships/mine")[1]["memberships"][0][
            "active"
        ]
        is True
    )
    # confirmed: the months go; pressing Received puts them back
    page.get_by_role("button", name="Not received", exact=True).click()
    page.get_by_role("button", name="Mark not received").click()
    page.wait_for_selector("text=Marked not received")
    assert (
        viewer.call("GET", "/api/videos/memberships/mine")[1]["memberships"][0][
            "active"
        ]
        is False
    )
    page.get_by_role("button", name="Received", exact=True).click()
    page.wait_for_selector("text=Received, thank you")
    assert (
        viewer.call("GET", "/api/videos/memberships/mine")[1]["memberships"][0][
            "active"
        ]
        is True
    )
    assert not _real(problems), problems
    viewer_context.close()
    creator_context.close()


def test_the_keyboard_stays_on_the_button_pressed_and_the_table_fits_a_phone(
    browser, env
):
    creator_context, creator, viewer_context, _viewer, _video = _setup(browser, env)
    problems: list[str] = []
    creator.call("POST", "/api/videos/earnings/terms", {})
    for brand in ("Spice Co", "Tea Co"):
        assert (
            creator.call(
                "POST",
                "/api/videos/deals",
                {"brand": brand, "amount_cents": 12345678, "status": "paid"},
            )[0]
            == 201
        )
    page = _watching_page(creator_context, problems)
    go(page, env, "videos?tab=earnings")
    page.wait_for_selector("text=Income log")
    page.get_by_role("button", name="Brand deals", exact=True).first.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Waiting to be paid")
    until(
        page,
        lambda: (
            page.evaluate("() => document.activeElement.textContent") == "Brand deals"
        ),
        what="the focus on Brand deals",
    )
    page.get_by_role("button", name="Paid", exact=True).focus()
    page.keyboard.press("Enter")
    until(
        page,
        lambda: (
            page.evaluate(
                "() => document.activeElement.textContent + '|' + document.activeElement.getAttribute('aria-pressed')"
            )
            == "Paid|true"
        ),
        what="the focus on the Paid filter",
    )
    # on a phone the 12-month table is one card per month: nothing hidden off to the side
    page.set_viewport_size({"width": 390, "height": 844})
    go(page, env, "videos?tab=earnings&part=overview")
    page.wait_for_selector(".vid-stack")
    fits = page.evaluate(
        """() => {
          const wrap = document.querySelector('.vid-stack');
          return { page: document.documentElement.scrollWidth, wrap: wrap.scrollWidth, box: wrap.clientWidth,
                   total: [...wrap.querySelectorAll('td')].some((td) => td.dataset.label === 'Total' && td.getBoundingClientRect().right <= 390) };
        }"""
    )
    assert fits["page"] <= 390 and fits["wrap"] <= fits["box"] and fits["total"], fits
    page.screenshot(path="D:/tmp/aira-work/videos/overview-390.png", full_page=True)
    assert not _real(problems), problems
    viewer_context.close()
    creator_context.close()
