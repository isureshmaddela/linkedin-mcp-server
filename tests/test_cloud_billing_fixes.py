"""Hosted billing correctness (docs/platform-review.md P0-5, P0-7, P0-8 and section 6).

Overage is billed per provider billing period, claimed before the provider is called; upgrades wait for payment;
one checkout is open at a time; Stripe requests carry idempotency keys and a pinned version; webhooks are applied
in order and portal changes are applied; the stand-in cannot run in public; sign-ups get a time-boxed trial; ad
top-ups are credited only on settled money.
"""

from __future__ import annotations

import calendar
import json
import logging
import threading
from contextlib import closing
from typing import Any

import pytest
from cloud_support import HOOKS, Browser, build_org, make_app, signup, webhook
from stripe_stub import SECRET, StripeStub

from monosage_cloud.billing import StandInRefused
from monosage_cloud.billing_providers import sign
from monosage_cloud.config import DAY, CloudConfig
from monosage_cloud.errors import Conflict


def at(year: int, month: int, day: int = 1, hour: int = 0) -> int:
    return calendar.timegm((year, month, day, hour, 0, 0))


def overage_invoices(app, org) -> list[dict[str, Any]]:
    return [i for i in app.billing.invoices(org.owner) if i["kind"] == "overage"]


def enable_overage(app, org) -> None:
    with app.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overage_enabled = 1 WHERE org_id = ?",
            (org.org_id,),
        )
    app.entitlements.invalidate(org.org_id)


def use_overage(app, org, units: int) -> None:
    """``units`` applications past the monthly limit, at the current time."""
    limit = app.entitlements.resolve(org.org_id, fresh=True).limit("apps_per_org_month")
    used = app.usage.used(org.org_id, "applications")
    if used < limit:
        app.usage.reserve(org.org_id, "applications", limit - used, monthly_limit=limit)
    app.usage.reserve(
        org.org_id, "applications", units, monthly_limit=limit, allow_overage=True
    )


def renewal(app, org, start: int, end: int) -> None:
    """The provider renews the subscription: its invoice for the new period is paid."""
    webhook(
        app,
        "invoice.paid",
        org_id=org.org_id,
        invoice_id=f"in_{start}",
        amount_cents=27500,
        period_start=start,
        period_end=end,
    )


class FakeProvider:
    """A provider whose invoice items can fail or block, recording every call."""

    name = "fake"

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.fail = 0
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def add_invoice_item(self, customer_id, amount_cents, description, currency, **kw):  # noqa: ANN001, ANN003, ANN201
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail:
            self.fail -= 1
            raise RuntimeError("provider down")
        self.items.append(
            {"amount_cents": amount_cents, "description": description, **kw}
        )
        return f"ii_fake_{len(self.items)}"

    def update_subscription(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return {}


def fake_billed_org(app):
    org = build_org(app, plan="team", seats=25)
    fake = FakeProvider()
    app.billing.providers["fake"] = fake
    with app.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET billing_provider = 'fake', billing_customer_id = 'cus_f', billing_subscription_id = 'sub_f' WHERE org_id = ?",
            (org.org_id,),
        )
    enable_overage(app, org)
    return org, fake


# ----------------------------------------------------------------------------- P0-8 overage


def test_overage_is_billed_for_each_provider_period_january_february_march(cloud):
    cloud.clock.set(at(2027, 1, 1, 1))
    org = build_org(cloud, plan="team", seats=25)
    enable_overage(cloud, org)
    renewal(
        cloud, org, at(2027, 1, 1), at(2027, 2, 1)
    )  # the provider's period: calendar months here
    cloud.clock.set(at(2027, 1, 15))
    use_overage(cloud, org, 10)
    cloud.clock.set(at(2027, 2, 1, 2))
    renewal(cloud, org, at(2027, 2, 1), at(2027, 3, 1))
    cloud.billing.process_due()
    cloud.clock.set(at(2027, 2, 10))
    use_overage(
        cloud, org, 20
    )  # February: the month the old calendar guess never billed
    cloud.clock.set(at(2027, 3, 1, 2))
    renewal(cloud, org, at(2027, 3, 1), at(2027, 4, 1))
    cloud.billing.process_due()
    cloud.clock.set(at(2027, 3, 5))
    use_overage(cloud, org, 5)
    cloud.clock.set(at(2027, 4, 1, 2))
    renewal(cloud, org, at(2027, 4, 1), at(2027, 5, 1))
    cloud.billing.process_due()
    cloud.billing.process_due()  # a second tick bills nothing again
    billed = sorted(overage_invoices(cloud, org), key=lambda i: i["period_start"])
    assert [
        (i["period_start"], i["period_end"], i["amount_cents"]) for i in billed
    ] == [
        (at(2027, 1, 1), at(2027, 2, 1), 10 * 10),
        (at(2027, 2, 1), at(2027, 3, 1), 20 * 10),
        (at(2027, 3, 1), at(2027, 4, 1), 5 * 10),
    ]
    assert "2027-02-01 to 2027-03-01" in billed[1]["lines"][0]["description"]


