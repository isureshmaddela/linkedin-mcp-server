# ruff: noqa: F811  (the `cloud` fixture is imported from cloud_support and used as a test argument)
"""The route the organization administration screens add: closure requested with a password check that is a 400, not a 401."""

from __future__ import annotations

from cloud_support import PASSWORD, build_org, sign_in  # noqa: F401


def test_closure_with_a_wrong_password_is_a_400_with_a_code_not_a_signed_out_401(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call(
        "POST", "/api/org/closure", {"password": "not my password at all"}
    )
    # a 401 would read as "your session ended" and send the person to the sign-in screen
    assert status == 400 and body["code"] == "password_incorrect"
    assert cloud.tenancy.org(org.org_id)["closure_requested_at"] is None


def test_closure_request_needs_the_owner_and_stops_the_subscription(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    admin = sign_in(cloud, org.admin)
    status, body = admin.call("POST", "/api/org/closure", {"password": PASSWORD})
    assert status == 403
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("POST", "/api/org/closure", {"password": PASSWORD})
    assert (
        status == 200
        and body["export"]["size"] > 0
        and body["deletion_at"] > cloud.clock.now()
    )
    assert cloud.billing.subscription(org.org_id)["status"] == "cancelled"
    status, _ = owner.call("DELETE", "/api/org/closure")
    assert status == 200
    assert cloud.tenancy.org(org.org_id)["closure_requested_at"] is None
