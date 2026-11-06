"""Selling to businesses and consumers (B2B and B2C) in a real browser: the seller turns on business buyers, a
business applies on the storefront, the seller approves it and gets the trade code once, the business signs in,
sees its trade prices and orders on net terms with a PO number, and the order page is its invoice."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page  # noqa: E402, F401
from test_suite_commerce_ui import caller, no_model, no_null  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402


def test_a_business_applies_is_approved_and_orders_on_terms(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env, width=1280, height=900)
    call = caller(context, env)
    call("POST", "/api/suite/business", {"name": "Bean Co", "tax_id": "GB999"})
    call(
        "POST",
        "/api/shop/products",
        {"name": "Coffee 1kg", "price_cents": 2000, "status": "active"},
    )

    # The shop sells to consumers: the Business buyers tab offers to turn it on.
    go(page, env, "shop?tab=business")
    page.get_by_role("button", name="Sell to businesses and consumers").click()
    page.wait_for_selector("text=What businesses owe")
    product = call("GET", "/api/shop/products")["products"][0]
    call(
        "PATCH",
        f"/api/shop/products/{product['id']}",
        {"wholesale_cents": 1500, "min_quantity": 5, "price_breaks": [{"min_quantity": 20, "unit_cents": 1300}]},
    )
    go(page, env, "shop?tab=products")
    page.wait_for_selector("text=trade")
    store_url = env.base + call("POST", "/api/shop/storefront/link", {})["link"]["path"]

    buyer = context.new_page()
    buyer.on("pageerror", lambda e: problems.append(f"storefront pageerror: {e}"))
    buyer.goto(store_url)
    buyer.wait_for_selector("text=Buying for a business?")
    buyer.get_by_role("button", name="Apply for a trade account").click()
    form = buyer.locator(".pub-form:not([hidden])", has_text="Business name")
    form.get_by_label("Business name").fill("Corner Cafe")
    form.get_by_label("Business email").fill("orders@corner.example")
    form.get_by_label("Tax or registration number").fill("GB123")
    form.get_by_role("button", name="Apply", exact=True).click()
    buyer.wait_for_selector("text=The shop will review your request")

    # The seller approves it with net 30 and sees the code once.
    go(page, env, "shop?tab=business")
    page.wait_for_selector("text=Waiting for approval (1)")
    page.locator("tr", has_text="Corner Cafe").first.click()
    page.get_by_label("Payment terms", exact=True).select_option("net30")
    page.get_by_role("button", name="Approve", exact=True).click()
    code = page.locator(".cm-code").inner_text()
    assert len(code) == 14
    page.get_by_role("button", name="Done").click()

    buyer.reload()
    buyer.get_by_role("button", name="Sign in with a trade code").click()
    form = buyer.locator(".pub-form:not([hidden])", has_text="Trade code")
    form.get_by_label("Business email").fill("orders@corner.example")
    form.get_by_label("Trade code").fill(code.lower())
    form.get_by_role("button", name="Sign in", exact=True).click()
    buyer.wait_for_selector("text=Trade prices")
    item = buyer.locator(".pub-item", has_text="Coffee 1kg")
    item.get_by_text("Minimum 5").wait_for()
    assert "20+" in item.inner_text()
    item.locator("input[type='number']").fill("20")
    item.get_by_role("button", name="Add to cart").click()
    buyer.wait_for_selector("text=You save on trade prices")
    assert "Net 30" in buyer.locator(".pub-table").last.inner_text()
    no_null(buyer)
    buyer.get_by_label("PO number (optional)").fill("PO-42")
    buyer.get_by_role("button", name="Place business order").click()
    buyer.wait_for_selector("text=Thank you, Corner Cafe!")
    buyer.get_by_role("link", name="See your order and invoice").click()
    buyer.wait_for_selector("text=Bill to")
    invoice = buyer.locator(".pub-card", has_text="Bill to").inner_text()
    assert "PO-42" in invoice and "GB999" in invoice and "GB123" in invoice
    no_null(buyer)

    # The seller sees what the business owes, and the order says who it is from.
    go(page, env, "shop?tab=business")
    owed = page.locator(".suite-section", has_text="What businesses owe")
    owed.get_by_text("PO-42").wait_for()
    go(page, env, "shop?tab=orders")
    page.locator("tr", has_text="Corner Cafe").first.click()
    page.wait_for_selector("text=Send payment reminder")
    assert "PO-42" in page.locator(".suite-section", has_text="PO number").inner_text()
    assert not problems, problems
