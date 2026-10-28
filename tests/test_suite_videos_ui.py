"""Videos and Ads in a real browser: upload a reel (a frame picked as its picture), watch it, move through Reels; an
ad campaign served before a video, skipped after five seconds, and the creator credited; the platform console's
Videos moderation and Creators & ads pages.

The test videos are recorded in the browser itself (a canvas through MediaRecorder: a few seconds of WebM), so
nothing large is kept in the repository and Chromium can really play them."""

# ruff: noqa: F811
from __future__ import annotations

import base64
import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from cloud_support import build_org, sign_in  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_cloud_apps import _open, served  # noqa: E402, F401
from test_cloud_ui_billing import operator_browser  # noqa: E402
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from videos_support import PNG, make_mp4, upload  # noqa: E402

from aira.suite import kit, public  # noqa: E402
from aira.suite.videos import db  # noqa: E402

import auto_apply_assistant as local  # noqa: E402

RECORD = """async ([width, height, seconds]) => {
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const g = canvas.getContext("2d");
  let frame = 0;
  const paint = () => { frame += 1; g.fillStyle = `hsl(${(frame * 7) % 360} 70% 50%)`; g.fillRect(0, 0, width, height); g.fillStyle = "#fff"; g.font = "28px sans-serif"; g.fillText(String(frame), 20, 50); };
  paint();
  const timer = setInterval(paint, 40);
  const stream = canvas.captureStream(25);
  const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
  const parts = [];
  recorder.ondataavailable = (event) => { if (event.data.size) parts.push(event.data); };
  const done = new Promise((resolve) => { recorder.onstop = resolve; });
  recorder.start(200);
  await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
  recorder.stop();
  await done;
  clearInterval(timer);
  const blob = new Blob(parts, { type: "video/webm" });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i]);
  return btoa(text);
}"""


def record_webm(context, width: int, height: int, seconds: float = 3.0) -> bytes:
    page = context.new_page()
    page.goto("about:blank")
    data = page.evaluate(RECORD, [width, height, seconds])
    page.close()
    return base64.b64decode(data)


HEADERS = {"Content-Type": "application/json", "X-Requested-With": "aira"}


class Requester:
    """``call`` over a browser context's own cookies (what videos_support.upload expects)."""

    def __init__(self, context, base: str) -> None:
        self.context, self.base = context, base

    def call(self, method, path, body=None):
        response = self.context.request.fetch(
            self.base + path,
            method=method,
            data=json.dumps(body) if body is not None else None,
            headers={**HEADERS, "Origin": self.base},
        )
        try:
            return response.status, response.json()
        except Exception:  # noqa: BLE001
            return response.status, response.text()


def _account(browser, env, email):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    response = context.request.post(
        env.base + "/api/auth/register",
        data=json.dumps({"email": email, "password": PASSWORD}),
        headers={**HEADERS, "Origin": env.base},
    )
    assert response.status == 201, response.text()
    return context, Requester(context, env.base)


def _watching_page(context, problems):
    page = context.new_page()
    page.set_default_timeout(60_000)
    page.on(
        "console",
        lambda m: (
            problems.append(f"console.{m.type}: {m.text}")
            if m.type == "error"
            else None
        ),
    )
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    return page