def test_the_stand_in_period_is_billed_when_it_ends(cloud):
    org = build_org(cloud, plan="team", seats=25)
    enable_overage(cloud, org)
    start = cloud.billing.subscription(org.org_id)["period_start"]
    use_overage(cloud, org, 7)
    cloud.clock.advance(days=31)
    cloud.billing.process_due()
    [invoice] = overage_invoices(cloud, org)
    assert invoice["amount_cents"] == 70 and invoice["period_start"] == start


def test_a_failed_provider_call_leaves_the_period_retryable_with_the_same_keys(cloud):
    org, fake = fake_billed_org(cloud)
    start = cloud.clock.now()
    renewal(cloud, org, start, start + 30 * DAY)
    use_overage(cloud, org, 12)
    cloud.clock.advance(days=30, hours=2)
    fake.fail = 1
    renewal(
        cloud, org, start + 30 * DAY, start + 60 * DAY
    )  # closes the period at the provider's boundary
    assert cloud.billing.bill_due_overage() == {"failed": 1}
    assert overage_invoices(cloud, org) == [] and fake.items == []
    with cloud.db.read() as connection:
        row = dict(
            connection.execute(
                "SELECT state, attempts, error FROM overage_periods WHERE org_id = ?",
                (org.org_id,),
            ).fetchone()
        )
    assert (
        row["state"] == "failed"
        and row["attempts"] == 1
        and "provider down" in row["error"]
    )
    # a tick that claimed the retry and died: its claim is taken over after an hour, not before
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE overage_periods SET state = 'pending', claimed_at = ? WHERE org_id = ?",
            (cloud.clock.now() - 600, org.org_id),
        )
    assert cloud.billing.bill_due_overage() == {}
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE overage_periods SET claimed_at = ? WHERE org_id = ?",
            (cloud.clock.now() - 2 * 3600, org.org_id),
        )
    assert cloud.billing.bill_due_overage() == {"billed": 1}
    [item] = fake.items
    assert item["amount_cents"] == 120 and item["subscription_id"] == "sub_f"
    assert item["idempotency_key"] == (
        f"aira-overage-{org.org_id}-{start}-{start + 30 * DAY}-applications:overage"
    )
    assert [i["amount_cents"] for i in overage_invoices(cloud, org)] == [120]
    assert cloud.billing.bill_due_overage() == {}


def test_overlapping_ticks_bill_a_period_once(cloud):
    org, fake = fake_billed_org(cloud)
    start = cloud.clock.now()
    renewal(cloud, org, start, start + 30 * DAY)
    use_overage(cloud, org, 3)
    cloud.clock.advance(days=30, hours=2)
    renewal(cloud, org, start + 30 * DAY, start + 60 * DAY)
    fake.gate = threading.Event()
    results: list[dict[str, int]] = []
    first = threading.Thread(
        target=lambda: results.append(cloud.billing.bill_due_overage())
    )
    first.start()
    assert fake.entered.wait(10), "the first tick reached the provider"
    # a second tick while the first is inside the provider call: the claimed period is not due for it, and a
    # tick that had already listed it loses the claim
    assert cloud.billing.bill_due_overage() == {}
    assert cloud.billing._bill_overage_period(org.org_id, start) == "taken"
    fake.gate.set()
    first.join(10)
    assert results == [{"billed": 1}]
    assert len(fake.items) == 1, "one invoice item, not two"
    assert len(overage_invoices(cloud, org)) == 1


