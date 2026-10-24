"""OIDC, SAML and SCIM for hosted organizations, through the gateway where practical."""
# ruff: noqa: F811, F401  (pytest fixtures are imported by name, like the other cloud tests)

from __future__ import annotations

from urllib.parse import quote, unquote

import pytest
from cloud_support import (
    PASSWORD,
    Browser,
    audit_actions,
    build_org,
    cloud,
    sign_in,
)
from idp_stub import OidcProvider, SamlIdp

from aira_cloud.errors import Forbidden, PlanRequired, ValidationFailed


@pytest.fixture(autouse=True)
def allow_loopback_idp(monkeypatch):
    monkeypatch.setenv("AIRA_CLOUD_ALLOW_LOCAL_IDP", "1")


@pytest.fixture
def oidc(cloud):
    provider = OidcProvider(cloud.clock.now)
    yield provider
    provider.close()


@pytest.fixture
def idp(cloud):
    return SamlIdp(cloud.clock.now)


def fresh(app) -> Browser:
    """A new person at a new browser (and the sign-in rate limit starts over)."""
    browser = Browser(app)
    app.gateway.auth_limiter.reset("sso:127.0.0.1")
    return browser


def team(cloud) -> object:
    return build_org(cloud, plan="team", seats=25)


def configure_oidc(cloud, org, oidc, **extra):
    options = {
        "issuer": oidc.issuer,
        "client_id": oidc.client_id,
        "client_secret": oidc.client_secret,
        "domains": ["acme.test"],
        **extra,
    }
    return cloud.sso.configure_oidc(org.owner, **options)


def oidc_login(
    cloud,
    org,
    oidc,
    *,
    email: str,
    sub: str = "user-1",
    name: str = "",
    groups=None,
    tamper=None,
    slug: str = "acme-outplacement",
):
    """Walk the whole redirect dance in a fresh browser; return (browser, start status, callback status, location)."""
    oidc.tamper = tamper or {}
    browser = fresh(cloud)
    status, _ = browser.call("GET", f"/api/sso/{slug}/start")
    assert status == 302, browser.header("Location")
    code, state = oidc.authorize(
        browser.header("Location"), email=email, sub=sub, name=name, groups=groups
    )
    status, _ = browser.call("GET", f"/api/sso/oidc/callback?code={code}&state={state}")
    return browser, status, browser.header("Location")


def signed_in_email(browser: Browser) -> str | None:
    status, me = browser.call("GET", "/api/auth/me")
    return me["email"] if status == 200 else None


# ----------------------------------------------------------------------------- configuration


def test_configuring_sso_needs_the_team_plan_and_the_permission(cloud, oidc):
    starter = build_org(
        cloud, "Starter Org", plan="starter", seats=10, slug_email="s.test"
    )
    with pytest.raises(PlanRequired) as raised:
        cloud.sso.configure_oidc(
            starter.owner, issuer=oidc.issuer, client_id="x", client_secret="y"
        )
    assert raised.value.extra["required_plan"] == "team"
    org = team(cloud)
    for role in (org.coach, org.candidate, org.billing, org.auditor):
        with pytest.raises(Forbidden):
            cloud.sso.configure_oidc(
                role, issuer=oidc.issuer, client_id="x", client_secret="y"
            )
    configured = configure_oidc(cloud, org, oidc)
    assert (
        configured["configured"]
        and configured["protocol"] == "oidc"
        and configured["settings"]["client_secret_set"] is True
    )
    assert oidc.client_secret not in repr(configured)
    admin_config = cloud.sso.configure_oidc(
        org.admin, issuer=oidc.issuer, client_id=oidc.client_id, domains=["acme.test"]
    )
    assert admin_config["settings"]["client_secret_set"], (
        "the stored secret is kept when it is not sent again"
    )


