"""Sign-up, verification, sign-in, sessions, MFA, invitations and password reset."""

from __future__ import annotations

import pytest
from cloud_support import (
    PASSWORD,
    add_member,
    audit_actions,
    pay,
    principal,
    signup,
    token_from_mail,
)  # noqa: F401

from aira_cloud import crypto
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    Gone,
    Locked,
    MfaRequired,
    NotFound,
    PlanRequired,
    Unauthorized,
    ValidationFailed,
)


def test_signup_creates_org_owner_and_trial(cloud):
    result = signup(cloud, verify=False)
    org_id = result["org"]["org_id"]
    sub = cloud.billing.subscription(org_id)
    assert sub["status"] == "trialing" and sub["plan_id"] == "trial"
    assert sub["trial_end"] - sub["period_start"] == 14 * 86400
    assert result["owner"].role == "owner"
    assert result["owner"].extra["email_verified"] is False
    assert "org.create" in audit_actions(cloud, org_id)
    assert cloud.mail.latest("owner@acme.test", "verify_email")


def test_email_verification_is_single_use(cloud):
    signup(cloud, verify=False)
    token = token_from_mail(cloud, "owner@acme.test", "verify_email")
    assert cloud.identity.verify_email(token)["verified"] is True
    with pytest.raises(Gone):
        cloud.identity.verify_email(token)
    with pytest.raises(NotFound):
        cloud.identity.verify_email("not-a-token")


def test_verification_link_expires(cloud):
    signup(cloud, verify=False)
    token = token_from_mail(cloud, "owner@acme.test", "verify_email")
    cloud.clock.advance(days=2)
    with pytest.raises(Gone):
        cloud.identity.verify_email(token)


def test_password_policy_and_duplicate_slugs(cloud):
    with pytest.raises(ValidationFailed):
        cloud.identity.signup_org("Short", "a@b.test", "short")
    with pytest.raises(ValidationFailed):
        cloud.identity.signup_org("X", "a@b.test", PASSWORD)
    with pytest.raises(ValidationFailed):
        cloud.identity.signup_org(
            "Name Ok", "alice@b.test", "alice-password-1"
        )  # contains the email name
    first = cloud.identity.signup_org("Same Name", "one@a.test", PASSWORD)
    second = cloud.identity.signup_org("Same Name", "two@a.test", PASSWORD)
    assert first["org"]["slug"] != second["org"]["slug"]


def test_login_lockout_and_unknown_email(cloud):
    signup(cloud)
    with pytest.raises(Unauthorized):
        cloud.identity.login("nobody@acme.test", PASSWORD)
    for _ in range(cloud.config.max_failed_logins):
        with pytest.raises(Unauthorized):
            cloud.identity.login("owner@acme.test", "wrong password here")
    with pytest.raises(Locked):
        cloud.identity.login("owner@acme.test", PASSWORD)
    cloud.clock.advance(seconds=cloud.config.lockout_seconds + 1)
    assert cloud.identity.login("owner@acme.test", PASSWORD)["token"]


def test_sessions_end_on_logout_expiry_and_role_change(cloud):
    result = signup(cloud)
    owner = result["owner"]
    coach = add_member(cloud, owner, "coach@acme.test", "coach")
    token = cloud.identity.login("coach@acme.test", PASSWORD)["token"]
    assert cloud.identity.principal_for_token(token)
    cloud.tenancy.change_role(
        owner, coach.member_id, "admin"
    )  # session rotation: every earlier session ends
    assert cloud.identity.principal_for_token(token) is None
    token = cloud.identity.login("coach@acme.test", PASSWORD)["token"]
    cloud.identity.logout(token)
    assert cloud.identity.principal_for_token(token) is None
    token = cloud.identity.login("coach@acme.test", PASSWORD)["token"]
    cloud.clock.advance(seconds=cloud.config.session_ttl + 1)
    assert cloud.identity.principal_for_token(token) is None


def test_device_sign_out_keeps_the_current_session(cloud):
    result = signup(cloud)
    first = cloud.identity.login("owner@acme.test", PASSWORD)["token"]
    second = cloud.identity.login("owner@acme.test", PASSWORD)["token"]
    me = cloud.identity.principal_for_token(second)
    assert cloud.identity.revoke_other_sessions(me) >= 2
    assert cloud.identity.principal_for_token(first) is None
    assert cloud.identity.principal_for_token(second) is not None
    assert result["owner"]


