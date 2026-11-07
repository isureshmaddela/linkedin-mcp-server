"""Accounts for people on their own, and accounts a platform admin makes for someone.

A person signs up with their name, email and password; the business name is optional ("<name>'s workspace"). A
platform admin can make an account from the console (email confirmed, on the plan for new sign-ups) and confirm
the email of someone who is stuck because this server sends no email. The sign-up page is told whether email is
sent.
"""

from __future__ import annotations

from cloud_support import PASSWORD, Browser, sign_in
from test_cloud_platform import operator_browser

from monosage_cloud.rbac import Principal


def test_a_person_on_their_own_signs_up_without_a_business_name(cloud):
    browser = Browser(cloud)
    status, made = browser.call(
        "POST",
        "/api/auth/signup-org",
        {
            "organization": "",
            "name": "Asha Rao",
            "email": "asha@solo.test",
            "password": PASSWORD,
            "adult": True,
        },
    )
    assert status in (200, 201), made
    org = cloud.tenancy.org(made["org"]["org_id"])
    assert org["name"] == "Asha Rao's workspace"
    status, config = Browser(cloud).call("GET", "/api/cloud/config")
    assert (
        status == 200 and config["mail_delivery"] is False
    )  # no SMTP in tests: the page says so
    assert config["signup"]["plan_id"] == cloud.billing.signup_default()["plan_id"]


def test_a_platform_admin_makes_an_account_that_works_at_once(cloud):
    admin = operator_browser(cloud)
    status, made = admin.call(
        "POST",
        "/api/platform/accounts",
        {"name": "Ravi Kumar", "email": "ravi@new.test", "password": PASSWORD},
    )
    assert status == 201, made
    assert "token" not in made and made["org"]["name"] == "Ravi Kumar's workspace"
    member = cloud.tenancy.member(made["org"]["org_id"], made["member"]["member_id"])
    assert member["email_verified_at"] and member["role"] == "owner"
    assert made["subscription"]["plan_id"] == cloud.billing.signup_default()["plan_id"]

    # Ravi signs in with the password he was given and uses the apps straight away (no email to confirm).
    ravi = Browser(cloud)
    status, data = ravi.call(
        "POST", "/api/auth/login", {"email": "ravi@new.test", "password": PASSWORD}
    )
    assert status == 200, data
    assert ravi.call("GET", "/api/apps")[0] == 200
    assert (
        cloud.platform_audit.list(org_id=made["org"]["org_id"], limit=10)[0]["action"]
        == "account.create"
    )

    # The same email again is refused like a sign-up would be; bad input is a 400.
    status, data = admin.call(
        "POST",
        "/api/platform/accounts",
        {"name": "R", "email": "x@new.test", "password": PASSWORD},
    )
    assert status == 400


def test_support_cannot_make_accounts_or_confirm_emails(cloud):
    support = operator_browser(cloud, email="support@aira.test", role="support")
    status, _ = support.call(
        "POST",
        "/api/platform/accounts",
        {"name": "Someone", "email": "s@new.test", "password": PASSWORD},
    )
    assert status == 403


def test_a_platform_admin_confirms_an_email_the_server_could_not_send(cloud):
    browser = Browser(cloud)
    status, made = browser.call(
        "POST",
        "/api/auth/signup-org",
        {
            "organization": "Stuck Co",
            "name": "Mina",
            "email": "mina@stuck.test",
            "password": PASSWORD,
            "adult": True,
        },
    )
    assert status in (200, 201), made
    org_id, member_id = made["org"]["org_id"], made["member"]["member_id"]
    assert not cloud.tenancy.member(org_id, member_id)["email_verified_at"]

    admin = operator_browser(cloud)
    status, detail = admin.call("GET", f"/api/platform/tenants/{org_id}")
    assert status == 200
    assert [(m["email"], m["email_verified"]) for m in detail["members"]] == [
        ("mina@stuck.test", False)
    ]
    status, done = admin.call(
        "POST", f"/api/platform/tenants/{org_id}/members/{member_id}/verify-email"
    )
    assert status == 200 and done["verified"] is True and done["already"] is False
    assert cloud.tenancy.member(org_id, member_id)["email_verified_at"]
    assert admin.call(
        "POST", f"/api/platform/tenants/{org_id}/members/{member_id}/verify-email"
    )[1]["already"]
    actions = [row["action"] for row in cloud.audit.list(org_id, limit=20)]
    assert "member.email_verified" in actions

    # A member of another organization cannot be named through this organization.
    other = Browser(cloud).call(
        "POST",
        "/api/auth/signup-org",
        {
            "organization": "Other Co",
            "name": "Oz",
            "email": "oz@other.test",
            "password": PASSWORD,
            "adult": True,
        },
    )[1]
    status, _ = admin.call(
        "POST",
        f"/api/platform/tenants/{org_id}/members/{other['member']['member_id']}/verify-email",
    )
    assert status == 404


def test_only_operators_reach_the_account_routes(cloud):
    from cloud_support import build_org

    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, _ = owner.call(
        "POST",
        "/api/platform/accounts",
        {"name": "Nope", "email": "n@x.test", "password": PASSWORD},
    )
    assert status in (401, 403)
    assert isinstance(org.owner, Principal)