def test_configuration_is_validated(cloud, oidc, idp, monkeypatch):
    org = team(cloud)
    oidc.tamper = {"discovery_issuer": "https://someone-else.test"}
    with pytest.raises(ValidationFailed):
        configure_oidc(cloud, org, oidc)
    oidc.tamper = {}
    with pytest.raises(ValidationFailed):
        configure_oidc(
            cloud, org, oidc, group_role_map={"Owners": "owner"}
        )  # a group never grants ownership
    with pytest.raises(ValidationFailed):
        configure_oidc(cloud, org, oidc, default_role="owner")
    with pytest.raises(ValidationFailed):
        configure_oidc(cloud, org, oidc, domains=["not a domain"])
    with pytest.raises(ValidationFailed):
        cloud.sso.configure_saml(
            org.owner,
            idp_entity_id=idp.entity_id,
            sso_url=idp.sso_url,
            idp_certificate_pem="not a certificate",
        )
    monkeypatch.delenv("AIRA_CLOUD_ALLOW_LOCAL_IDP")
    with pytest.raises(
        ValidationFailed
    ):  # loopback and private addresses are refused unless a developer opts in
        configure_oidc(cloud, org, oidc)
    with pytest.raises(ValidationFailed):
        cloud.sso.configure_saml(
            org.owner,
            idp_entity_id=idp.entity_id,
            sso_url="http://169.254.169.254/latest",
            idp_certificate_pem=idp.cert_pem,
        )


def test_sso_settings_are_managed_through_the_api(cloud, oidc):
    org = team(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call(
        "POST",
        "/api/org/sso",
        {
            "protocol": "oidc",
            "issuer": oidc.issuer,
            "client_id": oidc.client_id,
            "client_secret": oidc.client_secret,
            "domains": ["acme.test"],
            "enforced": False,
        },
    )
    assert status == 200 and body["configured"], body
    status, body = owner.call("GET", "/api/org/sso")
    assert (
        body["available"]
        and body["sp"]["oidc_redirect_uri"].endswith("/api/sso/oidc/callback")
        and oidc.client_secret not in str(body)
    )
    assert sign_in(cloud, org.coach).call("GET", "/api/org/sso")[0] == 403
    status, body = owner.call("POST", "/api/org/sso", {"protocol": "ldap"})
    assert status == 400
    assert owner.call("DELETE", "/api/org/sso")[0] == 200
    assert owner.call("GET", "/api/org/sso")[1]["configured"] is False
    assert "sso.configure" in audit_actions(cloud, org.org_id, "sso.")


def test_discover_reveals_only_whether_sso_applies(cloud, oidc):
    org = team(cloud)
    assert cloud.sso.discover("someone@acme.test") == {"sso": False}
    configure_oidc(cloud, org, oidc)
    found = cloud.sso.discover("someone@ACME.test")
    assert found == {"sso": True, "org_slug": "acme-outplacement", "protocol": "oidc"}
    assert cloud.sso.discover("someone@other.test") == {"sso": False}
    assert cloud.sso.discover("not an email") == {"sso": False}
    status, body = fresh(cloud).call(
        "POST", "/api/sso/discover", {"email": "x@acme.test"}
    )
    assert status == 200 and set(body) == {"sso", "org_slug", "protocol"}


# ----------------------------------------------------------------------------- OIDC


def test_full_oidc_login_links_an_existing_member_and_skips_mfa(cloud, oidc):
    org = team(cloud)
    configure_oidc(cloud, org, oidc)
    browser, status, location = oidc_login(
        cloud, org, oidc, email="admin@acme.test", sub="idp-admin", name="Ada Admin"
    )
    assert status == 302 and location == "/#/"
    request = oidc.token_requests[-1]
    assert (
        request["client_secret"] == oidc.client_secret and request["code_verifier"]
    ), "the PKCE verifier went to the token endpoint"
    status, me = browser.call("GET", "/api/auth/me")
    assert (
        status == 200
        and me["email"] == "admin@acme.test"
        and me["auth_method"] == "sso"
    )
    assert me["mfa"]["required"] is False, "the identity provider did the second factor"
    assert browser.call("GET", "/api/org/dashboard")[0] == 200, (
        "an admin on Team needs no local MFA after SSO"
    )
    again, _, _ = oidc_login(cloud, org, oidc, email="admin@acme.test", sub="idp-admin")
    assert signed_in_email(again) == "admin@acme.test"
    actions = audit_actions(cloud, org.org_id)
    assert "member.sso_link" in actions and "auth.sso_login" in actions
    with cloud.db.read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sso_identities WHERE org_id = ?", (org.org_id,)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "tamper",
    [
        {"nonce": "someone-elses-nonce"},
        {"aud": "another-client"},
        {"exp_offset": -3600},
        {"sign_with_other_key": True},
    ],
    ids=["wrong-nonce", "wrong-audience", "expired", "bad-signature"],
)
def test_oidc_rejects_a_bad_id_token(cloud, oidc, tamper):
    org = team(cloud)
    configure_oidc(cloud, org, oidc)
    browser, status, location = oidc_login(
        cloud, org, oidc, email="admin@acme.test", tamper=tamper
    )
    assert status == 302 and location.startswith("/#/signin?sso_error=")
    assert signed_in_email(browser) is None


