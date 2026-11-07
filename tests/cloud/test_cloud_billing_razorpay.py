"""Razorpay as the plan billing provider, against the loopback stub (tests/cloud_razorpay_stub.py).

The whole lifecycle: sign-up, checkout (a subscription the customer authorises), activation, the cycle's charge, an
upgrade, metered overage billed as a subscription add-on, a halted payment, recovery and cancellation; and ad
top-ups credited only when the charge that carries them is paid.
"""

from __future__ import annotations

from contextlib import closing

import pytest
from cloud_razorpay_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub
from cloud_support import HOOKS, Browser, make_app, signup

from monosage_cloud.billing_providers import RazorpayProvider
from monosage_cloud.config import DAY, CloudConfig, FakeClock
from monosage_cloud.errors import Unauthorized
from monosage_cloud.plans import INR_SEAT_PAISE


@pytest.fixture
def razorpay():
    stub = RazorpayStub().start()
    yield stub
    stub.stop()


@pytest.fixture
def rzp(tmp_path, razorpay):
    HOOKS.reset()
    app = make_app(
        tmp_path,
        billing_provider="razorpay",
        razorpay_key_id=KEY_ID,
        razorpay_key_secret=KEY_SECRET,
        razorpay_webhook_secret=WEBHOOK_SECRET,
        razorpay_api_base=razorpay.base_url,
    )
    yield app
    app.close()


def deliver(app, event: tuple[bytes, str, str]) -> dict:
    body, signature, event_id = event
    return app.billing.handle_webhook("razorpay", body, signature, event_id=event_id)


def activate(app, razorpay, plan: str = "starter", seats: int = 10):
    result = signup(app)
    owner = result["owner"]
    started = app.billing.start_checkout(owner, plan, seats)
    sub_id = started["session_id"]
    now = app.clock.now()
    outcome = deliver(
        app,
        razorpay.event(
            "subscription.activated",
            sub_id,
            created_at=now,
            status="active",
            current_start=now,
            current_end=now + 30 * DAY,
            customer_id="cust_rzp_auth",
        ),
    )
    assert outcome["outcome"] == "activated"
    return owner, sub_id, started


