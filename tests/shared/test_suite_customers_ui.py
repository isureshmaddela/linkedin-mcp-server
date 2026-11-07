"""In a real browser: Business hub > Customers (search, the contact page and timeline, merging, Send to Sales), the
Online payment card on a Shop order, the Marketing subscribers waiting to confirm, and the Leads follow-up sequence
editor with a lead's sequence status."""

# ruff: noqa: F811
from __future__ import annotations

import json
import re

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_commerce_ui import caller, no_model, quick_forms  # noqa: E402, F401


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    yield
    public.reset_index()


def _orders(call):
    mug = call(
        "POST",
        "/api/shop/products",
        {"name": "Mug", "price_cents": 1500, "status": "draft"},
    )["product"]
    paid = call(
        "POST",
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ana Silva",
            "email": "ana@example.com",
            "mark_paid": True,
        },
    )["order"]
    open_order = call(
        "POST",
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ana S",
            "email": "ana.s@work.example",
        },
    )["order"]
    return paid, open_order


def test_customers_contact_page_merge_and_send_to_sales(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1280, height=900)
    call = caller(context, env)
    _orders(call)
    go(page, env, "hub?tab=customers")
    page.wait_for_selector("text=2 customers")
    page.locator(".suite-table tbody tr", has_text="ana@example.com").click()
    page.wait_for_selector(".hub-timeline")
    assert "Order #1001" in page.locator(".hub-timeline").inner_text()
    assert (
        page.locator(".suite-kpi", has_text="Payments")
        .inner_text()
        .strip()
        .endswith("1")
    )
    page.get_by_role("button", name="Merge with another record").click()
    dialog = page.locator(".modal", has_text="Merge with")
    dialog.get_by_label("Find the other record").fill("work.example")
    dialog.get_by_role("button", name="Search").click()
    dialog.get_by_role("button", name="Merge", exact=True).click()
    page.locator(".modal", has_text="Merge two records").get_by_role(
        "button", name="Merge", exact=True
    ).click()
    until(
        page,
        lambda: page.locator(".hub-timeline li", has_text="Order #").count() == 2,
        what="both orders on one record",
    )
    page.get_by_role("button", name="Send to Sales").click()
    page.wait_for_url(re.compile(r"#/studio"))
    deals = call("GET", "/api/studio/deals")["deals"]
    assert len(deals) == 1 and deals[0]["stage"] == "new"
    go(page, env, "hub?tab=customers")
    page.wait_for_selector("text=1 customer")
    assert not problems, problems


def test_order_payment_card_subscribers_waiting_and_lead_sequences(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1280, height=900)
    call = caller(context, env)
    _paid, open_order = _orders(call)
    # the Shop order: an Online payment card with its buttons (no link yet)
    go(
        page, env, f"shop?tab=orders&order={open_order['id']}"
    )  # opens the order's drawer
    card = page.locator(".suite-payments")
    card.wait_for()
    assert card.get_by_role("button", name="Create payment link").is_visible()
    assert "No payment link yet" in card.inner_text()
    page.keyboard.press("Escape")
    # Marketing: the list counts people who have not confirmed
    call("POST", "/api/marketing/subscribers", {"email": "sam@example.com"})
    go(page, env, "marketing?tab=subscribers")
    page.wait_for_selector("text=Waiting to confirm")
    assert (
        page.get_by_label("Status")
        .locator("option", has_text="Waiting to confirm")
        .count()
        == 1
    )
    # Leads: a follow-up sequence made in the editor, and a lead added to it from its drawer
    call(
        "POST",
        "/api/leads/items",
        {
            "leads": [
                {
                    "name": "Biz A",
                    "company": "Biz A",
                    "email": "ana@biza.test",
                    "kind": "company",
                    "source": "manual",
                }
            ]
        },
    )
    go(page, env, "leads?tab=outreach")
    page.locator("#ldSeqNew").click()
    editor = page.locator(".modal", has_text="New follow-up sequence")
    editor.get_by_label("Name", exact=True).fill("Website follow-up")
    editor.get_by_label("Subject").fill("Websites for {company}")
    editor.get_by_label("Email", exact=True).fill("Hi {first_name}, a first note.")
    editor.get_by_role("button", name="Add a step").click()
    editor.get_by_label("Email", exact=True).nth(1).fill("Following up.")
    editor.get_by_role("button", name="Save sequence").click()
    page.wait_for_selector("#ldSeqTable >> text=Website follow-up")
    saved = call("GET", "/api/leads/sequences")["sequences"]
    assert (
        len(saved) == 1
        and len(saved[0]["steps"]) == 2
        and saved[0]["steps"][1]["delay_days"] == 3
    )
    lead_id = call("GET", "/api/leads/items")["leads"][0]["id"]
    go(page, env, f"leads?tab=leads&open={lead_id}")
    status = page.locator("#ldLeadSequences")
    status.get_by_role("button", name="Add to the sequence").click()
    status.locator("text=Running").wait_for()
    assert "step 1 of 2" in status.inner_text()
    status.get_by_role("button", name="Stop Website follow-up").click()
    status.locator("text=You stopped it").wait_for()
    assert not problems, problems
    assert json.dumps(problems) == "[]"