def test_upload_a_reel_watch_it_and_move_through_reels(browser, env):
    kit.forget_schema()
    db.forget()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    tall = record_webm(context, 180, 320, 3.0)
    assert tall[:4] == b"\x1a\x45\xdf\xa3"
    go(page, env, "videos?tab=studio")
    page.wait_for_selector("text=Upload a video")
    page.set_input_files(
        "input[aria-label='Choose a video']",
        files=[{"name": "sunset.webm", "mimeType": "video/webm", "buffer": tall}],
    )
    facts = page.locator("p", has_text="sunset.webm")
    until(
        page,
        lambda: facts.count() > 0 and "(tall)" in facts.first.inner_text(),
        timeout=20,
        what="the video's size to be read",
    )
    assert (
        page.locator("select[name=kind]").input_value() == "reel"
    )  # a short tall video is offered as a reel
    page.fill("input[name=title]", "Sunset reel")
    page.locator("button:has-text('Use this frame')").click()
    page.wait_for_selector("text=Picture chosen.")
    page.locator("button:has-text('Upload')").last.click()
    page.wait_for_url("**watch=*", timeout=60_000)
    page.wait_for_selector("h1:has-text('Sunset reel')")
    player = page.locator(".vid-player video")
    until(
        page,
        lambda: player.evaluate("v => v.readyState") >= 2,
        timeout=30,
        what="the reel to load in the player",
    )
    assert "/api/videos/media/" in player.evaluate("v => v.currentSrc")
    # a second reel through the API, then the feed: one at a time, muted, arrow keys move between them
    second = Requester(context, env.base)
    file_id = upload(second, "video", record_webm(context, 180, 320, 2.0))
    status, made = second.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": file_id,
            "title": "Second reel",
            "kind": "reel",
            "duration": 2,
            "width": 180,
            "height": 320,
        },
    )
    assert status == 201, made
    go(page, env, "videos?tab=reels")
    page.wait_for_selector(".vid-reel video")
    assert page.locator(".vid-reel video").evaluate("v => v.muted") is True
    until(
        page,
        lambda: page.locator(".vid-reels > span.vid-meta").inner_text() == "1 of 2",
        what="the first reel",
    )
    page.locator(".vid-reels").focus()
    page.keyboard.press("ArrowDown")
    until(
        page,
        lambda: page.locator(".vid-reels > span.vid-meta").inner_text() == "2 of 2",
        what="the next reel",
    )
    page.wait_for_timeout(600)
    page.keyboard.press("ArrowUp")
    until(
        page,
        lambda: page.locator(".vid-reels > span.vid-meta").inner_text() == "1 of 2",
        what="the previous reel",
    )
    # the home page lists both, with the picked frame as a picture
    go(page, env, "videos")
    page.wait_for_selector(".vid-card")
    assert page.locator(".vid-card img").count() >= 1
    assert not problems, problems
    context.close()


