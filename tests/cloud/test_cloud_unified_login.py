"""One sign-in page for everyone (POST /api/auth/login): members, platform operators, and people who are both.

The page sends email and password; whatever they match opens a session: the member cookie, the operator (console)
cookie, or both. A wrong password and an unknown email answer the same. Operators still give their second factor
before the console answers. Signing out signs out of both.
"""

from __future__ import annotations

import pytest
from cloud_support import PASSWORD, Browser, build_org, signup

from monosage_cloud import crypto
from monosage_cloud.webio import OPERATOR_COOKIE, SESSION_COOKIE

OPS = "ops@aira.test"
WRONG = "not the password at all"


def _login(browser: Browser, email: str, password: str = PASSWORD, org: str = ""):
    return browser.call(
        "POST", "/api/auth/login", {"email": email, "password": password, "org": org}
    )


def _enrol_operator_mfa(app, browser: Browser) -> str:
    status, begun = browser.call("POST", "/api/platform/auth/mfa/begin", {})
    assert status == 200, begun
    status, done = browser.call(
        "POST",
        "/api/platform/auth/mfa/enable",
        {"code": crypto.totp_code(begun["secret"], app.clock.now())},
    )
    assert status == 200 and len(done["recovery_codes"]) == 10, done
    return begun["secret"]


def _failed_logins(app, table: str, email: str) -> int:
    with app.db.read() as connection:
        row = connection.execute(
            f"SELECT failed_logins FROM {table} WHERE email = ?", (email,)
        ).fetchone()
    return int(row[0])


# ----------------------------------------------------------------------------- one identity


def test_an_operator_signs_in_on_the_main_page_and_must_give_a_second_factor(cloud):
    cloud.identity.create_operator(OPS, PASSWORD, role="platform_admin")
    browser = Browser(cloud)
    status, data = _login(browser, OPS)
    assert status == 200, data
    assert data["signed_in"] == ["operator"] and data["next"] == "console"
    assert data["operator"] == {"role": "platform_admin", "mfa_enrolled": False}
    assert data["me"] is None and data["mfa_required"] is False
    assert OPERATOR_COOKIE in browser.cookies and SESSION_COOKIE not in browser.cookies
    # the same cookie and flags the console's own sign-in sets
    cookie = next(
        v
        for k, v in browser.last_headers
        if k.lower() == "set-cookie" and v.startswith(OPERATOR_COOKIE + "=")
    )
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert f"Max-Age={cloud.config.operator_session_ttl}" in cookie
    # nothing works before the second factor; enrolling works from this session
    status, body = browser.call("GET", "/api/platform/tenants")
    assert status == 401 and body["code"] == "mfa_required"
    assert browser.call("GET", "/api/platform/me")[1]["mfa"] == {
        "enabled": False,
        "verified": False,
    }
    secret = _enrol_operator_mfa(cloud, browser)
    assert browser.call("GET", "/api/platform/tenants")[0] == 200
    assert browser.call("GET", "/api/auth/me")[0] == 401, "no organization session"
    actions = [e["action"] for e in cloud.platform_audit.list()]
    assert "operator.login" in actions

    # the next sign-in asks for the code it enrolled
    again = Browser(cloud)
    status, data = _login(again, OPS)
    assert status == 200 and data["operator"]["mfa_enrolled"] is True
    assert again.call("GET", "/api/platform/tenants")[1]["code"] == "mfa_required"
    cloud.clock.advance(seconds=30)
    status, _ = again.call(
        "POST",
        "/api/platform/auth/mfa/verify",
        {"code": crypto.totp_code(secret, cloud.clock.now())},
    )
    assert status == 200
    assert again.call("GET", "/api/platform/tenants")[0] == 200


def test_a_member_signs_in_as_before_and_gets_no_console(cloud):
    result = signup(cloud)
    browser = Browser(cloud)
    status, data = _login(browser, "owner@acme.test")
    assert status == 200, data
    assert data["signed_in"] == ["member"] and data["next"] == "home"
    assert data["operator"] is None and data["me"]["role"] == "owner"
    assert SESSION_COOKIE in browser.cookies and OPERATOR_COOKIE not in browser.cookies
    me = browser.call("GET", "/api/auth/me")[1]
    assert me["org"]["org_id"] == result["org"]["org_id"] and "console" not in me
    assert browser.call("GET", "/api/platform/me")[0] == 401


