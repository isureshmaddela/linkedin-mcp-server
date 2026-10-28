"""Sales in a real browser: pages never show a stray "null", the Earnings tab and the settings for what Sales does by
itself work, and a client accepts a proposal with a deposit on a phone and pays it from the page."""

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


def _words(page):
    return page.evaluate("() => document.body.innerText").split()


def _sideways(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def test_empty_tabs_and_plain_public_pages_never_show_null(browser, env):
    context, page, problems = signed_in_page(browser, env)
    for tab in ("invoices", "deals", "reports", "settings", "overview"):
        go(page, env, f"studio?tab={tab}")
        # the tabs are folders in the left menu on a wide screen (the page's own bar is for phones)
        page.wait_for_selector(".shell-sublink[aria-current='page']")
        page.wait_for_load_state("networkidle")
        # an empty list used to print the word "null" above the table
        assert "null" not in _words(page), tab
    go(page, env, "studio?tab=deals")
    page.wait_for_selector("text=Add a deal")

    # no payment link, no notes, nothing left to pay: every optional part of the client page is left out
    client = _api(context, env, "POST", "/api/studio/clients", {"name": "Lee Chen"})[
        "client"
    ]
    invoice = _api(
        context,
        env,
        "POST",
        "/api/studio/invoices",
        {
            "client_id": client["id"],
            "items": [{"description": "Audit", "unit_cents": 5000}],
        },
    )["invoice"]
    link = _api(context, env, "POST", f"/api/studio/invoices/{invoice['id']}/link", {})[
        "link"
    ]
    _api(context, env, "POST", f"/api/studio/invoices/{invoice['id']}/payments", {})
    proposal = _api(
        context,
        env,
        "POST",
        "/api/studio/proposals",
        {
            "client_id": client["id"],
            "title": "Plain",
            "items": [{"description": "X", "unit_cents": 100}],
        },
    )["proposal"]
    plink = _api(
        context, env, "POST", f"/api/studio/proposals/{proposal['id']}/link", {}
    )["link"]
    visitor = browser.new_context().new_page()
    for path, heading in ((link["path"], "Invoice INV-0001"), (plink["path"], "Plain")):
        visitor.goto(env.base + path)
        visitor.wait_for_selector(f"h1:has-text('{heading}')")
        assert "null" not in _words(visitor), path
    assert not problems, problems


def test_a_deposit_accepted_and_paid_on_a_phone_then_seen_in_earnings(browser, env):
    context, page, problems = signed_in_page(browser, env)
    _api(
        context,
        env,
        "POST",
        "/api/suite/business",
        {"name": "Northlight Studio", "payment_link": "https://pay.example/northlight"},
    )
    client = _api(
        context,
        env,
        "POST",
        "/api/studio/clients",
        {
            "name": "Maya Patel",
            "company": "Bloom Bakery",
            "email": "maya@bloom.example",
        },
    )["client"]

    # the deposit is set in the proposal editor
    go(page, env, "studio?tab=proposals")
    page.get_by_role("button", name="New proposal").first.click()
    page.wait_for_selector(".modal")
    page.locator(".modal select").first.select_option(client["id"])
    page.locator(".modal input[placeholder^='Website refresh']").fill("Online shop")
    page.locator(".modal input[aria-label='Description']").fill("Build the shop")
    page.locator(".modal input[aria-label='Unit price']").fill("4000")
    page.locator(".modal input[type='number']").fill("25")
    page.get_by_role("button", name="Create proposal").click()
    page.wait_for_selector(".drawer >> text=Deposit: 25% ($1,000.00)")
    page.locator(".drawer").get_by_role("button", name="Create link").click()
    page.wait_for_selector(".drawer .suite-share-url")
    url = page.locator(".drawer .suite-share-url").inner_text()

    phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone_problems: list[str] = []
    phone.on("pageerror", lambda e: phone_problems.append(str(e)))
    phone.goto(url)
    phone.wait_for_selector("text=A deposit of 25% ($1,000.00) is due when you accept")
    assert _sideways(phone) <= 0
    phone.locator("input[autocomplete='name']").fill("Maya Patel")
    phone.locator("input[type='checkbox']").check()
    phone.get_by_role("button", name="Accept proposal").click()
    phone.wait_for_selector("text=Pay your deposit to get started")
    # the badge at the top used to keep saying "Awaiting your answer"
    assert phone.locator(".pub-badge").first.inner_text() == "Accepted"
    assert (
        phone.locator("a.pub-btn[href='https://pay.example/northlight']").count() == 1
    )
    phone.get_by_role("link", name="Open the deposit invoice").click()
    phone.wait_for_selector("h1:has-text('Deposit invoice INV-0001')")
    assert "$1,000.00" in phone.locator(".pub-total").first.inner_text()
    assert _sideways(phone) <= 0
    assert not phone_problems, phone_problems

    # the business records the deposit and sees it in Earnings
    deposit = _api(context, env, "GET", "/api/studio/invoices")["invoices"][0]
    _api(context, env, "POST", f"/api/studio/invoices/{deposit['id']}/payments", {})
    page.set_viewport_size({"width": 390, "height": 844})
    go(page, env, "studio?tab=reports")
    page.wait_for_selector("text=Who pays you most")
    until(
        page,
        lambda: "$1,000.00" in page.locator(".suite-kpi").first.inner_text(),
        what="the money received",
    )
    assert page.locator(".studio-rank li").count() == 1
    assert _sideways(page) <= 0

    # settings: turn on reminders, then check now
    page.set_viewport_size({"width": 1366, "height": 900})
    go(page, env, "studio?tab=settings")
    page.get_by_label("Remind clients about overdue invoices").check()
    page.get_by_role("button", name="Save reminders and retainers").click()
    until(
        page,
        lambda: _api(context, env, "GET", "/api/studio/settings")["settings"][
            "auto_reminders"
        ],
        what="the setting to be saved",
    )
    page.get_by_role("button", name="Check now").click()
    page.wait_for_selector("text=Nothing needed doing right now.")
    assert not problems, problems
