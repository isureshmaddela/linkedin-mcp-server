"""Marketing in the Aira frame (shell.js): "+ New" offers the app's main creates and opens their forms, the search box
across the top finds the app's records (and saved sales leads) and opens one, Enter shows every match, and the left
menu's folders under Marketing and Leads open their tabs. Also the search the box runs on (service.search)."""

# ruff: noqa: F811
from __future__ import annotations

import re
import sqlite3

import pytest

from aira.suite import kit, public
from aira.suite.marketing import service, store

# ---------------------------------------------------------------------------
# service.search
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "marketing.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


def test_search_finds_each_kind_of_record_shaped_like_its_list(db):
    campaign = service.create_campaign(db, {"name": "Autumn colour launch"})
    service.create_post(db, {"channel": "instagram", "title": "Autumn looks"})
    service.create_post(db, {"channel": "x", "title": "Spring sale"})
    form = service.create_form(db, {"title": "Autumn consult"})
    service.create_link(
        db, {"destination": "https://glow.example/autumn", "title": "Bio booking"}
    )
    service.add_subscriber(db, {"email": "ana@example.com", "name": "Ana Autumn"})
    service.create_newsletter(db, {"subject": "Autumn news", "body": "Hello"})
    service.submit(
        db,
        form["id"],
        {"name": "Bea", "email": "bea@example.com", "message": "An autumn booking"},
    )

    found = service.search(db, "autumn")
    assert [c["id"] for c in found["campaigns"]] == [campaign["id"]]
    assert "results" in found["campaigns"][0]  # the campaign drawer reads its results
    assert [p["title"] for p in found["posts"]] == ["Autumn looks"]
    assert [f["title"] for f in found["forms"]] == ["Autumn consult"]
    assert "conversion_percent" in found["forms"][0]
    assert [link["title"] for link in found["links"]] == ["Bio booking"]
    assert "final_url" in found["links"][0]
    assert [s["email"] for s in found["subscribers"]] == ["ana@example.com"]
    assert [n["subject"] for n in found["newsletters"]] == ["Autumn news"]
    assert "remaining" in found["newsletters"][0]
    assert [s["name"] for s in found["submissions"]] == ["Bea"]
    assert found["submissions"][0]["form_title"] == "Autumn consult"

    assert all(rows == [] for rows in service.search(db, "  ").values())
    for index in range(4):
        service.create_post(db, {"channel": "blog", "title": f"Autumn idea {index}"})
    assert len(service.search(db, "autumn", limit=2)["posts"]) == 2
    assert (
        len(service.search(db, "autumn", limit=500)["posts"]) == 5
    )  # capped at 25, five exist


# ---------------------------------------------------------------------------
# the frame in a real browser
# ---------------------------------------------------------------------------

patchright = pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_marketing_ui import call, local_index  # noqa: E402, F401


def _wait_loaded(page):
    until(
        page,
        lambda: page.locator("#main .skeleton-list").count() == 0,
        what="the page to load",
    )