def test_the_console_sign_in_endpoint_still_works_for_api_clients(cloud):
    signup(cloud)
    cloud.identity.create_operator(OPS, PASSWORD, role="support")
    browser = Browser(cloud)
    status, data = browser.call(
        "POST", "/api/platform/auth/login", {"email": OPS, "password": PASSWORD}
    )
    assert status == 200 and data["mfa_enrolled"] is False
    assert SESSION_COOKIE not in browser.cookies
    member = Browser(cloud)
    status, _ = member.call(
        "POST",
        "/api/platform/auth/login",
        {"email": "owner@acme.test", "password": PASSWORD},
    )
    assert status == 401, "the console endpoint signs in operators only"


# ----------------------------------------------------------------------------- both


def _both(cloud, plan="starter"):
    org = build_org(cloud, plan=plan, seats=10)
    cloud.identity.create_operator(org.owner.email, PASSWORD, role="platform_admin")
    return org


def test_someone_who_is_both_gets_both_sessions_and_each_side_knows_the_other(cloud):
    org = _both(cloud)
    browser = Browser(cloud)
    status, data = _login(browser, org.owner.email)
    assert status == 200, data
    assert data["signed_in"] == ["member", "operator"]
    assert data["next"] == "console", "an operator lands on the console"
    assert data["me"]["role"] == "owner"
    assert SESSION_COOKIE in browser.cookies and OPERATOR_COOKIE in browser.cookies
    # the apps side offers the console, the console offers the apps
    me = browser.call("GET", "/api/auth/me")[1]
    assert me["console"] == {"role": "platform_admin", "mfa_verified": False}
    _enrol_operator_mfa(cloud, browser)
    assert browser.call("GET", "/api/auth/me")[1]["console"]["mfa_verified"] is True
    platform_me = browser.call("GET", "/api/platform/me")[1]
    assert platform_me["member"]["role"] == "owner"
    assert platform_me["member"]["org"]["name"] == "Acme Outplacement"
    # both work at once, each with its own cookie
    assert browser.call("GET", "/api/platform/tenants")[0] == 200
    assert browser.call("GET", "/api/org")[0] == 200
    # sign out signs out everything
    status, _ = browser.call("POST", "/api/auth/logout", {})
    assert status == 200 and not browser.cookies
    assert browser.call("GET", "/api/auth/me")[0] == 401
    assert browser.call("GET", "/api/platform/me")[0] == 401


def test_signing_out_of_the_console_signs_out_of_the_apps_too(cloud):
    org = _both(cloud)
    browser = Browser(cloud)
    _login(browser, org.owner.email)
    member_token = browser.cookies[SESSION_COOKIE]
    status, _ = browser.call("POST", "/api/platform/auth/logout", {})
    assert status == 200 and not browser.cookies
    assert cloud.identity.principal_for_token(member_token) is None, (
        "revoked, not only cleared"
    )


def test_a_member_side_from_another_person_is_not_offered(cloud):
    org = _both(cloud)
    ops = Browser(cloud)
    _login(ops, org.owner.email)
    other = Browser(cloud)
    _login(other, org.candidate.email)
    # a browser holding someone else's member cookie next to this operator's cookie
    mixed = Browser(cloud)
    mixed.cookies = {
        SESSION_COOKIE: other.cookies[SESSION_COOKIE],
        OPERATOR_COOKIE: ops.cookies[OPERATOR_COOKIE],
    }
    assert "console" not in mixed.call("GET", "/api/auth/me")[1]
    assert mixed.call("GET", "/api/platform/me")[1]["member"] is None