def test_the_razorpay_subscription_lifecycle(rzp, razorpay):
    app = rzp
    owner, sub_id, started = activate(app, razorpay)
    org_id = owner.org_id
    # what was sent: a customer, a monthly plan in paise, a subscription for the seats with the organization in notes
    [customer] = razorpay.find("POST", "/v1/customers")
    assert customer["authorized"] and customer["content_type"] == "application/json"
    assert (
        customer["body"]["notes"] == {"org_id": org_id}
        and customer["body"]["fail_existing"] == "0"
    )
    [plan] = razorpay.find("POST", "/v1/plans")
    assert plan["body"]["period"] == "monthly" and plan["body"]["interval"] == 1
    assert (
        plan["body"]["item"]["amount"] == INR_SEAT_PAISE["starter"]
        and plan["body"]["item"]["currency"] == "INR"
    )
    [created] = razorpay.find("POST", "/v1/subscriptions")
    body = created["body"]
    assert (
        body["quantity"] == 10
        and body["total_count"] == 120
        and body["customer_notify"] == 1
    )
    assert body["notes"]["org_id"] == org_id and body["notes"]["plan_id"] == "starter"
    assert started["checkout_url"] == razorpay.subscriptions[sub_id]["short_url"]
    sub = app.billing.subscription(org_id)
    assert (sub["status"], sub["plan_id"], sub["candidate_seats"]) == (
        "active",
        "starter",
        10,
    )
    assert (
        sub["billing_provider"] == "razorpay"
        and sub["billing_subscription_id"] == sub_id
    )
    start = sub["period_start"]

    # the cycle's charge is recorded as a paid invoice in rupees; a redelivery (same event id) is a no-op
    charged = razorpay.event(
        "subscription.charged",
        sub_id,
        created_at=start + 60,
        payment={"amount": 10 * INR_SEAT_PAISE["starter"]},
    )
    assert deliver(app, charged)["processed"] is True
    assert deliver(app, charged) == {"duplicate": True}
    [paid] = [i for i in app.billing.invoices(owner) if i["kind"] == "subscription"]
    assert (
        paid["status"] == "paid"
        and paid["currency"] == "inr"
        and paid["amount_cents"] == 119_900 * 10
    )

    # an upgrade: PATCH with the Team plan and 25 seats now, the prorated difference as an add-on; it applies when
    # Razorpay confirms the change
    app.clock.advance(days=10)
    outcome = app.billing.change_plan(owner, "team", 25)
    assert outcome["pending_payment"] is True
    assert app.billing.subscription(org_id)["plan_id"] == "starter"
    [patch] = razorpay.find("PATCH", f"/v1/subscriptions/{sub_id}")
    assert (
        patch["body"]["quantity"] == 25 and patch["body"]["schedule_change_at"] == "now"
    )
    team_plan = patch["body"]["plan_id"]
    assert razorpay.plans[team_plan]["item"]["amount"] == INR_SEAT_PAISE["team"]
    [proration] = razorpay.find("POST", f"/v1/subscriptions/{sub_id}/addons")
    assert proration["body"]["item"]["currency"] == "INR"
    assert proration["body"]["item"]["amount"] == outcome["prorated_charge_cents"] * 85
    deliver(
        app,
        razorpay.event(
            "subscription.updated",
            sub_id,
            created_at=app.clock.now(),
            plan_id=team_plan,
            quantity=25,
        ),
    )
    sub = app.billing.subscription(org_id)
    assert (sub["plan_id"], sub["candidate_seats"], sub["pending_upgrade"]) == (
        "team",
        25,
        "",
    )

    # metered overage is billed as an add-on for the provider's period once the next cycle is charged
    with app.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overage_enabled = 1 WHERE org_id = ?", (org_id,)
        )
    app.entitlements.invalidate(org_id)
    limit = app.entitlements.resolve(org_id, fresh=True).limit("apps_per_org_month")
    app.usage.reserve(org_id, "applications", limit, monthly_limit=limit)
    app.usage.reserve(
        org_id, "applications", 10, monthly_limit=limit, allow_overage=True
    )
    app.clock.set(start + 30 * DAY + 3600)
    deliver(
        app,
        razorpay.event(
            "subscription.charged",
            sub_id,
            created_at=app.clock.now(),
            current_start=start + 30 * DAY,
            current_end=start + 60 * DAY,
            payment={"amount": 25 * INR_SEAT_PAISE["team"]},
        ),
    )
    app.billing.process_due()
    addons = razorpay.find("POST", f"/v1/subscriptions/{sub_id}/addons")
    overage = addons[-1]["body"]["item"]
    assert len(addons) == 2 and "overage" in overage["name"].lower()
    assert overage["amount"] == 10 * 10 * 85 and overage["currency"] == "INR"
    [billed] = [i for i in app.billing.invoices(owner) if i["kind"] == "overage"]
    assert (billed["period_start"], billed["period_end"]) == (start, start + 30 * DAY)

    # a halted subscription (retries exhausted) is past due; the next successful charge restores it
    deliver(
        app,
        razorpay.event(
            "subscription.halted",
            sub_id,
            created_at=app.clock.now() + 10,
            status="halted",
        ),
    )
    assert app.billing.subscription(org_id)["status"] == "past_due"
    assert app.mail.latest("owner@acme.test", "payment_failed")
    deliver(
        app,
        razorpay.event(
            "subscription.charged",
            sub_id,
            created_at=app.clock.now() + 20,
            status="active",
            payment={"amount": 25 * INR_SEAT_PAISE["team"]},
        ),
    )
    assert app.billing.subscription(org_id)["status"] == "active"

    # Razorpay has no customer portal: the in-app billing page stands in
    assert app.billing.portal(owner)["url"].endswith("/#/org/billing")

    # cancelling stops renewal at the cycle's end; Razorpay's cancellation ends the subscription
    app.billing.cancel(owner)
    [cancel] = razorpay.find("POST", f"/v1/subscriptions/{sub_id}/cancel")
    assert cancel["body"] == {"cancel_at_cycle_end": True}
    deliver(
        app,
        razorpay.event(
            "subscription.cancelled",
            sub_id,
            created_at=app.clock.now() + 30,
            status="cancelled",
        ),
    )
    assert app.billing.subscription(org_id)["status"] == "cancelled"


