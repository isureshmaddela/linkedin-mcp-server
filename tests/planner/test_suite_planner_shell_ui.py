"""Planner in the Aira frame. The search behind the box across the top (who sees what), then in a real browser:
"+ New" offers a task, a shopping item, a meal, a note and something to sell and opens their forms; the search
suggests chores, shopping, recipes and notes and opens the one chosen; Enter lists every match; the tabs are
folders in the left menu and the page is named after the open one. No script errors along the way."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

from aira.suite.planner import goals, meals, search, selling, service
from test_suite_planner import Req, db, family  # noqa: F401

pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_planner_ui import call, local_index, real_problems  # noqa: E402, F401

NEW_ITEMS = [
    "New task",
    "Add to the shopping list",
    "Plan a meal",
    "New note",
    "Sell something",
]
FOLDERS = [
    "My day",
    "Chores",
    "Shopping",
    "Meals",
    "Budget",
    "Savings",
    "Bills",
    "Sell",
    "Notes",
    "Rewards",
]


# ---------------------------------------------------------------------------
# the search itself
# ---------------------------------------------------------------------------


def test_search_finds_every_kind_and_only_what_the_person_may_see(db, family):
    parent, kid = family["parent"], family["kid"]
    service.create_task(db, parent, {"title": "Water the garden", "repeat": "daily"})
    service.create_task(
        db, parent, {"title": "Garden fence call", "assignee": family["teen"]["id"]}
    )
    shopping = service.lists(db)[0]
    service.add_item(db, parent["id"], shopping["id"], {"name": "Garden gloves"})
    meals.save_recipe(
        db, parent, {"title": "Soup", "ingredients": ["Garden peas", "Stock"]}
    )
    service.save_note(db, parent, {"title": "Garden plan", "shared": True})
    service.save_note(db, parent, {"title": "Garden gift ideas", "shared": False})
    service.save_bill(db, parent, {"name": "Garden water", "amount_cents": 1200})
    selling.save_item(db, parent, {"title": "Garden bench", "price_cents": 3000})
    goals.save_goal(db, parent, {"title": "Garden shed", "target_cents": 50000})

    mine = search.find(db, parent, "garden")
    assert {t["title"] for t in mine["tasks"]} == {
        "Water the garden",
        "Garden fence call",
    }
    assert [i["name"] for i in mine["items"]] == ["Garden gloves"]
    assert mine["items"][0]["list_name"] == shopping["name"]
    assert [r["title"] for r in mine["recipes"]] == ["Soup"]  # by an ingredient
    assert {n["title"] for n in mine["notes"]} == {"Garden plan", "Garden gift ideas"}
    assert [b["name"] for b in mine["bills"]] == ["Garden water"]
    assert [s["title"] for s in mine["sell"]] == ["Garden bench"]
    assert [g["title"] for g in mine["goals"]] == ["Garden shed"]

    # A repeating chore shows once, by its next turn, after it is done.
    daily = next(t for t in mine["tasks"] if t["title"] == "Water the garden")
    service.complete(db, parent, daily["id"])
    again = search.find(db, parent, "water the")
    assert len(again["tasks"]) == 1 and not again["tasks"][0]["done"]

    # A child: their own and shared things, never money, the sale or someone's private note or chore.
    child = search.find(db, kid, "garden")
    assert [t["title"] for t in child["tasks"]] == ["Water the garden"]
    assert [n["title"] for n in child["notes"]] == ["Garden plan"]
    assert child["bills"] == child["sell"] == child["goals"] == []
    assert [i["name"] for i in child["items"]] == ["Garden gloves"]

    # Nothing typed finds nothing; a wildcard is a plain character.
    assert search.find(db, parent, "")["tasks"] == []
    assert search.find(db, parent, "%")["tasks"] == []
    assert len(search.find(db, parent, "garden", limit=1)["notes"]) == 1


# ---------------------------------------------------------------------------
# in a real browser
# ---------------------------------------------------------------------------


def open_planner(page, env, hash_="planner"):
    page.goto(f"{env.base}/planner#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .planner .page-head h1, #main .page-head h1")
    page.wait_for_selector("#main .planner")


def type_search(page, text):
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)
    # The first row is "Search Planner for “...”": wait for this search's list, not the last one's.
    until(
        page,
        lambda: (
            text
            in (
                page.locator(
                    "#appSearchList:not([hidden]) .shell-search-option"
                ).first.inner_text(timeout=200)
                if page.locator(
                    "#appSearchList:not([hidden]) .shell-search-option"
                ).count()
                else ""
            )
        ),
        what=f"suggestions for {text}",
    )


def choose_new(page, label):
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")


def close_modal(page):
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")


def seed(context, env):
    call(context, env, "POST", "/api/planner/tasks", {"title": "Take the bins out"})
    lists = call(context, env, "GET", "/api/planner/lists")["lists"]
    call(
        context,
        env,
        "POST",
        f"/api/planner/lists/{lists[0]['id']}/items",
        {"name": "Milk", "quantity": "2"},
    )
    call(
        context,
        env,
        "POST",
        "/api/planner/recipes",
        {"title": "Pancakes", "ingredients": ["2 eggs", "300 ml milk", "Flour"]},
    )
    call(
        context,
        env,
        "POST",
        "/api/planner/notes",
        {"title": "Wifi password", "body": "On the router", "shared": True},
    )
    call(
        context,
        env,
        "POST",
        "/api/planner/bills",
        {"name": "Electricity", "amount_cents": 6500, "repeat": "monthly"},
    )


def test_planner_new_search_and_folders_in_the_frame(browser, env):
    context, page, problems = signed_in_page(browser, env)
    seed(context, env)

    # The address names the app and the page head names the open folder, not the app again.
    open_planner(page, env)
    assert "/planner#/planner" in page.url
    assert page.locator("#main .page-head h1").inner_text() == "My day"

    # The left menu shows the tabs as folders with icons; a folder opens its tab, and the bar under the title (the
    # same list again) is folded away on a wide screen.
    folders = page.locator("#primaryNav .shell-sublink")
    assert [t.strip() for t in folders.all_inner_texts()] == FOLDERS
    assert page.locator("#primaryNav .shell-sublink .shell-subdot").count() == 0
    assert not page.locator("#main .planner > .tabs").is_visible()
    page.click("#primaryNav .shell-sublink[data-tab='bills']")
    until(page, lambda: "tab=bills" in page.url, what="the Bills folder")
    page.wait_for_selector("#main .page-head h1:has-text('Bills')")
    page.wait_for_selector(".life-card:has-text('Electricity')")
    current = page.locator("#primaryNav .shell-sublink[aria-current='page']")
    assert current.inner_text().strip() == "Bills"

    # "+ New" lists the app's creates; each opens its form, and the address forgets ?new= at once.
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    items = page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    assert [t.strip() for t in items] == NEW_ITEMS
    page.keyboard.press("Escape")

    choose_new(page, "New task")
    page.wait_for_selector(".modal:has-text('New task') [name=title]")
    assert "tab=chores" in page.url and "new=" not in page.url
    page.fill(".modal [name=title]", "Feed the fish")
    page.locator(".modal .actions .btn.primary").click()
    page.wait_for_selector(".modal", state="detached")
    page.wait_for_selector(".life-line:has-text('Feed the fish')")

    choose_new(page, "Add to the shopping list")
    until(page, lambda: "tab=shopping" in page.url and "new=" not in page.url)
    until(
        page,
        lambda: page.evaluate(
            "() => document.activeElement && document.activeElement.getAttribute('aria-label') === 'Item'"
        ),
        what="the shopping list's add box to have focus",
    )
    page.keyboard.type("Butter")
    page.keyboard.press("Enter")
    page.wait_for_selector(".life-line:has-text('Butter')")

    choose_new(page, "Plan a meal")
    page.wait_for_selector(".modal:has-text('Plan a meal') [name=day]")
    assert "tab=meals" in page.url and "new=" not in page.url
    close_modal(page)

    choose_new(page, "New note")
    page.wait_for_selector(".modal:has-text('New note') [name=body]")
    assert "tab=notes" in page.url
    close_modal(page)

    choose_new(page, "Sell something")
    page.wait_for_selector(".modal:has-text('Sell something') [name=title]")
    assert "tab=sell" in page.url and "new=" not in page.url
    close_modal(page)
    # Back does not open a form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # The search across the top: chores, shopping, recipes and notes, each opening itself.
    open_planner(page, env, "planner?tab=rewards")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search chores, shopping, recipes and notes"
    )
    type_search(page, "bins")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Take the bins out')"
    ).click()
    page.wait_for_selector(".planner-found:has-text('Take the bins out')")
    assert "tab=chores" in page.url and "task=" not in page.url
    assert (
        page.locator("#main input[aria-label='Search tasks']").input_value()
        == "Take the bins out"
    )

    type_search(page, "milk")
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any("Milk (2)" in t and "To buy" in t for t in texts), texts
    assert any("Pancakes" in t and "Recipe" in t for t in texts), texts
    page.locator("#appSearchList .shell-search-option:has-text('Pancakes')").click()
    page.wait_for_selector(".planner-found:has-text('Pancakes')")
    assert "tab=meals" in page.url and "recipe=" not in page.url

    type_search(page, "router")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Wifi password')"
    ).click()
    page.wait_for_selector(".life-card.planner-found:has-text('Wifi password')")
    assert "tab=notes" in page.url and "note=" not in page.url

    # Enter lists every match, kind by kind; a result opens its record.
    type_search(page, "milk")
    page.keyboard.press("Enter")
    until(
        page,
        lambda: "tab=search" in page.url and "q=milk" in page.url,
        what="the results",
    )
    page.wait_for_selector("#main .page-head h1:has-text('Search results')")
    page.wait_for_selector(".suite-section:has-text('Shopping (1)') >> text=Milk (2)")
    page.wait_for_selector(".suite-section:has-text('Recipes (1)') >> text=Pancakes")
    page.click(".planner-hit:has-text('Milk (2)')")
    page.wait_for_selector(".planner-found:has-text('Milk')")
    assert "tab=shopping" in page.url

    assert real_problems(problems) == []


def test_planner_in_the_frame_on_a_phone_fits_and_keeps_its_creates(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    # The menu (and its "+ New") is a sheet on a phone, so the tab bar stays and the tabs keep their own buttons.
    for hash_ in (
        "planner",
        "planner?tab=chores",
        "planner?tab=sell",
        "planner?tab=search&q=milk",
    ):
        open_planner(page, env, hash_)
        page.wait_for_timeout(300)
        assert page.locator("#main .planner > .tabs").is_visible(), hash_
        assert not page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 2"
        ), hash_
    open_planner(page, env, "planner?tab=chores")
    assert page.locator("#main button:has-text('New task')").first.is_visible()
    # An empty tab says what to do next, with the button to do it.
    open_planner(page, env, "planner?tab=savings")
    page.wait_for_selector(".empty button:has-text('Save for something')")
    assert real_problems(problems) == []