def test_overage_hours_belong_to_exactly_one_period(cloud):
    org = build_org(cloud, plan="team", seats=25)
    enable_overage(cloud, org)
    boundary = (cloud.clock.now() // 3600 + 5) * 3600 + 1800  # half past the hour
    cloud.clock.set(boundary - 600)
    use_overage(
        cloud, org, 4
    )  # ten minutes before the boundary, in the hour the boundary falls in
    before = cloud.usage.overage_between(org.org_id, boundary - 30 * DAY, boundary)
    after = cloud.usage.overage_between(org.org_id, boundary, boundary + 30 * DAY)
    assert before == {} and after == {"applications:overage": 4}


# ----------------------------------------------------------------------------- Stripe: keys, version, tax


@pytest.fixture
def stripe():
    stub = StripeStub().start()
    yield stub
    stub.stop()


@pytest.fixture
def stripe_app(tmp_path, stripe):
    HOOKS.reset()
    app = make_app(
        tmp_path,
        billing_provider="stripe",
        stripe_secret_key=SECRET,
        stripe_webhook_secret="whsec_test",
        stripe_api_base=stripe.base_url,
        stripe_tax=True,
    )
    yield app
    app.close()


def stripe_event(
    app, event_id: str, event_type: str, obj: dict, created: int | None = None
):
    body: dict[str, Any] = {"id": event_id, "type": event_type, "data": {"object": obj}}
    if created is not None:
        body["created"] = created
    payload = json.dumps(body).encode()
    return app.billing.handle_webhook(
        "stripe", payload, sign("whsec_test", payload, app.clock.now())
    )


def on_stripe(app, org) -> str:
    sub_id = f"sub_{org.org_id[:8]}"
    with app.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET billing_provider = 'stripe', billing_subscription_id = ?, billing_customer_id = 'cus_1' WHERE org_id = ?",
            (sub_id, org.org_id),
        )
    return sub_id


def test_every_stripe_post_has_an_idempotency_key_and_the_pinned_version(
    stripe_app, stripe
):
    result = signup(stripe_app)
    stripe_app.billing.start_checkout(result["owner"], "team", 30)
    posts = [c for c in stripe.calls if c["method"] == "POST"]
    assert posts and all(c["idempotency_key"] for c in posts), posts
    assert len({c["idempotency_key"] for c in posts}) == len(posts)
    assert all(
        c["stripe_version"] == CloudConfig().stripe_api_version for c in stripe.calls
    )
    [session] = stripe.find("POST", "/v1/checkout/sessions")
    assert session["idempotency_key"].startswith("aira-checkout-")
    params = session["params"]
    assert params["automatic_tax"] == {"enabled": "true"}
    assert params["tax_id_collection"] == {"enabled": "true"}
    assert params["customer_update"] == {"address": "auto", "name": "auto"}


def test_stripe_tax_is_off_unless_configured(tmp_path, stripe):
    HOOKS.reset()
    app = make_app(
        tmp_path,
        billing_provider="stripe",
        stripe_secret_key=SECRET,
        stripe_api_base=stripe.base_url,
    )
    try:
        app.billing.start_checkout(signup(app)["owner"], "team", 30)
        [session] = stripe.find("POST", "/v1/checkout/sessions")
        assert "automatic_tax" not in session["params"]
    finally:
        app.close()


# ----------------------------------------------------------------------------- upgrades wait for payment


def test_a_stripe_upgrade_waits_for_its_payment(stripe_app, stripe):
    org = build_org(stripe_app, plan="starter", seats=10)
    sub_id = on_stripe(stripe_app, org)
    stripe_app.clock.advance(days=15)
    outcome = stripe_app.billing.change_plan(org.owner, "team", 25)
    assert outcome["applied"] is False and outcome["pending_payment"] is True
    [update] = stripe.find("POST", f"/v1/subscriptions/{sub_id}")
    params = update["params"]
    assert params["payment_behavior"] == "pending_if_incomplete"
    assert params["proration_behavior"] == "always_invoice"
    assert (
        params["items"][0]["price"].startswith("price_")
        and params["items"][0]["quantity"] == "25"
    )
    sub = stripe_app.billing.subscription(org.org_id)
    assert sub["plan_id"] == "starter" and sub["candidate_seats"] == 10
    assert stripe_app.entitlements.resolve(org.org_id, fresh=True).plan_id == "starter"
    proration = next(
        i for i in stripe_app.billing.invoices(org.owner) if i["kind"] == "proration"
    )
    assert proration["status"] == "open" and proration["paid_at"] is None
    summary = stripe_app.billing.summary(org.org_id)
    assert summary["pending_upgrade"]["plan_id"] == "team"
    with pytest.raises(Conflict):
        stripe_app.billing.change_plan(org.owner, "team", 30)
    # the provider says the prorated invoice was paid: the upgrade applies, and the period stays as it was
    period = (sub["period_start"], sub["period_end"])
    stripe_event(
        stripe_app,
        "evt_up_paid",
        "invoice.paid",
        {
            "id": "in_upgrade_1",
            "customer": "cus_1",
            "subscription": sub_id,
            "billing_reason": "subscription_update",
            "amount_paid": proration["amount_cents"],
            "lines": {
                "data": [
                    {
                        "period": {
                            "start": stripe_app.clock.now(),
                            "end": sub["period_end"],
                        }
                    }
                ]
            },
            "subscription_details": {"metadata": {"org_id": org.org_id}},
        },
    )
    after = stripe_app.billing.subscription(org.org_id)
    assert after["plan_id"] == "team" and after["candidate_seats"] == 25
    assert (after["period_start"], after["period_end"]) == period
    assert not after["pending_upgrade"]
    assert stripe_app.entitlements.resolve(org.org_id, fresh=True).has(
        "sources.linkedin"
    )
    proration = next(
        i for i in stripe_app.billing.invoices(org.owner) if i["kind"] == "proration"
    )
    assert proration["status"] == "paid"