def test_oidc_state_is_single_use_and_unverified_email_is_refused(cloud, oidc):
    org = team(cloud)
    configure_oidc(cloud, org, oidc)
    browser = fresh(cloud)
    browser.call("GET", "/api/sso/acme-outplacement/start")
    code, state = oidc.authorize(browser.header("Location"), email="admin@acme.test")
    assert (
        browser.call("GET", f"/api/sso/oidc/callback?code={code}&state={state}")[0]
        == 302
    )
    assert signed_in_email(browser) == "admin@acme.test"
    replay = fresh(cloud)
    replay.call("GET", f"/api/sso/oidc/callback?code={code}&state={state}")
    assert replay.header("Location").startswith("/#/signin?sso_error="), (
        "a used state cannot be replayed"
    )
    browser = fresh(cloud)
    browser.call("GET", "/api/sso/acme-outplacement/start")
    code, state = oidc.authorize(
        browser.header("Location"), email="admin@acme.test", email_verified=False
    )
    browser.call("GET", f"/api/sso/oidc/callback?code={code}&state={state}")
    assert signed_in_email(browser) is None
    stale = fresh(cloud)
    stale.call("GET", "/api/sso/acme-outplacement/start")
    code, state = oidc.authorize(stale.header("Location"), email="admin@acme.test")
    cloud.clock.advance(seconds=700)
    stale.call("GET", f"/api/sso/oidc/callback?code={code}&state={state}")
    assert stale.header("Location").startswith("/#/signin?sso_error="), (
        "a sign-in that took over ten minutes is stale"
    )


# ----------------------------------------------------------------------------- SAML


def configure_saml(cloud, org, idp, **extra):
    options = {
        "idp_entity_id": idp.entity_id,
        "sso_url": idp.sso_url,
        "idp_certificate_pem": idp.cert_pem,
        "domains": ["acme.test"],
        **extra,
    }
    return cloud.sso.configure_saml(org.owner, **options)


def saml_start(cloud, idp, slug="acme-outplacement"):
    browser = fresh(cloud)
    status, _ = browser.call("GET", f"/api/sso/{slug}/start")
    assert status == 302
    location = browser.header("Location")
    assert location.startswith(idp.sso_url)
    request_id, relay = idp.parse_request(location)
    return browser, request_id, relay