def test_new_search_and_folders_work_in_the_frame(browser, env):
    context, page, problems = signed_in_page(browser, env)
    call(
        context,
        env,
        "POST",
        "/api/marketing/campaigns",
        {"name": "Autumn colour launch", "goal": "40 bookings"},
    )
    call(
        context,
        env,
        "POST",
        "/api/marketing/posts",
        {"channel": "instagram", "title": "Autumn looks", "status": "draft"},
    )
    call(
        context,
        env,
        "POST",
        "/api/marketing/subscribers",
        {"email": "ana@example.com", "name": "Ana Autumn"},
    )
    call(
        context,
        env,
        "POST",
        "/api/leads/items",
        {
            "leads": [
                {
                    "name": "Autumn Dental",
                    "kind": "company",
                    "company": "Autumn Dental",
                    "source": "manual",
                }
            ]
        },
    )
    assert (
        call(context, env, "GET", "/api/marketing/search?q=autumn")["results"][
            "campaigns"
        ][0]["name"]
        == "Autumn colour launch"
    )

    go(page, env, "marketing")
    page.wait_for_selector(".page-head h1")
    _wait_loaded(page)
    assert page.locator(".page-head h1").inner_text() == "Overview"
    assert "/marketing" in page.url

    # + New: the app's creates; one opens the same form as the button on its tab, and leaves the address clean.
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    labels = [
        t.strip()
        for t in page.locator(".shell-new-menu [role=menuitem]").all_inner_texts()
    ]
    assert labels == ["Post", "Campaign", "Lead form", "Newsletter", "Find leads"]
    page.locator(".shell-new-menu [role=menuitem]", has_text="Campaign").click()
    page.wait_for_selector(".modal input[name=name]")
    assert "New campaign" in page.locator(".modal").inner_text()
    assert "new=" not in page.url and "tab=campaigns" in page.url
    assert page.locator(".page-head h1").inner_text() == "Campaigns"
    page.fill(".modal input[name=name]", "Winter push")
    page.locator(".modal .actions button", has_text="Save").click()
    page.wait_for_selector("#main td strong:text('Winter push')")
    page.go_back()  # Back does not open the form again
    page.wait_for_timeout(500)
    assert page.locator(".modal input[name=name]").count() == 0

    page.click(".shell-new")
    page.locator(".shell-new-menu [role=menuitem]", has_text="Post").click()
    page.wait_for_selector(".modal select[name=channel]")
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    page.click(".shell-new")
    page.locator(".shell-new-menu [role=menuitem]", has_text="Find leads").click()
    page.wait_for_url(re.compile(r"/marketing#/leads\?tab=find$"))
    page.wait_for_selector("#ldSearchForm")
    assert page.locator(".ld-head h1").inner_text() == "Find leads"

    # The search box: the app's own records, each opening where it lives.
    go(page, env, "marketing")
    _wait_loaded(page)
    page.click("#appSearchInput")
    page.keyboard.type("autumn")
    page.wait_for_selector(
        "#appSearchList [role=option]:has-text('Autumn colour launch')"
    )
    options = page.locator("#appSearchList [role=option]").all_inner_texts()
    assert any("Autumn Dental" in o and "Sales lead" in o for o in options), options
    assert any("Ana Autumn" in o for o in options), options
    assert any("Autumn looks" in o for o in options), options
    page.locator(
        "#appSearchList [role=option]", has_text="Autumn colour launch"
    ).click()
    page.wait_for_selector(".drawer h2")
    assert page.locator(".drawer h2").inner_text() == "Autumn colour launch"
    assert "tab=campaigns" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    page.click("#appSearchInput")
    page.fill("#appSearchInput", "")
    page.keyboard.type("dental")
    page.wait_for_selector("#appSearchList [role=option]:has-text('Autumn Dental')")
    page.locator("#appSearchList [role=option]", has_text="Autumn Dental").click()
    page.wait_for_selector(".drawer h2:text('Autumn Dental')")
    assert "#/leads?tab=leads" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    # Enter: every match on a results page; a row opens its record there.
    go(page, env, "marketing?tab=calendar")
    _wait_loaded(page)
    page.click("#appSearchInput")
    page.keyboard.type("autumn")
    page.keyboard.press("Enter")
    page.wait_for_selector(".page-head h1:has-text('Results for')")
    _wait_loaded(page)
    text = page.locator("#main").inner_text()
    for expected in (
        "Subscribers (1)",
        "Posts (1)",
        "Campaigns (1)",
        "Sales leads (1)",
        "Ana Autumn",
        "Autumn Dental",
    ):
        assert expected in text, expected
    page.locator("#main tr.suite-row", has_text="Ana Autumn").click()
    page.wait_for_selector(".drawer h2:text('Ana Autumn')")
    page.keyboard.press("Escape")

    # Enter on the Leads page filters the saved leads.
    go(page, env, "leads?tab=leads")
    page.wait_for_selector("#ldFilterText")
    page.click("#appSearchInput")
    page.keyboard.type("dental")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"tab=leads&q=dental"))
    page.wait_for_selector("#ldFilterText")
    assert page.input_value("#ldFilterText") == "dental"
    page.wait_for_selector(".ld-table button.ld-open:has-text('Autumn Dental')")

    # The left menu's folders open their tabs, under Marketing and under Leads.
    go(page, env, "marketing")
    _wait_loaded(page)
    folders = page.locator(
        "#primaryNav .shell-subnav[data-section=marketing] .shell-sublink"
    )
    assert folders.count() == 9
    assert (
        page.locator(
            "#primaryNav .shell-subnav[data-section=marketing] a[data-tab=links] svg"
        ).count()
        == 1
    )
    page.click("#primaryNav .shell-subnav[data-section=marketing] a[data-tab=links]")
    page.wait_for_selector(".page-head h1:text('Links')")
    assert (
        page.locator(
            "#primaryNav .shell-subnav[data-section=marketing] a[data-tab=links]"
        ).get_attribute("aria-current")
        == "page"
    )
    page.click("#primaryNav a[data-section=leads]")
    page.wait_for_selector(
        "#primaryNav .shell-subnav[data-section=leads] a[data-tab=history]"
    )
    page.click("#primaryNav .shell-subnav[data-section=leads] a[data-tab=history]")
    page.wait_for_selector(".ld-head h1:text('Search history')")
    assert "tab=history" in page.url

    assert not problems, problems
    context.close()


def test_a_child_profile_is_offered_nothing_to_create(browser, env):
    """Marketing refuses a child or teen profile (access.require_grown_up): "+ New" offers it nothing."""
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "marketing")
    page.wait_for_selector(".shell-new")
    page.evaluate(
        "() => { window.Aira.life.me = { age_group: 'teen' }; window.Aira.renderNav(); }",
        isolated_context=False,
    )
    until(
        page,
        lambda: page.locator(".shell-new").count() == 0,
        what="+ New to go away for a teen",
    )
    assert not problems, problems
    context.close()