def test_mfa_enrol_verify_replay_and_recovery(cloud):
    signup(cloud)
    login = cloud.identity.login("owner@acme.test", PASSWORD)
    me = principal(cloud, login["token"])
    begin = cloud.identity.mfa_begin(me)
    secret = begin["secret"]
    with pytest.raises(ValidationFailed):
        cloud.identity.mfa_enable(me, "000000")
    code = crypto.totp_code(secret, cloud.clock.now())
    enabled = cloud.identity.mfa_enable(me, code)
    assert len(enabled["recovery_codes"]) == 10
    # the next sign-in needs the second factor
    login = cloud.identity.login("owner@acme.test", PASSWORD)
    assert login["mfa_required"] is True
    pending = principal(cloud, login["token"])
    assert pending.mfa_verified is False
    # the code already used cannot be replayed
    with pytest.raises(Unauthorized):
        cloud.identity.mfa_verify(pending, code)
    cloud.clock.advance(seconds=31)
    assert cloud.identity.mfa_verify(
        pending, crypto.totp_code(secret, cloud.clock.now())
    )["verified"]
    # a recovery code works once
    login = cloud.identity.login("owner@acme.test", PASSWORD)
    pending = principal(cloud, login["token"])
    recovery = enabled["recovery_codes"][0]
    assert cloud.identity.mfa_verify(pending, recovery)["recovery_code_used"] is True
    login = cloud.identity.login("owner@acme.test", PASSWORD)
    with pytest.raises(Unauthorized):
        cloud.identity.mfa_verify(principal(cloud, login["token"]), recovery)


def test_mfa_cannot_be_disabled_when_the_plan_requires_it(cloud):
    result = signup(cloud)
    org_id = result["org"]["org_id"]
    pay(cloud, org_id, "team", 25)
    me = principal(cloud, cloud.identity.login("owner@acme.test", PASSWORD)["token"])
    assert cloud.identity.mfa_status(me)["required"] is True
    secret = cloud.identity.mfa_begin(me)["secret"]
    cloud.identity.mfa_enable(me, crypto.totp_code(secret, cloud.clock.now()))
    cloud.clock.advance(seconds=31)
    with pytest.raises(Forbidden):
        cloud.identity.mfa_disable(
            me, PASSWORD, crypto.totp_code(secret, cloud.clock.now())
        )


def test_mfa_reset_by_admin_ends_sessions(cloud):
    result = signup(cloud)
    owner = result["owner"]
    coach = add_member(cloud, owner, "coach@acme.test", "coach")
    secret = cloud.identity.mfa_begin(coach)["secret"]
    cloud.identity.mfa_enable(coach, crypto.totp_code(secret, cloud.clock.now()))
    cloud.identity.mfa_reset(owner, coach.member_id)
    assert (
        cloud.identity.principal_for_token(
            cloud.identity.login("coach@acme.test", PASSWORD)["token"]
        ).mfa_verified
        is True
    )
    assert "auth.mfa_reset" in audit_actions(cloud, result["org"]["org_id"])


def test_invites_are_single_use_and_expire_in_seven_days(cloud):
    result = signup(cloud)
    owner = result["owner"]
    cloud.identity.invite(owner, "cand@acme.test", "candidate")
    token = token_from_mail(cloud, "cand@acme.test", "invite")
    assert cloud.identity.invite_info(token)["role"] == "candidate"
    accepted = cloud.identity.accept_invite(token, PASSWORD, name="Cam Candidate")
    assert accepted["role"] == "candidate"
    with pytest.raises(Gone):
        cloud.identity.accept_invite(token, PASSWORD)
    cloud.identity.invite(owner, "late@acme.test", "coach")
    late = token_from_mail(cloud, "late@acme.test", "invite")
    cloud.clock.advance(days=7, seconds=1)
    with pytest.raises(Gone):
        cloud.identity.accept_invite(late, PASSWORD)


def test_resend_and_revoke_invites(cloud):
    result = signup(cloud)
    owner = result["owner"]
    invite = cloud.identity.invite(owner, "cand@acme.test", "candidate")
    old = token_from_mail(cloud, "cand@acme.test", "invite")
    cloud.identity.resend_invite(owner, invite["invite_id"])
    new = token_from_mail(cloud, "cand@acme.test", "invite")
    assert new != old
    with pytest.raises(NotFound):
        cloud.identity.accept_invite(old, PASSWORD)
    assert len(cloud.identity.list_invites(owner)) == 1
    cloud.identity.revoke_invite(owner, invite["invite_id"])
    with pytest.raises(Gone):
        cloud.identity.accept_invite(new, PASSWORD)
    assert cloud.identity.list_invites(owner) == []


