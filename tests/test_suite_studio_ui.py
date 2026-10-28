"""Studio in a real browser against the single-user server: every tab renders, a client and an invoice are made
through the screens, and the client uses the public invoice and proposal pages without signing in."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

HEADERS = {"Content-Type": "application/json", "X-Requested-With": "aira"}


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def setup():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def _api(context, env, method, path, body=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={**HEADERS, "Origin": env.base},
    )
    assert response.status < 400, (path, response.status, response.text())
    return response.json()


def test_studio_tabs_and_an_invoice_paid_through_its_public_page(browser, env):
    context, page, problems = signed_in_page(browser, env)
    _api(
        context,
        env,
        "POST",
        "/api/suite/business",
        {"name": "Northlight Studio", "payment_link": "https://pay.example/northlight"},
    )
    for tab, heading in (
        ("overview", "Overview"),
        ("deals", "Deals"),
        ("clients", "Clients"),
        ("activities", "Activities"),
        ("proposals", "Proposals"),
        ("projects", "Projects"),
        ("invoices", "Invoices"),
        ("retainers", "Retainers"),
        ("settings", "Settings"),
    ):
        go(page, env, f"studio?tab={tab}")
        page.wait_for_selector(".page-head h1")
        # the logo says Sales; the page names the folder it shows (a folder in the left menu on a wide screen)
        assert page.locator(".page-head h1").inner_text() == heading
        page.wait_for_selector(".shell-sublink[aria-current='page']")
    assert not problems, problems

    # A client through the form.
    go(page, env, "studio?tab=clients")
    page.get_by_role("button", name="New client").first.click()
    page.locator(".modal input[name='name']").fill("Maya Patel")
    page.locator(".modal input[name='email']").fill("maya@bloom.example")
    page.locator(".modal input[name='company']").fill("Bloom Bakery")
    page.get_by_role("button", name="Add client").click()
    page.wait_for_selector("text=Bloom Bakery")

    # An invoice through the line editor.
    go(page, env, "studio?tab=invoices")
    page.get_by_role("button", name="New invoice").first.click()
    page.wait_for_selector(".modal")
    client_id = _api(context, env, "GET", "/api/studio/clients")["clients"][0]["id"]
    page.locator(".modal select").first.select_option(client_id)
    page.locator(".modal input[aria-label='Description']").fill("Brand identity")
    page.locator(".modal input[aria-label='Unit price']").fill("1500")
    until(
        page,
        lambda: "$1,500.00" in page.locator(".modal .suite-kpis").inner_text(),
        what="the live total",
    )
    page.get_by_role("button", name="Create invoice").click()
    page.wait_for_selector(".drawer h2:has-text('Invoice INV-0001')")
    page.locator(".drawer").get_by_role("button", name="Create link").click()
    page.wait_for_selector(".drawer .suite-share-url")
    url = page.locator(".drawer .suite-share-url").inner_text()
    assert "/p/" in url

    # The client, signed out, on the invoice page.
    visitor = browser.new_context().new_page()
    visitor_problems: list[str] = []
    visitor.on("pageerror", lambda e: visitor_problems.append(str(e)))
    visitor.goto(url)
    visitor.wait_for_selector("h1:has-text('Invoice INV-0001')")
    assert "$1,500.00" in visitor.locator(".pub-total").first.inner_text()
    assert (
        visitor.locator("a.pub-btn[href='https://pay.example/northlight']").count() == 1
    )
    assert visitor.locator("#pubName").inner_text() == "Northlight Studio"
    visitor.locator("input[placeholder='Transaction or reference number']").fill(
        "UTR 4411"
    )
    visitor.get_by_role("button", name="Let them know I paid").click()
    visitor.wait_for_selector("text=We have your note")
    assert not visitor_problems, visitor_problems

    # Back in Studio: the note is shown, and the business records the money.
    invoice_id = _api(context, env, "GET", "/api/studio/invoices")["invoices"][0]["id"]
    go(page, env, f"studio?tab=invoices&invoice={invoice_id}")
    page.wait_for_selector(".drawer >> text=Your client says they paid")
    page.locator(".drawer").get_by_role("button", name="Mark paid in full").click()
    page.wait_for_selector(".drawer .suite-pill:has-text('paid')")
    assert (
        _api(context, env, "GET", "/api/suite/home")["revenue"]["total_cents"] == 150000
    )
    assert not problems, problems


def test_a_client_accepts_a_proposal_on_its_page(browser, env):
    context, page, problems = signed_in_page(browser, env)
    client = _api(
        context,
        env,
        "POST",
        "/api/studio/clients",
        {"name": "Lee Chen", "company": "Chen Dental"},
    )["client"]
    proposal = _api(
        context,
        env,
        "POST",
        "/api/studio/proposals",
        {
            "client_id": client["id"],
            "title": "New website",
            "intro": "Hi Lee, here is the plan.",
            "items": [{"description": "Website", "unit_cents": 400000}],
        },
    )["proposal"]
    link = _api(
        context, env, "POST", f"/api/studio/proposals/{proposal['id']}/link", {}
    )["link"]
    visitor = browser.new_context().new_page()
    visitor.goto(env.base + link["path"])
    visitor.wait_for_selector("h1:has-text('New website')")
    assert "Hi Lee" in visitor.locator("main").inner_text()
    visitor.get_by_role("button", name="Accept proposal").click()
    visitor.wait_for_selector(".pub-notice.bad")  # no name yet
    visitor.locator("input[autocomplete='name']").fill("Lee Chen")
    visitor.locator("input[type='checkbox']").check()
    visitor.get_by_role("button", name="Accept proposal").click()
    visitor.wait_for_selector("text=Accepted by Lee Chen")
    go(page, env, f"studio?tab=proposals&proposal={proposal['id']}")
    page.wait_for_selector(".drawer >> text=Accepted by Lee Chen")
    page.locator(".drawer").get_by_role(
        "button", name="Start project and invoice"
    ).click()
    page.wait_for_selector(".drawer h2:has-text('Invoice INV-0001')")
    assert not problems, problems