def test_a_declined_upgrade_changes_nothing_and_does_not_mark_the_plan_past_due(
    stripe_app, stripe
):
    org = build_org(stripe_app, plan="starter", seats=10)
    sub_id = on_stripe(stripe_app, org)
    stripe_app.billing.change_plan(org.owner, "team", 25)
    stripe_event(
        stripe_app,
        "evt_up_failed",
        "invoice.payment_failed",
        {
            "id": "in_upgrade_1",
            "customer": "cus_1",
            "subscription": sub_id,
            "billing_reason": "subscription_update",
            "amount_due": 1000,
            "subscription_details": {"metadata": {"org_id": org.org_id}},
        },
    )
    sub = stripe_app.billing.subscription(org.org_id)
    assert (
        sub["status"] == "active"
        and sub["plan_id"] == "starter"
        and not sub["pending_upgrade"]
    )
    assert stripe_app.mail.latest("owner@acme.test", "payment_failed") is None


def test_an_unpaid_upgrade_expires_after_a_day(stripe_app, stripe):
    org = build_org(stripe_app, plan="starter", seats=10)
    on_stripe(stripe_app, org)
    stripe_app.billing.change_plan(org.owner, "team", 25)
    stripe_app.clock.advance(days=1, hours=1)
    stripe_app.billing.process_due()
    sub = stripe_app.billing.subscription(org.org_id)
    assert sub["plan_id"] == "starter" and not sub["pending_upgrade"]