def test_the_webhook_route_reads_razorpays_signature_and_event_id(rzp, razorpay):
    app = rzp
    owner, sub_id, _ = activate(app, razorpay)
    body, signature, event_id = razorpay.event(
        "subscription.halted", sub_id, created_at=app.clock.now() + 5, status="halted"
    )
    browser = Browser(app)
    status, _ = browser.call(
        "POST",
        "/api/billing/webhook/razorpay",
        body,
        headers={
            "X-Razorpay-Signature": "0" * 64,
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )
    assert status == 401
    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": event_id,
        "Content-Type": "application/json",
    }
    status, answer = browser.call(
        "POST", "/api/billing/webhook/razorpay", body, headers=headers
    )
    assert status == 200 and answer["processed"] is True
    assert app.billing.subscription(owner.org_id)["status"] == "past_due"
    status, answer = browser.call(
        "POST", "/api/billing/webhook/razorpay", body, headers=headers
    )
    assert status == 200 and answer == {"duplicate": True}


def test_a_replaced_razorpay_checkout_is_cancelled(rzp, razorpay):
    owner = signup(rzp)["owner"]
    first = rzp.billing.start_checkout(owner, "starter", 10)
    rzp.billing.start_checkout(owner, "team", 30)
    [cancel] = razorpay.find("POST", f"/v1/subscriptions/{first['session_id']}/cancel")
    assert cancel["body"] == {"cancel_at_cycle_end": False}
    assert razorpay.subscriptions[first["session_id"]]["status"] == "cancelled"


def test_razorpay_ad_top_ups_count_when_the_charge_that_carries_them_is_paid(
    rzp, razorpay
):
    from aira.suite import kit
    from aira.suite.ads import service as ads
    from aira.suite.videos import db as videos_db

    kit.forget_schema()
    videos_db.forget()
    app = rzp
    owner, sub_id, _ = activate(app, razorpay, plan="team", seats=25)
    app.billing.subscribe_app(owner, "ads")

    def balance() -> int:
        with closing(videos_db.connect()) as sc:
            return int(ads.account(sc, owner.org_id)["balance_micros"])

    result = app.billing.charge_ads_topup(owner, 2000)
    assert (
        result["status"] == "scheduled"
        and result["credited"] is False
        and balance() == 0
    )
    addon = razorpay.find("POST", f"/v1/subscriptions/{sub_id}/addons")[-1]["body"]
    assert addon["item"]["amount"] == 2000 * 85 and addon["quantity"] == 1
    # the subscription's charge fails: nothing is credited
    deliver(
        app,
        razorpay.event(
            "subscription.pending",
            sub_id,
            created_at=app.clock.now() + 5,
            status="pending",
        ),
    )
    assert balance() == 0
    # the next charge succeeds and carries the add-on: the top-up is credited, once
    app.clock.advance(seconds=60)
    charged = razorpay.event(
        "subscription.charged",
        sub_id,
        created_at=app.clock.now(),
        status="active",
        payment={"amount": 99_000},
    )
    deliver(app, charged)
    assert balance() == 20_000_000
    payment_id = __import__("json").loads(charged[0])["payload"]["payment"]["entity"][
        "id"
    ]
    # the payment is refunded: the credit is taken back
    deliver(
        app,
        razorpay.refund_event(
            payment_id=payment_id, amount=99_000, created_at=app.clock.now() + 5
        ),
    )
    assert balance() == 0
    topup = next(i for i in app.billing.invoices(owner) if i["kind"] == "ads_topup")
    assert topup["status"] == "refunded"


def test_the_signature_is_an_hmac_of_the_raw_body():
    provider = RazorpayProvider(
        CloudConfig(razorpay_webhook_secret=WEBHOOK_SECRET), FakeClock(1_800_000_000)
    )
    body, signature, _ = RazorpayStub.sign(
        {"event": "subscription.charged", "payload": {}}
    )
    assert provider.verify_webhook(body, signature)["event"] == "subscription.charged"
    with pytest.raises(Unauthorized):
        provider.verify_webhook(body + b" ", signature)
    _, other, _ = RazorpayStub.sign({"event": "x"}, secret="another")
    with pytest.raises(Unauthorized):
        provider.verify_webhook(body, other)