def post_acs(browser: Browser, response: str, relay: str, slug="acme-outplacement"):
    body = f"SAMLResponse={quote(response, safe='')}&RelayState={quote(relay, safe='')}".encode()
    status, _ = browser.call(
        "POST",
        f"/api/sso/{slug}/saml/acs",
        body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return status, browser.header("Location")


def saml_urls(cloud, org):
    details = cloud.sso.sp_details(org.org_id)
    return details["saml_acs_url"], details["saml_entity_id"]


def test_sp_metadata_and_authn_request(cloud, idp):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    status, xml = fresh(cloud).call(
        "GET", "/api/sso/acme-outplacement/saml/metadata", raw=True
    )
    acs, entity = saml_urls(cloud, org)
    assert status == 200 and entity.encode() in xml and acs.encode() in xml
    _, request_id, relay = saml_start(cloud, idp)
    assert request_id.startswith("_") and relay


def test_full_saml_login_with_a_signed_assertion(cloud, idp):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    acs, entity = saml_urls(cloud, org)
    browser, request_id, relay = saml_start(cloud, idp)
    response = idp.response(
        request_id=request_id,
        acs=acs,
        audience=entity,
        email="cand@acme.test",
        name="Cam Candidate",
    )
    status, location = post_acs(browser, response, relay)
    assert status == 302 and location == "/#/"
    status, me = browser.call("GET", "/api/auth/me")
    assert (
        me["email"] == "cand@acme.test"
        and me["auth_method"] == "sso"
        and me["role"] == "candidate"
    )
    assert "member.sso_link" in audit_actions(cloud, org.org_id)


def test_saml_login_with_a_signed_response(cloud, idp):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    acs, entity = saml_urls(cloud, org)
    browser, request_id, relay = saml_start(cloud, idp)
    status, location = post_acs(
        browser,
        idp.response(
            request_id=request_id,
            acs=acs,
            audience=entity,
            email="coach@acme.test",
            sign="response",
        ),
        relay,
    )
    assert location == "/#/" and signed_in_email(browser) == "coach@acme.test"


@pytest.mark.parametrize(
    "options",
    [
        {"sign": "none"},
        {"key": "other"},
        {"audience": "https://another-service.test/entity"},
        {"recipient": "https://elsewhere.test/acs"},
        {"destination": "https://elsewhere.test/acs"},
        {"not_on_or_after": -600},
        {"not_before": 3600, "not_on_or_after": 4000},
        {"issuer": "https://other-idp.test/entity"},
        {"response_in_response_to": "_someone_elses_request"},
        {"in_response": "_not_the_request_we_sent"},
        {"status": "urn:oasis:names:tc:SAML:2.0:status:Responder"},
    ],
    ids=[
        "unsigned",
        "other-key",
        "wrong-audience",
        "wrong-recipient",
        "wrong-destination",
        "expired",
        "not-yet-valid",
        "wrong-issuer",
        "wrong-response-request",
        "wrong-assertion-request",
        "failed-status",
    ],
)
def test_saml_rejects_a_bad_response(cloud, idp, options):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    acs, entity = saml_urls(cloud, org)
    browser, request_id, relay = saml_start(cloud, idp)
    options = dict(options)
    in_response = options.pop("in_response", None)
    options.setdefault("audience", entity)
    response = idp.response(
        request_id=in_response or request_id,
        acs=acs,
        email="admin@acme.test",
        **options,
    )
    status, location = post_acs(browser, response, relay)
    assert status == 302 and location.startswith("/#/signin?sso_error="), location
    assert signed_in_email(browser) is None


def test_saml_signature_wrapping_never_signs_in_the_forged_identity(cloud, idp):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    acs, entity = saml_urls(cloud, org)
    for graft in (False, True):
        browser, request_id, relay = saml_start(cloud, idp)
        response = idp.response(
            request_id=request_id,
            acs=acs,
            audience=entity,
            email="cand@acme.test",
            evil={"email": "admin@acme.test"},
            graft_signature=graft,
        )
        post_acs(browser, response, relay)
        assert signed_in_email(browser) in {None, "cand@acme.test"}, (
            "the unsigned assertion must never be believed"
        )
        assert signed_in_email(browser) != "admin@acme.test"


def test_saml_response_cannot_be_replayed_and_relay_state_is_one_time(cloud, idp):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    acs, entity = saml_urls(cloud, org)
    browser, request_id, relay = saml_start(cloud, idp)
    response = idp.response(
        request_id=request_id, acs=acs, audience=entity, email="cand@acme.test"
    )
    assert post_acs(browser, response, relay)[1] == "/#/"
    replay = fresh(cloud)
    assert post_acs(replay, response, relay)[1].startswith("/#/signin?sso_error=")
    # a fresh sign-in that tries to reuse an assertion id the service already accepted is refused too
    other, request_two, relay_two = saml_start(cloud, idp)
    same_id = idp.response(
        request_id=request_two,
        acs=acs,
        audience=entity,
        email="cand@acme.test",
        assertion_id="_fixed-assertion-id",
    )
    assert post_acs(other, same_id, relay_two)[1] == "/#/"
    again, request_three, relay_three = saml_start(cloud, idp)
    reused = idp.response(
        request_id=request_three,
        acs=acs,
        audience=entity,
        email="cand@acme.test",
        assertion_id="_fixed-assertion-id",
    )
    location = post_acs(again, reused, relay_three)[1]
    assert location.startswith("/#/signin?sso_error=") and "already%20used" in location


def test_saml_with_doctype_is_refused(cloud, idp):
    org = team(cloud)
    configure_saml(cloud, org, idp)
    browser, _request_id, relay = saml_start(cloud, idp)
    import base64

    xml = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    location = post_acs(browser, base64.b64encode(xml).decode(), relay)[1]
    assert location.startswith("/#/signin?sso_error=")


# ----------------------------------------------------------------------------- provisioning and roles


def test_just_in_time_provisioning_follows_domain_default_role_and_seats(cloud, oidc):
    org = team(cloud)
    configure_oidc(cloud, org, oidc, default_role="candidate")
    browser, _, location = oidc_login(
        cloud, org, oidc, email="new.person@acme.test", sub="new-1", name="New Person"
    )
    assert location == "/#/"
    status, me = browser.call("GET", "/api/auth/me")
    assert me["role"] == "candidate" and me["verified"] is True
    workspace = cloud.workspaces.data_path(org.org_id, me["member_id"])
    assert workspace.exists(), "a candidate gets a workspace"
    assert {"member.sso_provision", "auth.sso_login"} <= set(
        audit_actions(cloud, org.org_id)
    )
    outsider, _, location = oidc_login(
        cloud, org, oidc, email="intruder@evil.test", sub="evil-1"
    )
    assert (
        location.startswith("/#/signin?sso_error=") and "domain_not_allowed" in location
    )
    assert signed_in_email(outsider) is None
    # seat limits apply: with all candidate seats used the next new person is refused, not provisioned
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET candidate_seats = 3 WHERE org_id = ?",
            (org.org_id,),
        )
    cloud.entitlements.invalidate(org.org_id)
    _, _, location = oidc_login(
        cloud, org, oidc, email="third@acme.test", sub="third-1"
    )
    assert location.startswith("/#/signin?sso_error=") and "candidate" in unquote(
        location
    )
    with cloud.db.read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM members WHERE org_id = ? AND email = 'third@acme.test'",
                (org.org_id,),
            ).fetchone()[0]
            == 0
        )