def test_an_ad_runs_before_a_video_and_the_creator_is_credited(browser, env):
    kit.forget_schema()
    db.forget()
    shop_context, shop = _account(
        browser, env, "shop@example.com"
    )  # the computer's first account
    creator_context, creator = _account(browser, env, "cara@example.com")
    viewer_context, _viewer = _account(browser, env, "vic@example.com")
    shop.call("POST", "/api/suite/business", {"name": "Spice Shop"})
    # the creator: a public video, creator terms, ads on
    film = record_webm(creator_context, 320, 180, 4.0)
    video_id = creator.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": upload(creator, "video", film),
            "title": "Cooking dal",
            "category": "food",
            "duration": 4,
            "width": 320,
            "height": 180,
        },
    )[1]["video"]["id"]
    assert creator.call("POST", "/api/videos/earnings/terms", {})[0] == 200
    assert creator.call("POST", "/api/videos/earnings/monetize", {"on": True})[0] == 200
    # the shop: a campaign with a pre-roll and a banner, approved and paid for by the computer's owner
    campaign = shop.call(
        "POST",
        "/api/ads/campaigns",
        {
            "name": "Autumn",
            "objective": "views",
            "bid_cents": 1000,
            "total_budget_cents": 2000,
            "daily_budget_cents": 2000,
            "landing_url": "https://spices.example",
        },
    )[1]["campaign"]
    ad_video = upload(
        shop, "video", record_webm(shop_context, 320, 180, 7.0), base="/api/ads"
    )
    preroll = shop.call(
        "POST",
        f"/api/ads/campaigns/{campaign['id']}/creatives",
        {
            "kind": "preroll",
            "headline": "Fresh spices",
            "file_id": ad_video,
            "cta": "Shop now",
        },
    )[1]["creative"]
    for creative_id in (preroll["id"],):
        assert (
            shop.call(
                "POST",
                f"/api/ads/admin/creatives/{creative_id}/review",
                {"decision": "approve"},
            )[0]
            == 200
        )
    assert (
        shop.call(
            "POST",
            "/api/ads/admin/payments",
            {"amount_cents": 2000, "reference": "cash"},
        )[0]
        == 201
    )
    assert (
        shop.call(
            "POST", f"/api/ads/campaigns/{campaign['id']}/status", {"status": "active"}
        )[1]["campaign"]["status"]
        == "active"
    )
    # the viewer opens the video: the ad plays first, can be skipped after five seconds, then the video plays
    problems: list[str] = []
    page = _watching_page(viewer_context, problems)
    go(page, env, f"videos?watch={video_id}")
    page.wait_for_selector(".vid-ad video")
    skip = page.locator(".vid-ad button:has-text('Skip ad')")
    assert (
        "Spice Shop" in page.locator(".vid-ad-bar").inner_text()
        and page.locator(".vid-ad a:has-text('Shop now')").count() == 1
    )
    until(
        page,
        lambda: page.locator(".vid-ad button", has_text="Skip ad in").count() == 1,
        timeout=15,
        what="the skip countdown",
    )
    until(
        page,
        lambda: (
            page.locator(".vid-ad button:not([disabled])", has_text="Skip ad").count()
            == 1
        ),
        timeout=20,
        what="the ad to become skippable",
    )
    skip.click()
    page.wait_for_selector(".vid-ad", state="detached")
    until(
        page,
        lambda: (
            "/api/videos/media/"
            in page.locator(".vid-player > video").evaluate("v => v.currentSrc")
        ),
        timeout=20,
        what="the video to start after the ad",
    )
    # the impression counted and paid the creator 55% of a cent
    until(
        page,
        lambda: creator.call("GET", "/api/videos/earnings")[1]["today_micros"] == 5_500,
        timeout=20,
        what="the creator to be credited",
    )
    report = shop.call("GET", "/api/ads/reports")[1]
    assert (
        report["totals"]["impressions"] == 1
        and report["totals"]["spend_micros"] == 10_000
    )
    assert not problems, problems
    # the creator sees it on their Earnings page and on Today
    creator_problems: list[str] = []
    creator_page = _watching_page(creator_context, creator_problems)
    go(creator_page, env, "videos?tab=earnings&part=ads")
    creator_page.wait_for_selector(".suite-kpi:has-text('Earned today')")
    assert (
        "$0.0055"
        in creator_page.locator(".suite-kpi", has_text="Earned today").inner_text()
    )
    assert "Cooking dal" in creator_page.locator("table").first.inner_text()
    go(creator_page, env, "today")
    creator_page.wait_for_selector("text=Earned today: $0.0055")
    # and the shop sees the numbers in Ads and the Business hub
    shop_problems: list[str] = []
    shop_page = _watching_page(shop_context, shop_problems)
    go(shop_page, env, "ads?tab=reports")
    shop_page.wait_for_selector(".suite-kpi:has-text('Impressions')")
    assert (
        "1"
        in shop_page.locator(".suite-kpi", has_text="Impressions").first.inner_text()
    )
    go(shop_page, env, "hub")
    shop_page.wait_for_selector("text=Ad spend this month")
    assert not creator_problems and not shop_problems, (creator_problems, shop_problems)
    for item in (shop_context, creator_context, viewer_context):
        item.close()