def test_admin_cannot_invite_an_owner_but_owner_can(cloud):
    result = signup(cloud)
    owner = result["owner"]
    pay(cloud, result["org"]["org_id"], "starter", 5)
    admin = add_member(cloud, owner, "admin@acme.test", "admin")
    with pytest.raises(Forbidden):
        cloud.identity.invite(admin, "second@acme.test", "owner")
    cloud.identity.invite(owner, "second@acme.test", "owner")


def test_invitation_respects_seat_limits_and_pending_invites_count(cloud):
    result = signup(cloud)
    owner = result["owner"]
    for index in range(3):
        cloud.identity.invite(
            owner, f"c{index}@acme.test", "candidate"
        )  # trial: 3 candidate seats
    with pytest.raises(PlanRequired) as raised:
        cloud.identity.invite(owner, "c3@acme.test", "candidate")
    assert raised.value.extra["limit"] == "candidate_seats"
    assert raised.value.extra["required_plan"] == "starter"
    cloud.identity.invite(
        owner, "coach@acme.test", "coach"
    )  # staff seats: owner + one more on trial
    with pytest.raises(PlanRequired):
        cloud.identity.invite(owner, "coach2@acme.test", "coach")


def test_password_reset_ends_sessions_and_is_single_use(cloud):
    result = signup(cloud)
    token_before = cloud.identity.login("owner@acme.test", PASSWORD)["token"]
    cloud.identity.request_password_reset("owner@acme.test")
    cloud.identity.request_password_reset("nobody@acme.test")  # same answer, no mail
    assert cloud.mail.latest("nobody@acme.test", "password_reset") is None
    reset = token_from_mail(cloud, "owner@acme.test", "password_reset")
    cloud.identity.reset_password(reset, "a brand new passphrase")
    assert cloud.identity.principal_for_token(token_before) is None
    with pytest.raises(Gone):
        cloud.identity.reset_password(reset, "another passphrase here")
    assert cloud.identity.login("owner@acme.test", "a brand new passphrase")["token"]
    assert result


def test_same_email_in_two_organizations_asks_which_one(cloud):
    one = signup(cloud, "First Org", "dual@x.test")
    cloud.identity.signup_org("Second Org", "dual@x.test", PASSWORD)
    with pytest.raises(Conflict) as raised:
        cloud.identity.login("dual@x.test", PASSWORD)
    assert raised.value.code == "org_required"
    assert {o["slug"] for o in raised.value.extra["orgs"]} == {
        "first-org",
        "second-org",
    }
    login = cloud.identity.login("dual@x.test", PASSWORD, org="second-org")
    assert login["org_id"] != one["org"]["org_id"]


def test_removed_member_cannot_sign_in_and_email_can_be_reinvited(cloud):
    result = signup(cloud)
    owner = result["owner"]
    coach = add_member(cloud, owner, "coach@acme.test", "coach")
    cloud.tenancy.remove_member(owner, coach.member_id)
    with pytest.raises(Unauthorized):
        cloud.identity.login("coach@acme.test", PASSWORD)
    again = add_member(cloud, owner, "coach@acme.test", "coach")
    assert again.member_id != coach.member_id


def test_a_password_alone_cannot_replace_an_enrolled_second_factor(cloud):
    signup(cloud)
    me = principal(cloud, cloud.identity.login("owner@acme.test", PASSWORD)["token"])
    secret = cloud.identity.mfa_begin(me)["secret"]
    cloud.identity.mfa_enable(me, crypto.totp_code(secret, cloud.clock.now()))

    # an attacker with the stolen password has a session that has not given the factor
    attacker = principal(
        cloud, cloud.identity.login("owner@acme.test", PASSWORD)["token"]
    )
    assert attacker.mfa_verified is False
    with pytest.raises(MfaRequired):
        cloud.identity.mfa_begin(attacker)
    with pytest.raises(MfaRequired):
        cloud.identity.mfa_enable(attacker, "123456")

    # after giving the real factor the person may re-enrol
    cloud.clock.advance(seconds=31)
    assert cloud.identity.mfa_verify(
        attacker, crypto.totp_code(secret, cloud.clock.now())
    )["verified"]
