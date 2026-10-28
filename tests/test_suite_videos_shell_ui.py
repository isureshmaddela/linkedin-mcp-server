"""Videos in the Aira frame, in a real browser: "+ New" opens the app's create forms (an upload set to a video, a reel
or a short film, a playlist, a brand deal), the search box across the top finds videos, your own private ones and
your playlists (and Enter lists them on Home), the tabs are folders in the left menu, and the pages fit a phone in
light and dark."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from aira.suite.videos import db  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_videos_ui import Requester  # noqa: E402
from videos_support import make_mp4, upload  # noqa: E402


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    db.forget()
    public.set_index(local._public_link_index)


def seeded(browser, env, **size):
    """Two videos everyone can find, a private one and a playlist, all on the signed-in person's channel."""
    context, page, problems = signed_in_page(browser, env, **size)
    person = Requester(context, env.base)
    made = {}
    for title, extra in (
        ("Garden walk in spring", {}),
        ("Harbour at dusk", {"tags": "sea, boats"}),
        ("Private garden notes", {"visibility": "private"}),
    ):
        file_id = upload(person, "video", make_mp4(300, 1280, 720), name="clip.mp4")
        status, body = person.call(
            "POST",
            "/api/videos/videos",
            {"file_id": file_id, "kind": "video", "title": title, **extra},
        )
        assert status == 201, body
        made[title] = body["video"]
    status, body = person.call(
        "POST", "/api/videos/playlists", {"name": "Garden ideas"}
    )
    assert status == 201, body
    made["playlist"] = body["playlist"]
    return context, page, problems, made


def open_app(page, env, hash_="videos"):
    page.goto(f"{env.base}/videos#/{hash_}")
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_load_state("networkidle")


def menu_items(page):
    return [
        item.strip()
        for item in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ]


def pick_new(page, label):
    page.click(".shell-new")
    page.locator(".shell-new-menu [role='menuitem']", has_text=label).click()


def options(page):
    return page.locator("#appSearchList [role='option']").all_inner_texts()


