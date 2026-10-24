"""Subscription lifecycle: checkout, upgrade, downgrade, dunning, cancellation, webhooks."""

from __future__ import annotations

import json

import pytest
from cloud_support import (
    PASSWORD,
    add_member,
    audit_actions,
    build_org,
    pay,
    signup,
    webhook,
)  # noqa: F401

from aira_cloud.billing_providers import check_signature, sign
from aira_cloud.config import DAY
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PlanRequired,
    Unauthorized,
    ValidationFailed,
)


def status(cloud, org_id):
    return cloud.billing.subscription(org_id)["status"]


def test_checkout_webhook_activates_at_once_and_writes_entitlements(cloud):
    result = signup(cloud)
    org_id, owner = result["org"]["org_id"], result["owner"]
    started = cloud.billing.start_checkout(owner, "team", 30)
    assert started["checkout_url"].startswith(
        "http://127.0.0.1:8780/billing/standin/checkout/"
    )
    assert started["quote"]["monthly_cents"] == 30 * 1100
    assert status(cloud, org_id) == "trialing"
    pay(cloud, org_id, "team", 30)
    sub = cloud.billing.subscription(org_id)
    assert (
        sub["status"] == "active"
        and sub["plan_id"] == "team"
        and sub["candidate_seats"] == 30
    )
    ent = cloud.entitlements.resolve(org_id)
    assert (
        ent.limit("candidate_seats") == 30
        and ent.has("sources.linkedin")
        and ent.limit("apps_per_org_month") == 15000
    )
    assert "subscription.trialing_to_active" in audit_actions(
        cloud, org_id, "subscription."
    )


def test_webhooks_are_idempotent_and_signature_checked(cloud):
    result = signup(cloud)
    org_id = result["org"]["org_id"]
    payload, signature = cloud.billing.standin.make_event(
        "checkout.completed",
        {
            "org_id": org_id,
            "plan_id": "starter",
            "seats": 10,
            "customer_id": "c",
            "subscription_id": "s",
        },
    )
    assert (
        cloud.billing.handle_webhook("standin", payload, signature)["processed"] is True
    )
    assert cloud.billing.handle_webhook("standin", payload, signature) == {
        "duplicate": True
    }
    with pytest.raises(Unauthorized):
        cloud.billing.handle_webhook("standin", payload, "t=1,v1=deadbeef")
    stale_payload, _ = cloud.billing.standin.make_event(
        "invoice.paid", {"org_id": org_id}
    )
    old = sign(cloud.billing.standin.secret, stale_payload, cloud.clock.now() - 3600)
    with pytest.raises(Unauthorized):
        cloud.billing.handle_webhook("standin", stale_payload, old)
    with pytest.raises(NotFound):
        cloud.billing.handle_webhook("paddle", b"{}", "")
    tampered = payload.replace(b"starter", b"team000")
    with pytest.raises(Unauthorized):
        cloud.billing.handle_webhook("standin", tampered, signature)


def test_stripe_signature_scheme():
    payload = b'{"id":"evt_1","type":"invoice.paid"}'
    header = sign("whsec_test", payload, 1_000_000)
    check_signature("whsec_test", payload, header, 1_000_100)
    with pytest.raises(Unauthorized):
        check_signature("whsec_other", payload, header, 1_000_100)
    with pytest.raises(Unauthorized):
        check_signature("whsec_test", payload, header, 1_000_000 + 301)
    with pytest.raises(Unauthorized):
        check_signature("whsec_test", payload, "garbage", 1_000_000)
    # Stripe may send several v1 signatures during secret rotation: any matching one is enough
    good = header.split(",")[1]
    check_signature("whsec_test", payload, f"t=1000000,v1=00,{good}", 1_000_000)