def test_the_stand_in_confirms_an_upgrade_through_its_own_invoice(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    outcome = cloud.billing.change_plan(org.owner, "team", 25)
    assert outcome["applied"] is True and outcome["pending_payment"] is False
    proration = next(
        i for i in cloud.billing.invoices(org.owner) if i["kind"] == "proration"
    )
    assert proration["status"] == "paid" and proration[
        "provider_invoice_id"
    ].startswith("in_standin_")


# ----------------------------------------------------------------------------- one open checkout


def test_a_new_checkout_replaces_the_open_one(cloud):
    owner = signup(cloud)["owner"]
    first = cloud.billing.start_checkout(owner, "starter", 10)
    second = cloud.billing.start_checkout(owner, "team", 30)
    assert (
        cloud.billing.standin.checkout_session(first["session_id"])["status"]
        == "expired"
    )
    assert (
        cloud.billing.standin.checkout_session(second["session_id"])["status"] == "open"
    )
    with cloud.db.read() as connection:
        states = dict(
            connection.execute(
                "SELECT session_id, status FROM billing_checkouts WHERE org_id = ?",
                (owner.org_id,),
            ).fetchall()
        )
    assert states == {first["session_id"]: "superseded", second["session_id"]: "open"}
    browser = Browser(cloud)
    status, body = browser.call(
        "POST", f"/billing/standin/checkout/{first['session_id']}/pay", {}
    )
    assert status == 409 and body["code"] == "checkout_expired"
    status, _ = browser.call(
        "POST", f"/billing/standin/checkout/{second['session_id']}/pay", {}, raw=True
    )
    assert status == 303
    sub = cloud.billing.subscription(owner.org_id)
    assert sub["status"] == "active" and sub["plan_id"] == "team"


def test_a_late_completion_of_a_replaced_checkout_is_not_applied_twice(
    stripe_app, stripe
):
    owner = signup(stripe_app)["owner"]
    old = stripe_app.billing.start_checkout(owner, "starter", 10)
    new = stripe_app.billing.start_checkout(owner, "team", 30)
    assert stripe.find("POST", f"/v1/checkout/sessions/{old['session_id']}/expire")

    def completed(
        event_id: str, session: str, subscription: str, plan: str, seats: int
    ):
        return stripe_event(
            stripe_app,
            event_id,
            "checkout.session.completed",
            {
                "id": session,
                "customer": "cus_x",
                "subscription": subscription,
                "metadata": {
                    "org_id": owner.org_id,
                    "plan_id": plan,
                    "seats": str(seats),
                },
            },
        )

    assert (
        completed("evt_new", new["session_id"], "sub_new", "team", 30)["outcome"]
        == "activated"
    )
    assert (
        completed("evt_old", old["session_id"], "sub_old", "starter", 10)["outcome"]
        == "duplicate"
    )
    sub = stripe_app.billing.subscription(owner.org_id)
    assert sub["plan_id"] == "team" and sub["billing_subscription_id"] == "sub_new"
    assert stripe.find("DELETE", "/v1/subscriptions/sub_old"), (
        "the second subscription is cancelled at once"
    )
    # the same completion delivered again (another event id) changes nothing either
    assert (
        completed("evt_new_again", new["session_id"], "sub_new", "team", 30)["outcome"]
        == "already_applied"
    )


# ----------------------------------------------------------------------------- webhook order and portal changes


def test_an_older_event_delivered_late_is_ignored(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    now = cloud.clock.now()

    def send(kind: str, created: int, **data: Any) -> dict[str, Any]:
        payload = json.dumps(
            {
                "id": f"evt_{kind}_{created}",
                "type": kind,
                "created": created,
                "data": {"org_id": org.org_id, **data},
            }
        ).encode()
        return cloud.billing.handle_webhook(
            "standin", payload, sign(cloud.billing.standin.secret, payload, now)
        )

    send("invoice.payment_failed", now + 200, invoice_id="in_2")
    assert cloud.billing.subscription(org.org_id)["status"] == "past_due"
    result = send(
        "invoice.paid",
        now + 100,
        invoice_id="in_1",
        period_start=now,
        period_end=now + 30 * DAY,
    )
    assert result["outcome"] == "recorded"
    assert cloud.billing.subscription(org.org_id)["status"] == "past_due", (
        "the newer failure stands"
    )
    send("subscription.updated", now + 150, cancel_at_period_end=True)
    assert cloud.billing.subscription(org.org_id)["cancel_at_period_end"] == 0
    send(
        "invoice.paid",
        now + 300,
        invoice_id="in_2",
        period_start=now,
        period_end=now + 30 * DAY,
    )
    assert cloud.billing.subscription(org.org_id)["status"] == "active"


def test_a_portal_change_of_plan_seats_and_cancellation_is_applied(stripe_app, stripe):
    org = build_org(stripe_app, plan="starter", seats=10)
    sub_id = on_stripe(stripe_app, org)
    now = stripe_app.clock.now()
    obj = {
        "id": sub_id,
        "status": "active",
        "cancel_at_period_end": True,
        "metadata": {"org_id": org.org_id, "plan_id": "starter", "seats": "10"},
        "items": {
            "data": [
                {
                    "id": "si_1",
                    "quantity": 30,
                    "price": {
                        "id": "price_portal_team",
                        "metadata": {"plan_id": "team"},
                        "product": "prod_x",
                    },
                    "current_period_start": now,
                    "current_period_end": now + 30 * DAY,
                }
            ]
        },
    }
    result = stripe_event(
        stripe_app, "evt_portal", "customer.subscription.updated", obj, created=now
    )
    assert result["outcome"] == "plan_changed"
    sub = stripe_app.billing.subscription(org.org_id)
    assert (sub["plan_id"], sub["candidate_seats"], sub["cancel_at_period_end"]) == (
        "team",
        30,
        1,
    )
    assert "billing.provider_change" in [
        r["action"] for r in stripe_app.audit.list(org.org_id, limit=100)
    ]
    # a lookup key names the plan as well (monosage_<plan>)
    obj["items"]["data"][0]["price"] = {
        "id": "price_lk",
        "lookup_key": "monosage_starter",
        "metadata": {},
    }
    obj["items"]["data"][0]["quantity"] = 12
    obj["cancel_at_period_end"] = False
    stripe_event(
        stripe_app,
        "evt_portal_2",
        "customer.subscription.updated",
        obj,
        created=now + 5,
    )
    sub = stripe_app.billing.subscription(org.org_id)
    assert (sub["plan_id"], sub["candidate_seats"], sub["cancel_at_period_end"]) == (
        "starter",
        12,
        0,
    )


# ----------------------------------------------------------------------------- P0-7 defaults


def test_the_stand_in_refuses_a_public_or_https_base_url(tmp_path):
    for base in (
        "https://aira.example.test",
        "http://aira.example.test",
        "https://127.0.0.1:8780",
    ):
        HOOKS.reset()
        with pytest.raises(StandInRefused):
            make_app(tmp_path / base.split("//")[1].replace(":", "_"), base_url=base)


def test_the_demo_override_runs_the_stand_in_with_a_loud_warning(tmp_path, caplog):
    HOOKS.reset()
    with caplog.at_level(logging.WARNING, logger="monosage_cloud.billing"):
        app = make_app(
            tmp_path, base_url="https://demo.example.test", allow_standin_billing=True
        )
    try:
        assert any("DEMO BILLING" in record.message for record in caplog.records)
    finally:
        app.close()


def test_a_real_provider_runs_on_https_and_the_stand_in_pages_are_closed(
    tmp_path, stripe
):
    HOOKS.reset()
    app = make_app(
        tmp_path,
        base_url="https://aira.example.test",
        billing_provider="stripe",
        stripe_secret_key=SECRET,
        stripe_api_base=stripe.base_url,
    )
    try:
        browser = Browser(app)
        status, _ = browser.call(
            "GET", "/billing/standin/checkout/cs_standin_" + "a" * 20
        )
        assert status == 404
        payload, signature = app.billing.standin.make_event(
            "invoice.paid", {"org_id": "x"}
        )
        status, _ = browser.call(
            "POST",
            "/api/billing/webhook/standin",
            payload,
            headers={"X-Aira-Signature": signature, "Content-Type": "application/json"},
        )
        assert status == 404, (
            "a stand-in event is not accepted from outside when Stripe bills"
        )
    finally:
        app.close()


def test_a_new_sign_up_gets_a_fourteen_day_trial_that_ends_in_a_clear_downgrade(
    tmp_path,
):
    HOOKS.reset()
    app = make_app(tmp_path, signup_plan=CloudConfig.signup_plan)  # the shipped default
    try:
        result = signup(app, "Fresh Org", "owner@fresh.test")
        org_id = result["org"]["org_id"]
        assert result["subscription"]["plan_id"] == "all_access"
        assert result["subscription"]["status"] == "trialing"
        sub = app.billing.subscription(org_id)
        assert sub["trial_end"] == app.clock.now() + 14 * DAY
        assert app.entitlements.resolve(org_id, fresh=True).automation_allowed
        app.clock.advance(days=14, seconds=1)
        # the trial's end counts before the scheduler has run
        ended = app.entitlements.resolve(org_id)
        assert ended.read_only and not ended.automation_allowed
        assert org_id in app.billing.process_due()["trial_ended"]
        assert app.billing.subscription(org_id)["status"] == "cancelled"
        with app.db.read() as connection:
            titles = [
                r[0]
                for r in connection.execute(
                    "SELECT title FROM org_notices WHERE org_id = ?", (org_id,)
                )
            ]
        assert "Your trial ended" in titles
    finally:
        app.close()


def test_the_trial_length_is_configurable_and_existing_organizations_keep_their_plan(
    tmp_path, monkeypatch
):
    HOOKS.reset()
    monkeypatch.setenv("AIRA_CLOUD_SIGNUP_TRIAL_DAYS", "21")
    app = make_app(tmp_path, signup_plan="all_access")
    try:
        org_id = signup(app, "Org", "owner@org.test")["org"]["org_id"]
        assert (
            app.billing.subscription(org_id)["trial_end"] == app.clock.now() + 21 * DAY
        )
        # an organization that already had All access with no end (made before this default) is left alone
        with app.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET status = 'active', trial_end = NULL WHERE org_id = ?",
                (org_id,),
            )
        app.entitlements.invalidate(org_id)
        app.clock.advance(days=40)
        assert org_id not in app.billing.process_due()["trial_ended"]
        assert app.billing.subscription(org_id)["status"] == "active"
    finally:
        app.close()


# ----------------------------------------------------------------------------- P0-5 ad top-ups


def ad_balance(org_id: str) -> int:
    from aira.suite.ads import service as ads
    from aira.suite.videos import db as videos_db

    with closing(videos_db.connect()) as sc:
        return int(ads.account(sc, org_id)["balance_micros"])


def ads_shop(app):
    from aira.suite import kit
    from aira.suite.videos import db as videos_db

    kit.forget_schema()
    videos_db.forget()
    shop = build_org(app, "Spice Shop", slug_email="shop.test")
    app.billing.subscribe_app(shop.owner, "ads")
    return shop


def test_a_paid_top_up_is_credited_once(stripe_app, stripe):
    shop = ads_shop(stripe_app)
    on_stripe(stripe_app, shop)
    result = stripe_app.billing.charge_ads_topup(shop.owner, 2000)
    assert result["status"] == "paid" and result["credited"] is True
    assert ad_balance(shop.org_id) == 20_000_000
    [invoice] = [c for c in stripe.calls if c["path"] == "/v1/invoices"]
    assert invoice["params"]["metadata"]["kind"] == "ads_topup"
    assert invoice["idempotency_key"] == f"aira-topup-{result['topup_id']}-invoice"
    [paying] = [c for c in stripe.calls if c["path"].endswith("/pay")]
    invoice_id = paying["path"].split("/")[3]
    # the webhook for the same invoice changes nothing (already credited)
    stripe_event(
        stripe_app,
        "evt_topup_paid",
        "invoice.paid",
        {
            "id": invoice_id,
            "customer": "cus_1",
            "amount_paid": 2000,
            "metadata": {"org_id": shop.org_id, "kind": "ads_topup"},
            "payment_intent": "pi_1",
        },
    )
    assert ad_balance(shop.org_id) == 20_000_000
    assert stripe_app.billing.subscription(shop.org_id)["status"] == "active"


def test_a_declined_top_up_credits_nothing(stripe_app, stripe):
    shop = ads_shop(stripe_app)
    on_stripe(stripe_app, shop)
    stripe.pay_failure = 402
    result = stripe_app.billing.charge_ads_topup(shop.owner, 2000)
    assert result["status"] == "open" and result["credited"] is False
    assert ad_balance(shop.org_id) == 0
    invoice_id = [c for c in stripe.calls if c["path"].endswith("/pay")][0][
        "path"
    ].split("/")[3]
    stripe_event(
        stripe_app,
        "evt_topup_failed",
        "invoice.payment_failed",
        {
            "id": invoice_id,
            "customer": "cus_1",
            "amount_due": 2000,
            "metadata": {"org_id": shop.org_id, "kind": "ads_topup"},
        },
    )
    topup = next(
        i for i in stripe_app.billing.invoices(shop.owner) if i["kind"] == "ads_topup"
    )
    assert topup["status"] == "failed" and ad_balance(shop.org_id) == 0
    assert stripe_app.billing.subscription(shop.org_id)["status"] == "active", (
        "a top-up is not the plan"
    )
    # Stripe's retry of the same invoice succeeds later: the money counts then, once
    paid = {
        "id": invoice_id,
        "customer": "cus_1",
        "amount_paid": 2000,
        "metadata": {"org_id": shop.org_id, "kind": "ads_topup"},
    }
    stripe_event(stripe_app, "evt_topup_retry", "invoice.paid", paid)
    stripe_event(stripe_app, "evt_topup_retry_again", "invoice.paid", paid)
    assert ad_balance(shop.org_id) == 20_000_000


def test_a_refunded_top_up_is_taken_back(stripe_app, stripe):
    shop = ads_shop(stripe_app)
    on_stripe(stripe_app, shop)
    stripe.pay_status = "open"  # paid asynchronously: the webhook settles it
    result = stripe_app.billing.charge_ads_topup(shop.owner, 3000)
    assert result["credited"] is False and ad_balance(shop.org_id) == 0
    invoice_id = [c for c in stripe.calls if c["path"].endswith("/pay")][0][
        "path"
    ].split("/")[3]
    paid = {
        "id": invoice_id,
        "customer": "cus_1",
        "amount_paid": 3000,
        "metadata": {"org_id": shop.org_id, "kind": "ads_topup"},
        "payment_intent": "pi_topup",
    }
    stripe_event(stripe_app, "evt_p", "invoice.paid", paid)
    stripe_event(
        stripe_app, "evt_p_again", "invoice.paid", paid
    )  # a redelivery under another id
    assert ad_balance(shop.org_id) == 30_000_000
    refund = {
        "id": "ch_1",
        "invoice": invoice_id,
        "payment_intent": "pi_topup",
        "amount_refunded": 3000,
        "customer": "cus_1",
    }
    stripe_event(stripe_app, "evt_r", "charge.refunded", refund)
    stripe_event(stripe_app, "evt_r_again", "charge.refunded", refund)
    assert ad_balance(shop.org_id) == 0
    topup = next(
        i for i in stripe_app.billing.invoices(shop.owner) if i["kind"] == "ads_topup"
    )
    assert topup["status"] == "refunded"


def test_creator_earnings_from_a_reversed_payment_are_held(cloud):
    from aira.suite.ads import service as ads
    from aira.suite.videos import db as videos_db

    ads_shop(cloud)
    with closing(videos_db.connect()) as sc:
        ads.credit_once(sc, "adv-1", 5_000_000, kind="topup", reference="t1")
        ads.credit_once(sc, "adv-1", 5_000_000, kind="topup", reference="t1")
        assert ads.account(sc, "adv-1")["balance_micros"] == 5_000_000

        def insert(table: str, values: dict[str, Any]) -> None:
            for column in sc.execute(f"PRAGMA table_info({table})").fetchall():
                name, kind, notnull, default = (
                    column[1],
                    column[2],
                    column[3],
                    column[4],
                )
                if name not in values and notnull and default is None:
                    values[name] = 0 if "INT" in kind.upper() else ""
            sc.execute(
                f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                tuple(values.values()),
            )

        insert("ad_campaigns", {"id": "c1", "home": "adv-1", "name": "C"})
        insert(
            "ad_impressions",
            {
                "id": "imp1",
                "campaign_id": "c1",
                "home": "adv-1",
                "channel_id": "ch1",
                "month": "2001-01",
                "day": "2001-01-02",
                "status": "counted",
                "creator_micros": 1_000,
                "cost_micros": 2_000,
                "earning_status": "estimated",
            },
        )
        sc.commit()
        ads.reverse_once(
            sc, "adv-1", 8_000_000, reference="t1"
        )  # spent 3 of 5, then 8 taken back
        ads.reverse_once(sc, "adv-1", 8_000_000, reference="t1")
        assert ads.account(sc, "adv-1")["balance_micros"] == -3_000_000
        ads.close_month(sc, "2001-01", "test")
        status = sc.execute(
            "SELECT earning_status FROM ad_impressions WHERE id = 'imp1'"
        ).fetchone()[0]
        assert status == "held"
        assert ads.balances(sc, "ch1")["payable_micros"] == 0
        ads.credit_once(
            sc, "adv-1", 3_000_000, kind="payment", reference="t2"
        )  # pays what it owes
        status = sc.execute(
            "SELECT earning_status FROM ad_impressions WHERE id = 'imp1'"
        ).fetchone()[0]
        assert status == "confirmed"


# ----------------------------------------------------------------------------- P0-6 payout details


def test_payout_details_are_encrypted_with_the_kms_key_and_shown_masked(cloud):
    from aira.suite import kit
    from aira.suite.ads import service as ads
    from aira.suite.videos import db as videos_db
    from aira.suite.videos import service as videos
    from aira.suite.videos import store

    kit.forget_schema()
    videos_db.forget()
    ident = {
        "key": "m:cara",
        "home": "org-cara",
        "profile": {"name": "Cara", "age_group": "adult"},
    }
    full = "Bank 1234 5678 9012, IFSC HDFC0000123"
    with closing(videos_db.connect()) as sc:
        cara = videos.me(sc, ident)
        updated = videos.update_channel(sc, cara, {"payout_details": full})
        assert (
            updated["payout_details"] == "•••• 9012"
            and updated["payout_details_saved"] is True
        )
        stored = sc.execute(
            "SELECT payout_details FROM vi_channels WHERE id = ?", (cara["id"],)
        ).fetchone()[0]
        assert stored.startswith(store.PAYOUT_PREFIX) and "5678" not in stored
        assert store.payout_details(sc, cara["id"]) == full
        # sending the mask back keeps what was saved
        videos.update_channel(
            sc, videos.channel(sc, cara["id"]), {"payout_details": "•••• 9012"}
        )
        assert store.payout_details(sc, cara["id"]) == full
        earnings = ads.earnings(sc, videos.viewer_of(videos.channel(sc, cara["id"])))
        assert earnings["payout_details"] == "•••• 9012"
        # rows written before encryption: a channel and a payout request in the clear
        sc.execute(
            "UPDATE vi_channels SET payout_details = 'cara@okhdfc' WHERE id = ?",
            (cara["id"],),
        )
        sc.execute(
            "INSERT INTO ad_payouts (id, channel_id, home, amount_micros, details, status, requested_at) VALUES (?, ?, 'org-cara', 5000000, 'cara@okhdfc', 'requested', 'x')",
            ("p" * 32, cara["id"]),
        )
        sc.commit()
    videos_db.forget()  # the next first connection seals what is still in the clear
    with closing(videos_db.connect()) as sc:
        for value in sc.execute(
            "SELECT payout_details FROM vi_channels UNION ALL SELECT details FROM ad_payouts"
        ):
            assert store.is_sealed(value[0]), value
        assert videos.channel(sc, cara["id"])["payout_details"] == "ca•••@okhdfc"
        [queued] = ads.payouts(sc, "requested")
        assert (
            queued["details"] == "cara@okhdfc"
            and queued["details_masked"] == "ca•••@okhdfc"
        )
    # hosted, the key is a data key wrapped by the control plane's master key: nothing next to the database
    with cloud.db.read() as connection:
        assert connection.execute(
            "SELECT 1 FROM cloud_kv WHERE key = 'payout_data_key'"
        ).fetchone()
    assert not (cloud.config.data_root / "videos-payout.key").exists()


def test_the_payout_mask():
    from aira.suite.videos.store import mask_payout

    assert mask_payout("ravi.k@oksbi") == "ra•••@oksbi"
    assert mask_payout("IBAN DE89 3704 0044 0532 0130 00") == "•••• 3000"
    assert mask_payout("A/C 50100123456789 IFSC HDFC0001234") == "•••• 6789"
    assert mask_payout("cash") == "ca•••" and mask_payout("") == ""