def test_groups_map_to_roles_and_never_touch_owners_or_candidates(cloud, oidc):
    org = team(cloud)
    configure_oidc(
        cloud,
        org,
        oidc,
        group_role_map={"Coaches": "coach", "Admins": "admin"},
        default_role="candidate",
    )
    browser, _, _ = oidc_login(
        cloud, org, oidc, email="fresh.coach@acme.test", sub="fc-1", groups=["Coaches"]
    )
    assert browser.call("GET", "/api/auth/me")[1]["role"] == "coach"
    browser, _, _ = oidc_login(
        cloud,
        org,
        oidc,
        email="fresh.coach@acme.test",
        sub="fc-1",
        groups=["Coaches", "Admins"],
    )
    assert browser.call("GET", "/api/auth/me")[1]["role"] == "admin", (
        "the most privileged mapped group wins"
    )
    with cloud.db.read() as connection:
        assert (
            connection.execute(
                "SELECT session_epoch FROM members WHERE email = 'fresh.coach@acme.test'"
            ).fetchone()[0]
            >= 1
        )
    owner, _, _ = oidc_login(
        cloud, org, oidc, email="owner@acme.test", sub="o-1", groups=["Coaches"]
    )
    assert owner.call("GET", "/api/auth/me")[1]["role"] == "owner", (
        "a group never demotes an owner"
    )
    candidate, _, _ = oidc_login(
        cloud, org, oidc, email="cand@acme.test", sub="c-1", groups=["Admins"]
    )
    assert candidate.call("GET", "/api/auth/me")[1]["role"] == "candidate", (
        "a candidate does not become staff through a group"
    )