def test_stripe_events_are_normalized():
    from aira_cloud.billing_providers import StripeProvider
    from aira_cloud.config import CloudConfig, Clock

    provider = StripeProvider(CloudConfig(), Clock())
    paid = provider.normalize(
        {
            "id": "evt_1",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_1",
                    "customer": "cus_1",
                    "subscription": "sub_1",
                    "amount_paid": 27500,
                    "currency": "usd",
                    "lines": {"data": [{"period": {"start": 100, "end": 200}}]},
                    "subscription_details": {"metadata": {"org_id": "org1"}},
                }
            },
        }
    )
    assert (
        paid["type"] == "invoice.paid"
        and paid["org_id"] == "org1"
        and paid["period_end"] == 200
        and paid["amount_cents"] == 27500
    )
    done = provider.normalize(
        {
            "id": "evt_2",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_1",
                    "subscription": "sub_1",
                    "metadata": {"org_id": "org1", "plan_id": "team", "seats": "40"},
                }
            },
        }
    )
    assert done == {
        "id": "evt_2",
        "type": "checkout.completed",
        "org_id": "org1",
        "plan_id": "team",
        "seats": 40,
        "customer_id": "cus_1",
        "subscription_id": "sub_1",
    }
    assert (
        provider.normalize(
            {"id": "e", "type": "charge.refunded", "data": {"object": {}}}
        )
        is None
    )


def test_quote_validates_seats_and_plan(cloud):
    result = signup(cloud)
    org_id = result["org"]["org_id"]
    with pytest.raises(ValidationFailed):
        cloud.billing.quote(org_id, "enterprise", 100)
    with pytest.raises(ValidationFailed):
        cloud.billing.quote(org_id, "team", 5)  # below the Team minimum
    with pytest.raises(PlanRequired) as raised:
        cloud.billing.quote(org_id, "starter", 30)  # Starter tops out at 25
    assert raised.value.extra["required_plan"] == "team"
    assert cloud.billing.quote(org_id, "starter", 25)["monthly_cents"] == 25 * 1400