def test_different_passwords_sign_in_to_one_side_without_locking_the_other(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    console_password = "a different console passphrase"
    cloud.identity.create_operator(
        org.owner.email, console_password, role="platform_admin"
    )
    for _ in range(cloud.config.max_failed_logins + 1):
        browser = Browser(cloud)
        status, data = _login(browser, org.owner.email)
        assert status == 200 and data["signed_in"] == ["member"], data
        assert OPERATOR_COOKIE not in browser.cookies
    assert _failed_logins(cloud, "operators", org.owner.email) == 0
    for _ in range(cloud.config.max_failed_logins + 1):
        browser = Browser(cloud)
        status, data = _login(browser, org.owner.email, console_password)
        assert status == 200 and data["signed_in"] == ["operator"], data
        assert SESSION_COOKIE not in browser.cookies
    assert _failed_logins(cloud, "members", org.owner.email) == 0


def test_a_sign_in_replaces_what_the_browser_held(cloud):
    org = _both(cloud)
    browser = Browser(cloud)
    _login(browser, org.owner.email)
    old_operator = browser.cookies[OPERATOR_COOKIE]
    status, data = _login(browser, org.candidate.email)
    assert status == 200 and data["signed_in"] == ["member"]
    assert OPERATOR_COOKIE not in browser.cookies, "the other person's console is gone"
    assert cloud.identity.principal_for_token(old_operator, kind="operator") is None, (
        "and its session revoked"
    )
    assert browser.call("GET", "/api/auth/me")[1]["email"] == org.candidate.email


def test_several_organizations_ask_which_one_before_any_session_opens(cloud):
    signup(cloud, "First Org", "dual@x.test")
    cloud.identity.signup_org("Second Org", "dual@x.test", PASSWORD)
    cloud.identity.create_operator("dual@x.test", PASSWORD, role="support")
    browser = Browser(cloud)
    status, data = _login(browser, "dual@x.test")
    assert status == 409 and data["code"] == "org_required"
    assert {o["slug"] for o in data["orgs"]} == {"first-org", "second-org"}
    assert not browser.cookies
    status, data = _login(browser, "dual@x.test", org="second-org")
    assert status == 200 and data["signed_in"] == ["member", "operator"]
    assert data["me"]["org"]["slug"] == "second-org"


def test_a_suspended_organization_still_lets_its_operator_into_the_console(cloud):
    org = _both(cloud)
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE organizations SET status = 'suspended' WHERE org_id = ?",
            (org.org_id,),
        )
    browser = Browser(cloud)
    status, data = _login(browser, org.owner.email)
    assert status == 200 and data["signed_in"] == ["operator"], data
    assert "suspended" in data["member_notice"]["error"]
    assert SESSION_COOKIE not in browser.cookies
    # a member who is not an operator is refused as before
    status, data = _login(Browser(cloud), org.admin.email)
    assert status == 403 and "suspended" in data["error"]


# ----------------------------------------------------------------------------- wrong credentials


def test_a_wrong_password_and_an_unknown_email_answer_the_same(cloud, monkeypatch):
    org = _both(cloud)
    cloud.identity.create_operator("only-ops@aira.test", PASSWORD, role="support")
    checks: list[int] = []
    real = crypto.verify_password

    def counted(password, digest):  # noqa: ANN001, ANN202
        checks.append(1)
        return real(password, digest)

    monkeypatch.setattr(crypto, "verify_password", counted)
    answers = {}
    for email in (
        "nobody@nowhere.test",
        org.candidate.email,
        "only-ops@aira.test",
        org.owner.email,
    ):
        checks.clear()
        status, data = _login(Browser(cloud), email, WRONG)
        answers[email] = (status, data["error"], data["code"], len(checks))
    assert {a[:3] for a in answers.values()} == {
        (401, "Email or password is incorrect.", "unauthorized")
    }, answers
    # always two password-hash checks (the operator account or a stand-in, and each membership or a stand-in)
    assert {a[3] for a in answers.values()} == {2}, answers


def test_operator_lockout_and_rate_limit_keep_working(cloud):
    cloud.identity.create_operator(OPS, PASSWORD, role="platform_admin")
    for _ in range(cloud.config.max_failed_logins):
        status, _ = _login(Browser(cloud), OPS, WRONG)
        assert status == 401
    status, data = _login(Browser(cloud), OPS)
    assert status == 423 and data["code"] == "locked", "the right password waits too"
    cloud.clock.advance(seconds=cloud.config.lockout_seconds + 1)
    assert _login(Browser(cloud), OPS)[0] == 200


def test_member_lockout_is_unchanged(cloud):
    signup(cloud)
    for _ in range(cloud.config.max_failed_logins):
        assert _login(Browser(cloud), "owner@acme.test", WRONG)[0] == 401
    status, data = _login(Browser(cloud), "owner@acme.test")
    assert status == 423 and "minutes" in data["error"]


def test_failed_sign_ins_count_towards_the_address_limit(cloud):
    Browser(
        cloud
    )  # the gateway (and its limiters) belongs to the app from the first request on
    cloud.gateway.auth_limiter.limit = 5
    try:
        for index in range(5):
            assert _login(Browser(cloud), f"nobody{index}@x.test", WRONG)[0] == 401
        status, data = _login(Browser(cloud), "nobody@x.test", WRONG)
        assert status == 429, data
    finally:
        cloud.gateway.auth_limiter.limit = 30
        cloud.gateway.auth_limiter.reset("auth:127.0.0.1")


@pytest.mark.parametrize("password", ["", " "])
def test_an_empty_password_opens_nothing(cloud, password):
    org = _both(cloud)
    status, _ = _login(Browser(cloud), org.owner.email, password)
    assert status == 401