def test_removed_members_cannot_sign_in_with_sso(cloud, oidc):
    org = team(cloud)
    configure_oidc(cloud, org, oidc)
    cloud.tenancy.remove_member(org.owner, org.coach.member_id)
    _, _, location = oidc_login(cloud, org, oidc, email="coach@acme.test", sub="gone-1")
    assert location == "/#/", (
        "a removed member's email is free: the person is provisioned as a new member in an allowed domain"
    )
    with cloud.db.read() as connection:
        newer = connection.execute(
            "SELECT member_id, role FROM members WHERE email = 'coach@acme.test' AND removed_at IS NULL"
        ).fetchone()
    assert newer["member_id"] != org.coach.member_id and newer["role"] == "candidate"
    cloud.tenancy.remove_member(org.owner, newer["member_id"])
    again, _, _ = oidc_login(cloud, org, oidc, email="coach@acme.test", sub="gone-1")
    assert signed_in_email(again) is None or signed_in_email(again) == "coach@acme.test"
    with cloud.db.read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sso_identities WHERE org_id = ? AND member_id = ?",
                (org.org_id, newer["member_id"]),
            ).fetchone()[0]
            == 0
        )


def test_enforced_sso_blocks_password_sign_in_except_for_owners(cloud, oidc):
    org = team(cloud)
    configure_oidc(cloud, org, oidc, enforced=True)
    with pytest.raises(Forbidden) as raised:
        cloud.identity.login("coach@acme.test", PASSWORD)
    assert raised.value.code == "sso_required"
    assert cloud.identity.login("owner@acme.test", PASSWORD)["token"], (
        "owners keep password and MFA as the break-glass sign-in"
    )
    status, body = fresh(cloud).call(
        "POST", "/api/auth/login", {"email": "cand@acme.test", "password": PASSWORD}
    )
    assert status == 403 and body["code"] == "sso_required"
    cloud.sso.remove_config(org.owner)
    assert cloud.identity.login("coach@acme.test", PASSWORD)["token"]


def test_a_downgrade_does_not_lock_people_out_of_password_sign_in(cloud, oidc):
    org = team(cloud)
    configure_oidc(cloud, org, oidc, enforced=True)
    assert cloud.sso.enforced_for(org.org_id)
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET plan_id = 'starter', plan_version = 1 WHERE org_id = ?",
            (org.org_id,),
        )
    cloud.entitlements.invalidate(org.org_id)
    assert not cloud.sso.enforced_for(org.org_id)
    assert cloud.identity.login("coach@acme.test", PASSWORD)["token"]
    assert cloud.sso.discover("x@acme.test") == {"sso": False}


# ----------------------------------------------------------------------------- SCIM


def enterprise(cloud, org):
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    op = cloud.identity.principal_for_token(
        cloud.identity.operator_login("ops@aira.test", PASSWORD)["token"],
        kind="operator",
    )
    cloud.billing.activate_enterprise(op, org.org_id, seats=200)


def scim(cloud, token, method, path, body=None):
    browser = fresh(cloud)
    status, data = browser.call(
        method,
        path,
        body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/scim+json",
        },
    )
    return status, data


def test_scim_tokens_need_enterprise(cloud):
    org = team(cloud)
    with pytest.raises(PlanRequired) as raised:
        cloud.sso.create_scim_token(org.owner, "okta")
    assert (
        raised.value.extra["required_plan"] is None
        or raised.value.extra["feature"] == "auth.scim"
    )
    enterprise(cloud, org)
    created = cloud.sso.create_scim_token(org.owner, "okta")
    assert (
        created["token"].startswith("scim_")
        and len(cloud.sso.list_scim_tokens(org.owner)) == 1
    )
    assert created["token"] not in repr(cloud.sso.list_scim_tokens(org.owner)), (
        "the token is shown once"
    )
    with pytest.raises(Forbidden):
        cloud.sso.create_scim_token(org.coach)


def test_scim_authentication_and_revocation(cloud):
    org = team(cloud)
    enterprise(cloud, org)
    token = cloud.sso.create_scim_token(org.owner)
    assert (
        scim(cloud, token["token"], "GET", "/scim/v2/ServiceProviderConfig")[0] == 200
    )
    assert scim(cloud, "scim_nope", "GET", "/scim/v2/Users")[0] == 401
    assert scim(cloud, "", "GET", "/scim/v2/Users")[0] == 401
    browser = fresh(cloud)
    status, data = browser.call("GET", "/scim/v2/Users")
    assert status == 401 and data["schemas"] == [
        "urn:ietf:params:scim:api:messages:2.0:Error"
    ]
    cloud.sso.revoke_scim_token(org.owner, token["id"])
    assert scim(cloud, token["token"], "GET", "/scim/v2/Users")[0] == 401