def test_upgrade_applies_at_once_with_a_prorated_charge(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    sub = cloud.billing.subscription(org.org_id)
    cloud.clock.advance(days=15)  # halfway through the 30-day period
    quote = cloud.billing.quote(org.org_id, "team", 25)
    expected = round((25 * 1100 - 10 * 1400) * 0.5)
    assert abs(quote["prorated_charge_cents"] - expected) <= 25
    outcome = cloud.billing.change_plan(org.owner, "team", 25)
    assert outcome["applied"] and not outcome["scheduled"]
    after = cloud.billing.subscription(org.org_id)
    assert after["plan_id"] == "team" and after["candidate_seats"] == 25
    assert cloud.entitlements.resolve(org.org_id).has("sources.linkedin"), (
        "an upgrade takes effect immediately"
    )
    invoices = cloud.billing.invoices(org.owner)
    assert any(
        i["kind"] == "proration"
        and i["amount_cents"] == outcome["prorated_charge_cents"]
        for i in invoices
    )
    assert sub["period_end"] == after["period_end"], (
        "the billing period does not restart"
    )


def test_only_the_owner_and_billing_contact_manage_the_subscription(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    for role in (org.admin, org.coach, org.candidate, org.auditor):
        with pytest.raises(Forbidden):
            cloud.billing.change_plan(role, "team", 25)
        with pytest.raises(Forbidden):
            cloud.billing.start_checkout(role, "team", 25)
    cloud.billing.change_plan(org.billing, "team", 25)


def test_downgrade_waits_for_period_end_then_pauses_automation_until_within_limits(
    cloud,
):
    org = build_org(cloud, plan="team", seats=25)
    for index in range(5):  # 7 candidates in total, on a plan that will allow 5
        add_member(cloud, org.owner, f"extra{index}@acme.test", "candidate")
    assert (
        cloud.gate.automation_block(org.org_id, org.candidate.member_id) is not None
    )  # consent not given yet
    for who in ("candidate",):
        cloud.tenancy.record_consent(
            getattr(org, who), ["automation", "coach_access", "sensitive_policy"]
        )
    assert cloud.gate.automation_block(org.org_id, org.candidate.member_id) is None
    result = cloud.billing.change_plan(org.owner, "starter", 5)
    assert (
        result["scheduled"]
        and result["effective_at"]
        == cloud.billing.subscription(org.org_id)["period_end"]
    )
    assert "candidate seats" in result["would_exceed"]
    assert cloud.entitlements.resolve(org.org_id).plan_id == "team", (
        "a downgrade does not apply before the period ends"
    )
    assert cloud.gate.automation_block(org.org_id, org.candidate.member_id) is None
    cloud.clock.advance(days=31)
    cloud.billing.process_due()
    ent = cloud.entitlements.resolve(org.org_id, fresh=True)
    assert ent.plan_id == "starter" and ent.limit("candidate_seats") == 5
    assert ent.over_limits and any(
        "candidate seats" in item for item in ent.over_limits
    )
    block = cloud.gate.automation_block(org.org_id, org.candidate.member_id)
    assert block and block[0] == "over_limits"
    assert cloud.mail.latest("owner@acme.test", "downgrade_pause")
    # removing candidates brings the organization back within limits and automation resumes
    cloud.tenancy.remove_member(org.owner, org.other.member_id)
    members = [
        m
        for m in cloud.tenancy.list_members(org.owner, role="candidate")
        if m["email"].startswith("extra")
    ]
    cloud.tenancy.remove_member(org.owner, members[0]["member_id"])
    assert cloud.entitlements.resolve(org.org_id, fresh=True).over_limits == ()
    assert cloud.gate.automation_block(org.org_id, org.candidate.member_id) is None
    assert "automation.resumed_within_limits" in audit_actions(
        cloud, org.org_id, "automation."
    )


def test_downgrade_can_be_cancelled_before_it_applies(cloud):
    org = build_org(cloud, plan="team", seats=25)
    cloud.billing.change_plan(org.owner, "starter", 10)
    cloud.billing.cancel_pending_change(org.owner)
    cloud.clock.advance(days=31)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["plan_id"] == "team"


def test_payment_failure_stops_automation_then_read_only_then_deletion(cloud):
    org = build_org(cloud, plan="team", seats=25)
    cloud.tenancy.record_consent(
        org.candidate, ["automation", "coach_access", "sensitive_policy"]
    )
    assert cloud.gate.automation_block(org.org_id, org.candidate.member_id) is None
    webhook(
        cloud,
        "invoice.payment_failed",
        org_id=org.org_id,
        invoice_id="in_1",
        amount_cents=27500,
    )
    assert status(cloud, org.org_id) == "past_due"
    block = cloud.gate.automation_block(org.org_id, org.candidate.member_id)
    assert block and block[0] == "subscription", "automation stops at once"
    assert cloud.entitlements.resolve(org.org_id, fresh=True).read_only is False, (
        "read access continues"
    )
    for email in ("owner@acme.test", "billing@acme.test"):
        assert cloud.mail.latest(email, "payment_failed"), f"{email} is told"
    cloud.clock.advance(days=13)
    cloud.billing.process_due()
    assert status(cloud, org.org_id) == "past_due"
    cloud.clock.advance(days=1)
    cloud.billing.process_due()
    assert status(cloud, org.org_id) == "read_only"
    assert cloud.entitlements.resolve(org.org_id, fresh=True).read_only is True
    assert cloud.mail.latest("billing@acme.test", "read_only")
    cloud.clock.advance(days=46)  # 60 days after the first failure
    cloud.billing.process_due()
    sub = cloud.billing.subscription(org.org_id)
    assert (
        sub["deletion_scheduled_at"]
        and sub["deletion_scheduled_at"] > cloud.clock.now()
    )
    for email in ("owner@acme.test", "billing@acme.test"):
        assert cloud.mail.latest(email, "deletion_notice"), (
            f"{email} gets one last notice"
        )
    cloud.clock.advance(days=cloud.config.deletion_grace_days + 1)
    cloud.billing.process_due()
    assert status(cloud, org.org_id) == "deleted"


def test_payment_recovery_restores_service_and_clears_dunning(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    webhook(cloud, "invoice.payment_failed", org_id=org.org_id)
    cloud.clock.advance(days=20)
    cloud.billing.process_due()
    assert status(cloud, org.org_id) == "read_only"
    webhook(
        cloud,
        "invoice.paid",
        org_id=org.org_id,
        invoice_id="in_9",
        amount_cents=14000,
        period_start=cloud.clock.now(),
        period_end=cloud.clock.now() + 30 * DAY,
    )
    sub = cloud.billing.subscription(org.org_id)
    assert (
        sub["status"] == "active"
        and sub["past_due_since"] is None
        and sub["read_only_at"] is None
    )
    assert cloud.entitlements.resolve(org.org_id, fresh=True).read_only is False


def test_every_transition_names_who_or_what_caused_it(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    webhook(cloud, "invoice.payment_failed", org_id=org.org_id)
    cloud.clock.advance(days=15)
    cloud.billing.process_due()
    with cloud.db.read() as connection:
        events = [
            dict(r)
            for r in connection.execute(
                "SELECT from_status, to_status, trigger_type FROM subscription_events WHERE org_id = ? ORDER BY id",
                (org.org_id,),
            )
        ]
    assert [(e["from_status"], e["to_status"], e["trigger_type"]) for e in events] == [
        ("", "trialing", "person"),
        ("trialing", "active", "webhook"),
        ("active", "past_due", "webhook"),
        ("past_due", "read_only", "scheduler"),
    ]
    actions = audit_actions(cloud, org.org_id, "subscription.")
    assert {
        "subscription.active_to_past_due",
        "subscription.past_due_to_read_only",
    } <= set(actions)


def test_trial_expires_into_the_export_window_then_deletion(cloud):
    result = signup(cloud)
    org_id = result["org"]["org_id"]
    cloud.clock.advance(days=12)
    cloud.billing.process_due()
    assert cloud.mail.latest("owner@acme.test", "trial_ending"), (
        "a notice goes out three days before the trial ends"
    )
    cloud.clock.advance(days=3)
    cloud.billing.process_due()
    sub = cloud.billing.subscription(org_id)
    assert (
        sub["status"] == "cancelled"
        and sub["export_until"] == cloud.clock.now() + 30 * DAY
    )
    assert cloud.entitlements.resolve(org_id, fresh=True).read_only is True
    cloud.clock.advance(days=31)
    cloud.billing.process_due()
    assert status(cloud, org_id) == "deleted"


def test_cancellation_keeps_service_until_period_end_then_30_day_export_window(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    cloud.billing.cancel(org.owner)
    assert status(cloud, org.org_id) == "active"
    assert cloud.billing.subscription(org.org_id)["cancel_at_period_end"] == 1
    cloud.billing.reactivate(org.owner)
    assert cloud.billing.subscription(org.org_id)["cancel_at_period_end"] == 0
    cloud.billing.cancel(org.owner)
    cloud.clock.advance(days=31)
    cloud.billing.process_due()
    sub = cloud.billing.subscription(org.org_id)
    assert sub["status"] == "cancelled" and sub["export_until"] > cloud.clock.now()
    assert cloud.mail.latest("owner@acme.test", "subscription_cancelled")
    # export stays available during the window
    assert cloud.lifecycle.export_organization(org.owner)["size"] > 0
    with pytest.raises(Conflict):
        cloud.billing.reactivate(org.owner)  # a cancelled plan needs checkout again
    pay(cloud, org.org_id, "starter", 10)
    assert status(cloud, org.org_id) == "active"


def test_active_subscription_renews_and_old_periods_roll(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    first_end = cloud.billing.subscription(org.org_id)["period_end"]
    cloud.clock.advance(days=31)
    done = cloud.billing.process_due()
    assert org.org_id in done["renewed"]
    sub = cloud.billing.subscription(org.org_id)
    assert sub["status"] == "active" and sub["period_end"] > first_end


def test_overage_is_invoiced_once_for_plans_that_allow_it(cloud):
    org = build_org(cloud, plan="team", seats=25)
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overage_enabled = 1 WHERE org_id = ?",
            (org.org_id,),
        )
    cloud.entitlements.invalidate(org.org_id)
    cloud.usage.reserve(org.org_id, "applications", 15000, monthly_limit=15000)
    cloud.usage.reserve(
        org.org_id, "applications", 40, monthly_limit=15000, allow_overage=True
    )
    cloud.clock.advance(days=35)
    cloud.billing.process_due()
    cloud.billing.process_due()
    overage = [i for i in cloud.billing.invoices(org.owner) if i["kind"] == "overage"]
    assert len(overage) == 1 and overage[0]["amount_cents"] == 40 * 10


def test_operator_actions_extend_trials_credit_and_activate_enterprise(cloud):
    result = signup(cloud)
    org_id = result["org"]["org_id"]
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    op = cloud.identity.principal_for_token(
        cloud.identity.operator_login("ops@aira.test", PASSWORD)["token"],
        kind="operator",
    )
    support = cloud.identity.create_operator(
        "support@aira.test", PASSWORD, role="support"
    )
    assert support
    new_end = cloud.billing.extend_trial(op, org_id, 10)
    assert new_end == cloud.billing.subscription(org_id)["trial_end"]
    cloud.billing.issue_credit(op, org_id, 5000, "goodwill")
    summary = cloud.billing.activate_enterprise(
        op,
        org_id,
        seats=400,
        overrides={"apps_per_candidate_day": 80},
        invoice_cents=1_000_000,
        reference="PO-1",
    )
    assert summary["plan_id"] == "enterprise" and summary["status"] == "active"
    ent = cloud.entitlements.resolve(org_id, fresh=True)
    assert (
        ent.limit("apps_per_candidate_day") == 80
        and ent.limit("candidate_seats") == 400
        and ent.has("auth.scim")
    )
    invoice = next(
        i for i in cloud.billing.invoices(result["owner"]) if i["kind"] == "enterprise"
    )
    cloud.billing.mark_invoice_paid(op, org_id, invoice["invoice_id"])
    support_principal = cloud.identity.principal_for_token(
        cloud.identity.operator_login("support@aira.test", PASSWORD)["token"],
        kind="operator",
    )
    with pytest.raises(Forbidden):
        cloud.billing.extend_trial(support_principal, org_id, 5)
    actions = [row["action"] for row in cloud.platform_audit.list(org_id=org_id)]
    assert {
        "billing.extend_trial",
        "billing.credit",
        "billing.enterprise_activate",
        "billing.invoice_paid",
    } <= set(actions)
    assert cloud.platform_audit.verify()["ok"]


def test_the_standin_provider_signs_what_it_delivers(cloud):
    payload, header = cloud.billing.standin.make_event("invoice.paid", {"org_id": "x"})
    check_signature(cloud.billing.standin.secret, payload, header, cloud.clock.now())
    assert json.loads(payload)["type"] == "invoice.paid"


def test_the_owner_turns_overage_on_for_team_and_a_starter_plan_cannot(cloud):
    team = build_org(cloud, "Overage Team", plan="team", seats=25)
    assert cloud.billing.set_overage(team.owner, True) == {"overage_enabled": True}
    assert cloud.entitlements.resolve(team.org_id).overage_enabled is True
    assert "billing.overage_enabled" in audit_actions(cloud, team.org_id, "billing.")
    cloud.billing.set_overage(team.owner, False)
    assert cloud.entitlements.resolve(team.org_id).overage_enabled is False
    with pytest.raises(Forbidden):
        cloud.billing.set_overage(team.coach, True)

    starter = build_org(cloud, "Overage Starter", plan="starter", seats=5)
    with pytest.raises(PlanRequired):
        cloud.billing.set_overage(starter.owner, True)


def test_the_overage_route_toggles_and_only_a_billing_manager_may(cloud):
    from cloud_support import sign_in

    org = build_org(cloud, "Overage Route", plan="team", seats=25)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("POST", "/api/org/billing/overage", {"enabled": True})
    assert status == 200 and body == {"overage_enabled": True}
    assert (
        owner.call("GET", "/api/org/billing")[1]["billing"]["overage_enabled"] is True
    )
    status, body = owner.call("POST", "/api/org/billing/overage", {"enabled": False})
    assert status == 200 and body == {"overage_enabled": False}
    coach = sign_in(cloud, org.coach)
    assert coach.call("POST", "/api/org/billing/overage", {"enabled": True})[0] == 403