def test_the_platform_console_moderates_videos_and_runs_creators_and_ads(served):
    app, base = served
    kit.forget_schema()
    db.forget()
    studio = build_org(app, "Cara Studio", slug_email="cara.test")
    app.billing.subscribe_app(studio.owner, "videos")
    cara = sign_in(app, studio.owner)
    video = cara.call(
        "POST",
        "/api/videos/videos",
        {"file_id": upload(cara, "video", make_mp4()), "title": "Prank call"},
    )[1]["video"]
    sign_in(app, studio.coach).call(
        "POST",
        "/api/videos/reports",
        {"target_type": "video", "target_id": video["id"], "reason": "Spam or a scam"},
    )
    shop = build_org(app, "Spice Shop Ltd", slug_email="shop.test")
    app.billing.subscribe_app(shop.owner, "ads")
    owner = sign_in(app, shop.owner)
    campaign = owner.call(
        "POST",
        "/api/ads/campaigns",
        {
            "name": "Autumn",
            "objective": "views",
            "bid_cents": 1000,
            "total_budget_cents": 2000,
            "daily_budget_cents": 2000,
            "landing_url": "https://spices.example",
        },
    )[1]["campaign"]
    banner = owner.call(
        "POST",
        f"/api/ads/campaigns/{campaign['id']}/creatives",
        {
            "kind": "banner",
            "headline": "Fresh spices",
            "file_id": upload(owner, "image", PNG, base="/api/ads"),
        },
    )[1]["creative"]
    with patchright.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        try:
            context, page, problems = _open(
                chromium, base, operator_browser(app), "#/platform/videos"
            )
            row = page.locator("tr", has_text="Prank call").first
            row.wait_for(timeout=30000)
            row.locator("button", has_text="Hide").click()
            page.wait_for_selector("text=Hidden for everyone.")
            assert (
                cara.call("GET", f"/api/videos/videos/{video['id']}")[1]["video"][
                    "blocked"
                ]
                == "hidden"
            )
            page.goto(f"{base}/#/platform/creators")
            review = page.locator("tr", has_text="Fresh spices").first
            review.wait_for(timeout=30000)
            assert review.locator("img").count() == 1
            review.locator("button", has_text="Approve").click()
            page.wait_for_selector("text=Approved: it can run now.")
            assert (
                owner.call("GET", f"/api/ads/campaigns/{campaign['id']}")[1][
                    "campaign"
                ]["creatives"][0]["review_status"]
                == "approved"
            )
            page.locator("button", has_text="Share and minimum payout").click()
            page.wait_for_selector(".modal")
            page.get_by_label("Creators' share of each ad (percent, 0 to 90)").fill(
                "60"
            )
            page.locator(".modal .actions button", has_text="Save").click()
            page.wait_for_selector(".suite-kpi:has-text('60% share')")
            assert not [p for p in problems if "/api/auth/me" not in p], problems
            context.close()
        finally:
            chromium.close()
    actions = [row["action"] for row in app.platform_audit.list(limit=50)]
    assert {"videos.moderate", "ads.review_approved", "ads.settings"} <= set(actions)
    del banner


@pytest.mark.parametrize("width", [390, 1280])
def test_videos_and_ads_fit_a_phone_and_easy_mode(browser, env, width):
    kit.forget_schema()
    db.forget()
    context, page, problems = signed_in_page(browser, env, width=width, height=820)
    me = Requester(context, env.base)
    file_id = upload(me, "video", record_webm(context, 320, 180, 1.5))
    video = me.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": file_id,
            "title": "A rather long title for a video about cooking dal at home",
            "duration": 1.5,
            "width": 320,
            "height": 180,
        },
    )[1]["video"]
    for path, ready in (
        ("videos", ".vid-card"),
        (f"videos?watch={video['id']}", ".vid-player"),
        ("videos?tab=studio", "text=Upload a video"),
        ("videos?tab=earnings", "text=Ways to earn"),
        ("videos?tab=earnings&part=ads", "text=Start earning"),
        ("videos?tab=earnings&part=memberships", "text=Your membership"),
        ("videos?tab=earnings&part=deals", "text=No brand deals yet"),
        ("ads", "text=How Ads works"),
        ("ads?tab=admin", "text=Creators' share and payouts"),
    ):
        go(page, env, path)
        page.wait_for_selector(ready)
        page.evaluate("document.body.classList.add('easy', 'text-xlarge')")
        page.wait_for_timeout(200)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 1, (path, width, overflow)
    assert not problems, problems
    context.close()