def test_scim_users_create_list_filter_patch_and_delete(cloud):
    org = team(cloud)
    enterprise(cloud, org)
    cloud.sso.configure_saml(
        org.owner,
        idp_entity_id="https://idp.test/e",
        sso_url="http://127.0.0.1:9/sso",
        idp_certificate_pem=SamlIdp(cloud.clock.now).cert_pem,
        domains=["acme.test"],
        default_role="coach",
    )
    token = cloud.sso.create_scim_token(org.owner)["token"]
    status, user = scim(
        cloud,
        token,
        "POST",
        "/scim/v2/Users",
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "Pat.Coach@acme.test",
            "name": {"givenName": "Pat", "familyName": "Coach"},
            "active": True,
        },
    )
    assert (
        status == 201
        and user["userName"] == "pat.coach@acme.test"
        and user["active"] is True
        and user["roles"][0]["value"] == "coach"
    )
    assert (
        scim(
            cloud, token, "POST", "/scim/v2/Users", {"userName": "pat.coach@acme.test"}
        )[0]
        == 409
    )
    status, listing = scim(
        cloud,
        token,
        "GET",
        "/scim/v2/Users?filter=" + quote('userName eq "pat.coach@acme.test"'),
    )
    assert (
        status == 200
        and listing["totalResults"] == 1
        and listing["Resources"][0]["id"] == user["id"]
    )
    status, listing = scim(cloud, token, "GET", "/scim/v2/Users?startIndex=1&count=2")
    assert listing["itemsPerPage"] == 2 and listing["totalResults"] >= 8
    assert (
        scim(cloud, token, "GET", "/scim/v2/Users?filter=" + quote("userName co x"))[0]
        == 400
    )
    status, patched = scim(
        cloud,
        token,
        "PATCH",
        f"/scim/v2/Users/{user['id']}",
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "name.givenName", "value": "Patricia"}
            ],
        },
    )
    assert status == 200 and patched["name"]["givenName"] == "Patricia"
    status, replaced = scim(
        cloud,
        token,
        "PUT",
        f"/scim/v2/Users/{user['id']}",
        {"userName": "pat.coach@acme.test", "displayName": "Pat C", "active": True},
    )
    assert status == 200 and replaced["displayName"] == "Pat C"
    status, off = scim(
        cloud,
        token,
        "PATCH",
        f"/scim/v2/Users/{user['id']}",
        {"Operations": [{"op": "replace", "value": {"active": False}}]},
    )
    assert status == 200 and off["active"] is False
    assert scim(cloud, token, "GET", f"/scim/v2/Users/{user['id']}")[0] == 404
    status, second = scim(
        cloud, token, "POST", "/scim/v2/Users", {"userName": "quinn@acme.test"}
    )
    assert status == 201
    assert scim(cloud, token, "DELETE", f"/scim/v2/Users/{second['id']}")[0] == 204
    assert scim(cloud, token, "DELETE", f"/scim/v2/Users/{second['id']}")[0] == 404
    actions = audit_actions(cloud, org.org_id)
    assert {"member.scim_provision", "member.scim_deprovision", "member.remove"} <= set(
        actions
    )


def test_scim_deprovisioning_ends_sessions_respects_candidate_policy_and_the_last_owner(
    cloud,
):
    org = team(cloud)
    enterprise(cloud, org)
    token = cloud.sso.create_scim_token(org.owner)["token"]
    coach_session = cloud.identity.login("coach@acme.test", PASSWORD)["token"]
    assert cloud.identity.principal_for_token(coach_session)
    assert (
        scim(
            cloud,
            token,
            "PATCH",
            f"/scim/v2/Users/{org.coach.member_id}",
            {"Operations": [{"op": "replace", "path": "active", "value": "False"}]},
        )[0]
        == 200
    )
    assert cloud.identity.principal_for_token(coach_session) is None
    status, data = scim(
        cloud, token, "DELETE", f"/scim/v2/Users/{org.candidate.member_id}"
    )
    assert status == 204
    assert cloud.mail.latest("cand@acme.test", "data_export_ready"), (
        "the candidate-data policy still applies: the candidate gets their export"
    )
    status, data = scim(cloud, token, "DELETE", f"/scim/v2/Users/{org.owner.member_id}")
    assert status == 409 and data["status"] == "409", "the last owner cannot be removed"