def test_new_search_and_folders_in_the_frame(browser, env):
    context, page, problems, made = seeded(browser, env)
    open_app(page, env)
    # The top bar says only Monosage; the app is named for screen readers and in its menu below.
    assert page.locator("#appBrand").inner_text().strip() == "Monosage"
    assert page.locator("#appBrand").get_attribute("aria-label") == "Monosage Videos: home"
    # The page is named after its tab, not the app again.
    assert page.locator("#main .page-head h1").inner_text().strip() == "Home"

    # "+ New": the app's main creates, each opening its form; the address forgets the ask.
    page.click(".shell-new")
    assert menu_items(page) == [
        "Upload a video",
        "Upload a reel",
        "Upload a short film",
        "New playlist",
        "New brand deal",
    ]
    page.keyboard.press("Escape")
    pick_new(page, "Upload a reel")
    page.wait_for_selector("#vidUpload .suite-h2:has-text('Upload a reel')")
    until(
        page,
        lambda: "tab=studio" in page.url and "new=" not in page.url,
        what="Studio without new=",
    )
    assert page.locator("select[name=kind]").input_value() == "reel"
    until(
        page,
        lambda: page.evaluate(
            "() => document.activeElement && document.activeElement.getAttribute('aria-label') === 'Choose a video'"
        ),
        what="the file chooser to take the focus",
    )
    pick_new(page, "Upload a short film")
    page.wait_for_selector("#vidUpload .suite-h2:has-text('Upload a short film')")
    assert page.locator("select[name=kind]").input_value() == "short"
    pick_new(page, "New playlist")
    page.wait_for_selector(".modal h2:has-text('New playlist')")
    until(
        page,
        lambda: "tab=library" in page.url and "new=" not in page.url,
        what="the Library without new=",
    )
    page.fill(".modal input[name=name]", "Weekend")
    page.locator(".modal button:has-text('Create playlist')").click()
    page.wait_for_selector("#main h1:has-text('Weekend')")
    assert "playlist=" in page.url
    pick_new(page, "New brand deal")
    page.wait_for_selector(".modal h2:has-text('New brand deal')")
    until(
        page,
        lambda: (
            "tab=earnings" in page.url
            and "part=deals" in page.url
            and "new=" not in page.url
        ),
        what="Brand deals without new=",
    )
    page.keyboard.press("Escape")
    # Back does not open the form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert page.locator(".modal").count() == 0

    # The search box: the app's records as you type; choosing one opens it.
    box = page.locator("#appSearchInput")
    assert box.get_attribute("placeholder") == "Search videos"
    box.click()
    box.fill("garden")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Garden ideas')")
    found = options(page)
    assert any("Garden walk in spring" in text for text in found), found
    assert any(
        "Private garden notes" in text and "Only me" in text for text in found
    ), found
    assert any("Garden ideas" in text and "Playlist" in text for text in found), found
    assert not any("Harbour" in text for text in found), found
    page.locator(
        "#appSearchList [role='option']", has_text="Garden walk in spring"
    ).click()
    page.wait_for_selector("#main h1:has-text('Garden walk in spring')")
    assert f"watch={made['Garden walk in spring']['id']}" in page.url
    box.fill("boats")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Harbour at dusk')")
    box.fill("garden ideas")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Playlist')")
    page.locator("#appSearchList [role='option']", has_text="Playlist").click()
    page.wait_for_selector("#main h1:has-text('Garden ideas')")
    assert f"playlist={made['playlist']['id']}" in page.url

    # Enter: every match on Home, the person's own private one apart.
    box.fill("garden")
    box.press("Enter")
    page.wait_for_selector("#main h1:has-text('Results for')")
    assert "tab=home" in page.url and "q=garden" in page.url
    page.wait_for_selector("#main h2:has-text('Your videos only you can find')")
    assert (
        page.locator("#main .vid-card", has_text="Garden walk in spring").count() == 1
    )
    assert page.locator("#main .vid-card", has_text="Private garden notes").count() == 1
    page.locator("#main .vid-card", has_text="Private garden notes").click()
    page.wait_for_selector("#main h1:has-text('Private garden notes')")
    box.fill("nothing-like-this")
    box.press("Enter")
    page.wait_for_selector("#main .empty:has-text('Nothing in Videos matches')")

    # The left menu: the tabs as folders with real icons; a folder opens its tab.
    open_app(page, env, "videos?tab=home")
    folders = page.locator(".shell-subnav[data-section='videos'] .shell-sublink")
    names = [name.strip() for name in folders.all_inner_texts()]
    assert names[:7] == [
        "Home",
        "Reels",
        "Short films",
        "Library",
        "Studio",
        "Analytics",
        "Earnings",
    ], names
    assert page.locator(".shell-subnav .shell-sublink .shell-subdot").count() == 0
    folders.filter(has_text="Library").click()
    page.wait_for_selector("#main h1:has-text('Library')")
    assert "tab=library" in page.url
    assert folders.filter(has_text="Library").get_attribute("aria-current") == "page"
    folders.filter(has_text="Short films").click()
    page.wait_for_selector("#main h1:has-text('Short films')")
    # On a wide screen the menu's folders and "+ New" stand in for the page's own tab bar and Upload.
    assert not page.locator("#main .vid-page > .tabs").is_visible()
    assert not page.locator("#main .vid-phone-new").is_visible()
    # A watch page keeps the folders, none of them marked.
    open_app(page, env, f"videos?watch={made['Harbour at dusk']['id']}")
    assert folders.count() >= 7
    assert folders.filter(has_text="Home").get_attribute("aria-current") == "false"
    assert not problems, problems
    context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_videos_fits_a_phone(browser, env, dark):
    context, page, problems, made = seeded(
        browser, env, width=390, height=844, dark=dark
    )
    for hash_ in (
        "videos?tab=home",
        "videos?tab=reels",
        "videos?tab=films",
        "videos?tab=library",
        "videos?tab=studio",
        "videos?tab=analytics",
        "videos?tab=earnings",
        "videos?tab=home&q=garden",
        f"videos?watch={made['Garden walk in spring']['id']}",
        f"videos?playlist={made['playlist']['id']}",
    ):
        open_app(page, env, hash_)
        page.wait_for_timeout(300)
        wide = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert wide <= 1, (hash_, wide)
    # A phone keeps the page's own tab bar and Upload (the menu is a sheet); Upload opens the upload form.
    open_app(page, env, "videos?tab=home")
    assert page.locator("#main .vid-page > .tabs").is_visible()
    assert page.locator("#appSearchInput").get_attribute("placeholder") == "Search"
    page.locator("#main .vid-phone-new").click()
    page.wait_for_selector("#vidUpload")
    until(
        page, lambda: "tab=studio" in page.url and "new=" not in page.url, what="Studio"
    )
    assert not problems, problems
    context.close()
