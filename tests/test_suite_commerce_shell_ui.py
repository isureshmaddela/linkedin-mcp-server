"""Commerce in the Aira frame, in a real browser: "+ New" opens the app's create forms, the search box across the top
finds orders, products, bookings and customers (and Enter lists them), the tabs are folders in the left menu, and
the pages fit a phone."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402

WEEK = [{"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)]


@pytest.fixture(autouse=True)
def no_model():
    """Never call the real writing model (a live Ollama may run on this machine)."""
    from aira import suite

    class NoModel:
        def model(self, prompt):
            return None

    previous = suite.gateway()
    suite.set_gateway(NoModel())
    yield
    suite.set_gateway(previous)


def seeded(browser, env, **size):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env, **size)
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }

    def post(path, body):
        response = context.request.post(
            env.base + path, data=json.dumps(body), headers=headers
        )
        assert response.status in (200, 201), response.text()
        return response.json()

    post("/api/suite/business", {"name": "Clay and Comb", "timezone": "UTC"})
    mug = post(
        "/api/shop/products",
        {"name": "Speckled mug", "price_cents": 2200, "status": "active"},
    )["product"]
    order = post(
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ana Silva",
            "email": "ana@example.com",
        },
    )["order"]
    post("/api/bookings/staff", {"name": "Maya", "hours": WEEK})
    cut = post(
        "/api/bookings/services",
        {"name": "Haircut", "minutes": 45, "price_cents": 3500},
    )["service"]
    found = context.request.get(
        f"{env.base}/api/bookings/slots?service_id={cut['id']}&days=7", headers=headers
    ).json()
    start = [slot["start"] for day in found["days"] for slot in day["slots"]][1]
    booking = post(
        "/api/bookings/bookings",
        {
            "service_id": cut["id"],
            "start": start,
            "name": "Chloe Silva",
            "email": "chloe@example.com",
        },
    )["booking"]
    return context, page, problems, {"mug": mug, "order": order, "booking": booking}


def open_app(page, env, hash_="shop"):
    page.goto(f"{env.base}/commerce#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .page-head h1")


def test_new_search_and_folders_in_the_frame(browser, env):
    context, page, problems, made = seeded(browser, env)
    open_app(page, env)
    # The top bar says only Monosage; the app is named for screen readers and in its menu below.
    assert page.locator("#appBrand").inner_text().strip() == "Monosage"
    assert page.locator("#appBrand").get_attribute("aria-label") == "Monosage Commerce: home"

    # "+ New": the app's main creates, each opening its form; the address forgets the request.
    page.click(".shell-new")
    offered = [
        item.strip()
        for item in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ]
    assert offered == [
        "New order",
        "New product",
        "New discount code",
        "New booking",
        "New service",
    ]
    page.locator(".shell-new-menu [role='menuitem']", has_text="New product").click()
    page.wait_for_selector(".drawer h2:has-text('New product')")
    until(
        page,
        lambda: "new=" not in page.url and "tab=products" in page.url,
        what="the address to drop new=",
    )
    page.keyboard.press("Escape")
    page.click(".shell-new")
    page.locator(
        ".shell-new-menu [role='menuitem']", has_text="New discount code"
    ).click()
    page.wait_for_selector(".modal h2:has-text('New discount code')")
    page.keyboard.press("Escape")
    page.click(".shell-new")
    page.locator(".shell-new-menu [role='menuitem']", has_text="New booking").click()
    page.wait_for_selector(".modal h2:has-text('New booking')")
    page.keyboard.press("Escape")
    until(
        page,
        lambda: "new=" not in page.url and "tab=appointments" in page.url,
        what="the address to drop new=",
    )
    # Back does not open the form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # The search box: records of every kind as you type; choosing one opens it.
    box = page.locator("#appSearchInput")
    assert "orders" in (box.get_attribute("placeholder") or "")
    box.click()
    box.fill("silva")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Order #')")
    options = page.locator("#appSearchList [role='option']").all_inner_texts()
    assert any("Ana Silva" in text for text in options), options
    assert any(
        "Chloe Silva" in text and made["booking"]["reference"] in text
        for text in options
    ), options
    page.locator(
        "#appSearchList [role='option']", has_text=f"Order #{made['order']['number']}"
    ).click()
    page.wait_for_selector(f".drawer h2:has-text('Order #{made['order']['number']}')")
    until(page, lambda: "order=" not in page.url, what="the address to drop order=")
    page.keyboard.press("Escape")
    box.fill("chloe")
    page.wait_for_selector("#appSearchList [role='option']:has-text('Haircut')")
    page.locator("#appSearchList [role='option']", has_text="Booking").first.click()
    page.wait_for_selector(".drawer h2:has-text('Chloe Silva')")
    assert "#/bookings" in page.url
    page.keyboard.press("Escape")

    # Enter: every match on one page.
    box.fill("silva")
    box.press("Enter")
    page.wait_for_selector("#main h1:has-text('Results for')")
    page.wait_for_selector("#main h2:has-text('Orders (1)')")
    assert page.locator("#main h2:has-text('Bookings (1)')").count() == 1
    assert page.locator("#main h2:has-text('Booking customers (1)')").count() == 1
    assert (
        page.locator("#main tr.suite-row", has_text="Speckled").count() == 0
    )  # no product matches "silva"
    page.locator("#main tr.suite-row", has_text="Chloe Silva").first.click()
    page.wait_for_selector(".drawer h2:has-text('Chloe Silva')")
    page.keyboard.press("Escape")
    box.fill("nothing-like-this")
    box.press("Enter")
    page.wait_for_selector("#main .empty:has-text('Nothing in Commerce matches')")

    # The left menu: each section's tabs as folders, with icons; a folder opens its tab.
    shop_folders = page.locator(".shell-subnav[data-section='shop'] .shell-sublink")
    booking_folders = page.locator(
        ".shell-subnav[data-section='bookings'] .shell-sublink"
    )
    assert [t.strip() for t in shop_folders.all_inner_texts()][:3] == [
        "Overview",
        "Orders",
        "Products",
    ]
    assert "Team pay" in [t.strip() for t in booking_folders.all_inner_texts()]
    assert (
        page.locator(".shell-subnav .shell-sublink .shell-subdot").count() == 0
    )  # every folder has a real icon
    shop_folders.filter(has_text="Products").click()
    page.wait_for_selector("#main tr.suite-row:has-text('Speckled mug')")
    assert "tab=products" in page.url
    assert (
        shop_folders.filter(has_text="Products").get_attribute("aria-current") == "page"
    )
    # Only the open section's folders show (like Gmail); open Bookings to reach its folders.
    assert page.locator(".shell-subnav[data-section='bookings']").is_hidden()
    page.click("#primaryNav a[data-section='bookings']")
    page.wait_for_selector(".shell-subnav[data-section='bookings'] .shell-sublink")
    assert page.locator(".shell-subnav[data-section='shop']").is_hidden()
    booking_folders.filter(has_text="Services").click()
    page.wait_for_selector("#main tr.suite-row:has-text('Haircut')")
    assert "#/bookings?tab=services" in page.url
    # The search for one record by its address opens it once, then the address is plain again.
    page.goto(f"{env.base}/commerce#/bookings?tab=services&new=service")
    page.wait_for_selector(".modal h2:has-text('New service')")
    until(page, lambda: "new=" not in page.url, what="the address to drop new=")
    page.keyboard.press("Escape")
    # A teen's household profile is refused by every Commerce route: "+ New" offers nothing.
    offered = page.evaluate(
        "() => { const life = window.Aira.life; const was = life.me; life.me = { age_group: 'teen' };"
        " const count = window.Aira.appNew.commerce().length; life.me = was; return count; }",
        isolated_context=False,
    )
    assert offered == 0
    assert not problems, problems
    context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_commerce_fits_a_phone(browser, env, dark):
    context, page, problems, _made = seeded(
        browser, env, width=390, height=844, dark=dark
    )
    for hash_ in (
        "shop?tab=orders",
        "shop?tab=products",
        "bookings?tab=appointments",
        "bookings?tab=calendar",
        "bookings?tab=customers",
        "shop?tab=search&q=silva",
    ):
        open_app(page, env, hash_)
        page.wait_for_timeout(300)
        wide = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert wide <= 1, (hash_, wide)
        # each table fits the screen without scrolling sideways
        for table in page.locator("#main .cm-narrow .table-wrap").all():
            assert (
                table.evaluate("(node) => node.scrollWidth - node.clientWidth") <= 2
            ), hash_
    # A phone books from the page head ("+ New" is in the menu sheet); the tab's own button steps aside.
    open_app(page, env, "bookings?tab=appointments")
    assert page.locator("#main .page-head .cm-head-new").is_visible()
    assert not page.locator("#main .cm-filters .cm-tab-new").is_visible()
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(200)
    assert not page.locator("#main .page-head .cm-head-new").is_visible()
    assert page.locator("#main .cm-filters .cm-tab-new").is_visible()
    assert not problems, problems
    context.close()