def test_scim_groups_change_roles(cloud):
    org = team(cloud)
    enterprise(cloud, org)
    cloud.sso.configure_saml(
        org.owner,
        idp_entity_id="https://idp.test/e",
        sso_url="http://127.0.0.1:9/sso",
        idp_certificate_pem=SamlIdp(cloud.clock.now).cert_pem,
        group_role_map={"Admins": "admin", "Auditors": "auditor"},
    )
    token = cloud.sso.create_scim_token(org.owner)["token"]
    status, group = scim(
        cloud,
        token,
        "POST",
        "/scim/v2/Groups",
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Admins",
            "members": [{"value": org.coach.member_id}],
        },
    )
    assert status == 201 and group["members"][0]["value"] == org.coach.member_id
    assert cloud.tenancy.member(org.org_id, org.coach.member_id)["role"] == "admin"
    assert (
        scim(cloud, token, "POST", "/scim/v2/Groups", {"displayName": "Admins"})[0]
        == 409
    )
    status, _ = scim(
        cloud,
        token,
        "PATCH",
        f"/scim/v2/Groups/{group['id']}",
        {
            "Operations": [
                {
                    "op": "add",
                    "path": "members",
                    "value": [{"value": org.billing.member_id}],
                }
            ]
        },
    )
    assert (
        status == 200
        and cloud.tenancy.member(org.org_id, org.billing.member_id)["role"] == "admin"
    )
    status, listing = scim(cloud, token, "GET", "/scim/v2/Groups")
    assert listing["totalResults"] == 1 and len(listing["Resources"][0]["members"]) == 2
    status, _ = scim(
        cloud,
        token,
        "PATCH",
        f"/scim/v2/Groups/{group['id']}",
        {
            "Operations": [
                {"op": "remove", "path": f'members[value eq "{org.billing.member_id}"]'}
            ]
        },
    )
    assert (
        status == 200
        and len(
            scim(cloud, token, "GET", f"/scim/v2/Groups/{group['id']}")[1]["members"]
        )
        == 1
    )
    status, auditors = scim(
        cloud,
        token,
        "POST",
        "/scim/v2/Groups",
        {"displayName": "Auditors", "members": [{"value": org.admin.member_id}]},
    )
    assert cloud.tenancy.member(org.org_id, org.admin.member_id)["role"] == "auditor"
    owner_group = scim(
        cloud,
        token,
        "POST",
        "/scim/v2/Groups",
        {"displayName": "Everyone", "members": [{"value": org.owner.member_id}]},
    )[1]
    assert cloud.tenancy.member(org.org_id, org.owner.member_id)["role"] == "owner"
    assert (
        scim(cloud, token, "DELETE", f"/scim/v2/Groups/{owner_group['id']}")[0] == 204
    )
    assert scim(cloud, token, "GET", f"/scim/v2/Groups/{owner_group['id']}")[0] == 404
    assert auditors["id"]


def test_sso_and_provisioning_are_scoped_to_their_own_organization(cloud, oidc):
    one = team(cloud)
    two = build_org(cloud, "Second Org", plan="team", seats=25, slug_email="two.test")
    configure_oidc(cloud, one, oidc)
    browser = fresh(cloud)
    assert browser.call("GET", "/api/sso/second-org/start")[0] == 302
    assert browser.header("Location").startswith("/#/signin?sso_error="), (
        "an organization with no SSO has no provider to redirect to"
    )
    enterprise(cloud, two)
    token = cloud.sso.create_scim_token(two.owner)["token"]
    status, listing = scim(cloud, token, "GET", "/scim/v2/Users")
    emails = {r["userName"] for r in listing["Resources"]}
    assert emails and all(e.endswith("@two.test") for e in emails)
    assert scim(cloud, token, "GET", f"/scim/v2/Users/{one.coach.member_id}")[0] == 404
    assert (
        scim(cloud, token, "DELETE", f"/scim/v2/Users/{one.coach.member_id}")[0] == 404
    )
