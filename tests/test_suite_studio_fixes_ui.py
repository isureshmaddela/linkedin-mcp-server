"""Sales in a real browser, for the tester's last round: the final invoice's editor takes the deposit off, the
client's deposit invoice says "Not paid yet", the invoices list fits a phone, "Check now" says when a client has no
email address, and a child's profile gets a plain page instead of the business's money."""

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
    if body is None and method == "POST":
        body = {}
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={**HEADERS, "Origin": env.base},
    )
    assert response.status < 400, (path, response.status, response.text())
    return response.json()


def test_deposit_journey_editor_public_page_phone_list_and_check_now(browser, env):
    context, page, problems = signed_in_page(browser, env)
    client = _api(
        context,
        env,
        "POST",
        "/api/studio/clients",
        {"name": "Maya Patel", "email": "maya@bloom.example"},
    )["client"]
    proposal = _api(
        context,
        env,
        "POST",
        "/api/studio/proposals",
        {
            "client_id": client["id"],
            "title": "Shop",
            "deposit_percent": "20",
            "tax_rate_bp": 0,
            "items": [{"description": "Build", "unit_cents": 300000}],
        },
    )["proposal"]
    link = _api(context, env, "POST", f"/api/studio/proposals/{proposal['id']}/link")[
        "link"
    ]

    # the client accepts on a phone and opens the deposit invoice
    phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.goto(env.base + link["path"])
    phone.locator("input[autocomplete='name']").fill("Maya Patel")
    phone.locator("input[type='checkbox']").check()
    phone.get_by_role("button", name="Accept proposal").click()
    phone.get_by_role("link", name="Open the deposit invoice").click()
    phone.wait_for_selector("h1:has-text('Deposit invoice INV-0001')")
    # it used to say "Awaiting your answer" over the amount due
    assert phone.locator(".pub-badge").first.inner_text() == "Not paid yet"

    # the final invoice's editor shows the total the client is billed
    made = _api(context, env, "POST", f"/api/studio/proposals/{proposal['id']}/convert")
    go(page, env, f"studio?tab=invoices&invoice={made['invoice']['id']}")
    page.locator(".drawer").get_by_role("button", name="Edit", exact=True).click()
    page.wait_for_selector(".modal >> text=Deposit taken off")
    totals = page.locator(".modal .suite-kpis").inner_text()
    assert "-$600.00" in totals and "$2,400.00" in totals, totals
    page.get_by_role("button", name="Cancel").click()

    # a late invoice for a client with no email: Check now says why no reminder went out
    quiet = _api(context, env, "POST", "/api/studio/clients", {"name": "Sam Ortiz"})[
        "client"
    ]
    late = _api(
        context,
        env,
        "POST",
        "/api/studio/invoices",
        {
            "client_id": quiet["id"],
            "issue_date": "2026-01-01",
            "due_date": "2026-01-15",
            "items": [{"description": "Work", "unit_cents": 20000}],
        },
    )["invoice"]
    _api(context, env, "POST", f"/api/studio/invoices/{late['id']}/link")
    _api(context, env, "POST", "/api/studio/settings", {"auto_reminders": True})
    go(page, env, "studio?tab=settings")
    page.get_by_role("button", name="Check now").click()
    page.wait_for_selector("text=not emailed because the client has no email address")

    # the invoices list on a phone shows the money columns without sideways scrolling
    page.set_viewport_size({"width": 390, "height": 844})
    go(page, env, "studio?tab=invoices")
    page.wait_for_selector(".studio-invoices table")
    shown = page.evaluate(
        "() => [...document.querySelectorAll('.studio-invoices th')].filter((t) => t.offsetParent)"
        ".map((t) => [t.textContent, t.getBoundingClientRect().right])"
    )
    labels = [label for label, _ in shown]
    assert "Total" in labels and "Still due" in labels, labels
    assert all(right <= 390 for _, right in shown), shown
    widths = page.evaluate(
        "() => { const w = document.querySelector('.studio-invoices .table-wrap'); const t = w.querySelector('table');"
        " return [w.scrollWidth, w.clientWidth, t.getBoundingClientRect().width,"
        " [...t.querySelectorAll('tbody tr:first-child td')].filter((c) => c.offsetParent).map((c) => [c.textContent, Math.round(c.getBoundingClientRect().width)])]; }"
    )
    assert widths[0] <= widths[1], widths
    assert not problems, problems


def test_a_child_profile_sees_a_plain_page_and_can_switch(browser, env):
    context, page, problems = signed_in_page(browser, env)
    _api(context, env, "POST", "/api/studio/clients", {"name": "Lee Chen"})
    kid = _api(
        context,
        env,
        "POST",
        "/api/life/profiles",
        {"name": "Kid", "age_group": "child"},
    )["profile"]
    header = _api(context, env, "POST", "/api/life/unlock", {"profile_id": kid["id"]})[
        "header"
    ]
    # the browser remembers who is using Aira the way life.js does after a face is tapped
    go(page, env, "studio")
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, value)",
        ["aira.lifeProfile.leads-ui@example.com", json.dumps(header)],
    )
    go(page, env, "studio?tab=invoices")
    page.reload()
    page.wait_for_selector("text=only the grown-ups in your household can open it")
    assert page.locator("text=Something went wrong").count() == 0
    assert page.locator("text=Lee Chen").count() == 0
    assert page.locator(".shell-new").count() == 0  # nothing to create on a child's profile
    page.get_by_role("button", name="Switch person").click()
    page.wait_for_selector(".life-picker")
    page.locator(".life-face").filter(has_not_text="Kid").first.click()
    # the choice is saved once the picker closes; leaving sooner would ask again
    page.wait_for_selector(".life-picker", state="detached")
    go(page, env, "studio?tab=clients")
    page.wait_for_selector("text=Lee Chen")  # the grown-up uses Sales as before
    assert page.locator(".shell-new").count() == 1
    # the only failures were Sales refusing the child (the browser logs each 403)
    assert all("403" in problem for problem in problems), problems
