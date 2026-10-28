"""Marketing: regressions for the problems an independent tester found after the money features.

* Amounts like "Infinity", "1e400", "sNaN" or "NaN" (as text, or as bare JSON numbers) were a server error (500),
  even from an anonymous visitor on the public media kit; they are a plain 400 now.
* A child or teen household profile could read every business lead and mark leads paid; it is refused (403), as
  the hosted build refuses a member without a business role, and so are the Leads routes.
* A lead paid, undone and paid again in a later month kept the first payment's date in the Business hub.
* A post that fell due while the business had no email used up its one reminder; it is reminded once an email is
  added.

Unit tests on a scratch database, then the single-user server, then the hosted gateway.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import test_careers
from aira.suite import kit
from aira.suite.marketing import service
from test_suite_marketing import (  # noqa: F401 - fixtures
    _anonymous,
    db,
    local_index,
    no_live_model,
    no_model,
)

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

M = "/api/marketing"
NOT_NUMBERS = ("Infinity", "-Infinity", "1e400", "sNaN", "NaN", "inf")


def _stamp(delta: timedelta) -> str:
    return (kit.utcnow() + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lead(db, **extra):
    form = service.create_form(db, {"title": "Book a consult"})
    service.submit(db, form["id"], {"name": "Ana Lopez", "email": "ana@example.com"})
    lead = service.list_submissions(db, form_id=form["id"])[0]
    if extra:
        lead = service.update_submission(db, lead["id"], extra)
    return lead


def _raw(env, api, method: str, path: str, body: bytes) -> tuple[int, dict]:
    """A request with a body written by hand (JSON as a browser or a script may send it: bare Infinity, 1e400)."""
    request = urllib.request.Request(env.base + path, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with api.opener.open(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


# ---------------------------------------------------------------------------
# amounts that are not real numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", NOT_NUMBERS)
def test_an_amount_that_is_not_a_real_number_is_a_plain_refusal(db, text):
    """Regression: Decimal("Infinity") and friends raised InvalidOperation (not a ValueError), so routes gave 500."""
    with pytest.raises(ValueError) as caught:
        service.amount(text, "your budget")
    message = str(caught.value)
    assert message.startswith("Your budget") and "convert" not in message
    with pytest.raises(ValueError):
        service.sponsorship_request(
            db,
            {
                "company": "Acme",
                "name": "Jo",
                "email": "jo@acme.example",
                "budget": text,
            },
        )


@pytest.mark.parametrize("number", [float("inf"), float("-inf"), float("nan"), 1e308])
def test_a_json_number_that_is_not_a_real_amount_is_refused(db, number):
    """Regression: json.loads reads bare Infinity, NaN and 1e400 as floats, and money_field crashed on them."""
    with pytest.raises(ValueError):
        service.money_field(number, "the value")
    lead = _lead(db, value_cents=5000)
    with pytest.raises(ValueError):
        service.update_submission(db, lead["id"], {"value_cents": number})
    with pytest.raises(ValueError):
        service.mark_paid(db, lead["id"], {"value_cents": number})
    with pytest.raises(ValueError):
        service.create_campaign(db, {"name": "X", "spend_cents": number})
    assert service.get_submission(db, lead["id"])["value_cents"] == 5000
    assert kit.revenue_lines(db, app="marketing") == []


def test_ordinary_amounts_still_work(db):
    assert service.amount("12.50", "the value") == 1250
    assert service.amount("1,200", "the value") == 120000
    assert service.amount("", "the value") == 0
    assert service.money_field(12.5, "the value") == 1250
    assert service.money_field(4500, "the value") == 4500
    with pytest.raises(ValueError, match="too large"):
        service.amount("1e30", "the value")


def test_anonymous_and_signed_in_requests_with_bad_amounts_get_400(
    env, api, local_index, no_model
):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow", "email": "owner@glow.example"})
    api.post(f"{M}/media-kit", {"headline": "Videos", "open": True})
    shared = api.post(f"{M}/media-kit/share")["public"]
    for text in ("Infinity", "1e400", "sNaN", "NaN"):
        status, data = _anonymous(
            env,
            "POST",
            f"/api/public/{shared['token']}/request",
            {
                "company": "Acme",
                "name": "Jo",
                "email": "jo@acme.example",
                "budget": text,
            },
        )
        assert status == 400, (text, status, data)
        assert "budget" in json.dumps(data).lower()
    status, _ = _anonymous(
        env,
        "POST",
        f"/api/public/{shared['token']}/request",
        {"company": "Acme", "name": "Jo", "email": "jo@acme.example", "budget": "300"},
    )
    assert status == 201
    lead_id = api.get(f"{M}/submissions")["submissions"][0]["id"]
    for body in (
        b'{"value_cents": Infinity}',
        b'{"value_cents": 1e400}',
        b'{"value_cents": NaN}',
    ):
        status, data = _raw(env, api, "PATCH", f"{M}/submissions/{lead_id}", body)
        assert status == 400, (body, status, data)
        status, data = _raw(env, api, "POST", f"{M}/submissions/{lead_id}/paid", body)
        assert status == 400, (body, status, data)
    status, data = _raw(
        env, api, "POST", f"{M}/campaigns", b'{"name": "X", "spend_cents": 1e400}'
    )
    assert status == 400, data
    status, data = api.call(
        "POST", f"{M}/submissions/{lead_id}/paid", {"value_cents": "1e400"}
    )
    assert status == 400, data
    assert api.get(f"{M}/submissions")["submissions"][0]["value_cents"] == 30000


# ---------------------------------------------------------------------------
# children and teens in the household
# ---------------------------------------------------------------------------


def test_a_child_profile_cannot_read_leads_or_mark_them_paid(
    env, api, local_index, no_model
):
    """Regression: /api/marketing/* ignored the household profile, so a child read customers' emails and changed
    the business's revenue."""
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow", "email": "owner@glow.example"})
    form = api.post(f"{M}/forms", {"title": "Consult"}, expect=201)["form"]
    token = api.post(f"{M}/forms/{form['id']}/link")["public"]["token"]
    assert (
        _anonymous(
            env,
            "POST",
            f"/api/public/{token}/submit",
            {"name": "Bea", "email": "bea@brand.example"},
        )[0]
        == 201
    )
    lead_id = api.get(f"{M}/submissions")["submissions"][0]["id"]

    household = Household(env, api)
    kid = household.add("Kid", age_group="child", pin="2468")
    teen = household.add("Sam", age_group="teen", pin="1357")
    grown_up = household.add("Ravi", age_group="adult", pin="8642")
    for young in (kid, teen):
        status, data = young.call("GET", f"{M}/submissions")
        assert status == 403, (young.name, status, data)
        assert "bea@brand.example" not in json.dumps(data)
        assert young.call("GET", f"{M}/overview")[0] == 403
        assert (
            young.call("POST", f"{M}/submissions/{lead_id}/paid", {"value_cents": 999})[
                0
            ]
            == 403
        )
        assert young.call("GET", "/api/leads/overview")[0] == 403
        assert young.call("GET", "/api/leads/items")[0] == 403
    assert api.get(f"{M}/submissions")["submissions"][0]["paid"] is False
    # Grown-ups (and the account itself, with no profile header) use it as before.
    status, data = grown_up.call("GET", f"{M}/submissions")
    assert status == 200 and data["submissions"][0]["email"] == "bea@brand.example"
    assert household.owner.call("GET", f"{M}/overview")[0] == 200
    assert api.get(f"{M}/overview")["leads_month"] >= 1
    assert grown_up.call("GET", "/api/leads/overview")[0] == 200


# ---------------------------------------------------------------------------
# a payment undone and made again later
# ---------------------------------------------------------------------------


def test_paid_again_after_undo_counts_in_the_month_it_was_really_paid(db):
    """Regression: the revenue line kept the first payment's date, so the hub and the overview disagreed."""
    kit.save_business(db, {"name": "Glow", "currency": "USD"})
    lead = _lead(db, value_cents=10000)
    service.mark_paid(db, lead["id"], {})
    earlier = _stamp(timedelta(days=-75))
    db.execute(
        "UPDATE suite_revenue SET at = ? WHERE app = 'marketing' AND source_id = ?",
        (earlier, lead["id"]),
    )
    db.execute(
        "UPDATE mk_submissions SET paid_at = ? WHERE id = ?", (earlier, lead["id"])
    )
    db.commit()
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 0
    service.mark_unpaid(db, lead["id"])
    again = service.mark_paid(db, lead["id"], {})
    line = kit.revenue_lines(db, app="marketing")[0]
    assert line["at"][:7] == again["paid_at"][:7] == kit.now()[:7]
    hub = kit.revenue(db, app="marketing")
    assert hub["this_month_cents"] == 10000
    assert service.overview(db)["earned_month_cents"] == hub["this_month_cents"]
    # Changing the value of a paid lead keeps its payment date.
    service.update_submission(db, lead["id"], {"value_cents": 12000})
    assert kit.revenue_lines(db, app="marketing")[0]["at"] == line["at"]


# ---------------------------------------------------------------------------
# a post reminder waits for the business email
# ---------------------------------------------------------------------------


def test_a_due_post_is_reminded_once_the_business_adds_an_email(db):
    """Regression: with no business email the post was marked reminded and never emailed afterwards."""
    kit.save_business(db, {"name": "Glow", "email": ""})
    post = service.create_post(
        db,
        {
            "channel": "instagram",
            "body": "New colours",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(minutes=-5)),
        },
    )
    assert service.remind_due_posts(db) == 0
    row = db.execute("SELECT reminded_at FROM mk_posts WHERE id = ?", (post["id"],))
    assert row.fetchone()[0] is None
    assert len(service.overview(db)["due_posts"]) == 1
    kit.save_business(db, {"name": "Glow", "email": "owner@glow.example"})
    assert service.remind_due_posts(db) == 1
    mails = kit.outbox(db, app="marketing")
    assert len(mails) == 1 and mails[0]["recipient"] == "owner@glow.example"
    assert service.remind_due_posts(db) == 0  # still once


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_bad_amounts_are_a_400_not_a_server_error(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "marketing")
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", f"{M}/media-kit", {"open": True})[0] == 200
    status, shared = owner.call("POST", f"{M}/media-kit/share", {})
    token = shared["public"]["token"]
    visitor = Browser(cloud)
    for text in ("Infinity", "1e400", "sNaN"):
        status, data = visitor.call(
            "POST",
            f"/api/public/{token}/request",
            {
                "company": "Acme",
                "name": "Jo",
                "email": "jo@acme.example",
                "budget": text,
            },
        )
        assert status == 400, (text, status, data)
    status, _ = visitor.call(
        "POST",
        f"/api/public/{token}/request",
        {"company": "Acme", "name": "Jo", "email": "jo@acme.example", "budget": "250"},
    )
    assert status == 201
    lead_id = owner.call("GET", f"{M}/submissions")[1]["submissions"][0]["id"]
    for value in ("1e400", "Infinity"):
        status, data = owner.call(
            "POST", f"{M}/submissions/{lead_id}/paid", {"value_cents": value}
        )
        assert status == 400, (value, status, data)
    status, data = owner.call(
        "POST",
        f"{M}/submissions/{lead_id}/paid",
        b'{"value_cents": 1e400}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 400, (status, data)
    status, paid = owner.call("POST", f"{M}/submissions/{lead_id}/paid", {})
    assert status == 200 and paid["submission"]["value_cents"] == 25000
