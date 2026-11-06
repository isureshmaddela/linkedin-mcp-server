"""Hosted security fixes from the platform review: machine routes and the sign-in limiter (P0-9), capability tokens
in logs (P0-10), credentials revoked with a password (P0-11), outbound calls pinned at call time, identity checks and
the developer platform's buckets."""

from __future__ import annotations

import base64
import io
import json
import logging

import pytest
from cloud_support import (
    PASSWORD,
    Browser,
    build_org,
    make_app,
    sign_in,
    signup,
    token_from_mail,
)

from aira.observability import JsonFormatter, TextFormatter, redact, redact_path
import monosage_cloud.routes  # noqa: F401  (registers the control-plane routes)
from monosage_cloud import crypto
from monosage_cloud.webio import CLOUD_ROUTES

WRONG = "not the right password at all"


def _webhook(
    app, browser: Browser, org_id: str, event_type: str = "noop.review"
) -> int:
    payload, signature = app.billing.standin.make_event(event_type, {"org_id": org_id})
    status, _body = browser.call(
        "POST",
        "/api/billing/webhook/standin",
        payload,
        headers={"X-Aira-Signature": signature, "Content-Type": "application/json"},
    )
    return status


def _login(browser: Browser, email: str, password: str = PASSWORD):
    return browser.call(
        "POST", "/api/auth/login", {"email": email, "password": password}
    )


# ----------------------------------------------------------------------------- P0-9 machine routes


def test_signed_machine_routes_are_marked_and_people_routes_are_not():
    machine = {route.name: route.machine for route in CLOUD_ROUTES if route.machine}
    assert machine["billing_webhook"] == "webhook"
    assert machine["token"] == "oauth" and machine["revoke"] == "oauth"
    assert machine["launch_validate"] == "signed"
    assert any(name.startswith("scim_") for name in machine)
    assert not {"login", "signup_org", "password_forgot"} & set(machine)


def test_a_burst_of_100_webhooks_is_not_locked_out_and_sign_in_is_still_limited(cloud):
    org_id = signup(cloud)["org"]["org_id"]
    browser = Browser(cloud)
    cloud.gateway.auth_limiter.limit = 3
    try:
        for index in range(3):
            assert _login(Browser(cloud), f"nobody{index}@x.test", WRONG)[0] == 401
        assert _login(Browser(cloud), "nobody@x.test", WRONG)[0] == 429, (
            "the address is now locked out of sign-in"
        )
        codes = [_webhook(cloud, browser, org_id) for _ in range(100)]
        assert codes == [200] * 100, sorted(set(codes))
        assert _login(Browser(cloud), "nobody@x.test", WRONG)[0] == 429, (
            "sign-in stays limited"
        )
    finally:
        cloud.gateway.auth_limiter.limit = 30
        cloud.gateway.auth_limiter.reset("auth:127.0.0.1")


def test_machine_routes_have_their_own_bucket_per_provider_and_client(tmp_path):
    app = make_app(tmp_path, machine_requests_per_minute=5)
    org_id = signup(app)["org"]["org_id"]
    browser = Browser(app)
    codes = [_webhook(app, browser, org_id) for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    # another OAuth client is another bucket, and the token endpoint never shared the webhook's
    basic = base64.b64encode(b"client-a:secret").decode()
    for _ in range(5):
        browser.call(
            "POST",
            "/oauth/token",
            {"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
        )
    other = base64.b64encode(b"client-b:secret").decode()
    status, _ = browser.call(
        "POST",
        "/oauth/token",
        {"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {other}"},
    )
    assert status != 429
    app.close()


def test_rejected_signatures_are_limited_per_address(tmp_path):
    app = make_app(tmp_path, machine_failures_per_window=4)
    browser = Browser(app)
    payload, _signature = app.billing.standin.make_event("noop.review", {})
    codes = [
        browser.call(
            "POST",
            "/api/billing/webhook/standin",
            payload,
            headers={
                "X-Aira-Signature": "t=1,v1=bad",
                "Content-Type": "application/json",
            },
        )[0]
        for _ in range(6)
    ]
    assert codes[:4] == [401] * 4 and codes[-1] == 429, codes
    app.close()


# ----------------------------------------------------------------------------- P0-10 tokens in logs


TOKEN = "Q2xvdWRUb2tlbl9GcjBtXzMyX2J5dGVzX3VybHNhZmU"  # shaped like crypto.new_token()


@pytest.mark.parametrize(
    "path",
    [
        f"/api/exports/download/{TOKEN}",
        f"/api/invites/{TOKEN}",
        f"/api/invites/{TOKEN}/accept",
        f"/api/public/{TOKEN}/book",
        f"/p/{TOKEN}",
        f"https://aira.example.com/#/reset/{TOKEN}",
        f"/somewhere/new/{TOKEN}",
        f"/api/sso/acme/callback?code={TOKEN}&state=abc",
        f"/#/launch?token={TOKEN}",
    ],
)
def test_capability_tokens_are_masked(path):
    assert TOKEN not in redact_path(path)
    assert TOKEN not in redact(f"GET {path} -> 200")


def test_ids_and_slugs_stay_readable():
    member = "0123456789abcdef0123456789abcdef"
    path = f"/api/org/members/{member}/role"
    assert redact_path(path) == path
    slug = "/api/sites/acme-outplacement-careers-site-2026"
    assert redact_path(slug) == slug


def test_the_gateway_access_log_never_holds_a_capability_token(cloud, caplog):
    browser = Browser(cloud)
    with caplog.at_level(logging.INFO, logger="monosage_cloud.gateway"):
        browser.call("GET", f"/api/exports/download/{TOKEN}")
        browser.call("GET", f"/api/invites/{TOKEN}")
    lines = [
        r.getMessage() for r in caplog.records if r.name == "monosage_cloud.gateway"
    ]
    assert lines and all(TOKEN not in line for line in lines), lines


def test_both_log_formats_redact_arguments_and_tracebacks():
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "%s %s -> %s", ("GET", f"/p/{TOKEN}", 200), None
    )
    assert TOKEN not in TextFormatter("%(message)s").format(record)
    assert TOKEN not in JsonFormatter().format(record)
    assert record.args[1] == f"/p/{TOKEN}", "the record itself is unchanged"
    try:
        raise ValueError(f"failed for /api/invites/{TOKEN}")
    except ValueError:
        import sys

        failing = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "boom", (), sys.exc_info()
        )
    assert TOKEN not in TextFormatter("%(message)s").format(failing)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.emit(failing)
    assert TOKEN not in stream.getvalue()


def test_the_capability_ingress_turns_the_access_log_off():
    from pathlib import Path

    yaml = pytest.importorskip("yaml")
    text = (
        Path(__file__).resolve().parent.parent / "deploy" / "k8s" / "ingress.yaml"
    ).read_text(encoding="utf-8")
    docs = [d for d in yaml.safe_load_all(text) if d]
    quiet = next(d for d in docs if d["metadata"]["name"] == "aira-capability-links")
    assert (
        quiet["metadata"]["annotations"][
            "nginx.ingress.kubernetes.io/enable-access-log"
        ]
        == "false"
    )
    paths = {p["path"] for p in quiet["spec"]["rules"][0]["http"]["paths"]}
    assert {"/api/exports/download/", "/api/invites/", "/api/public/", "/p/"} <= paths


# ----------------------------------------------------------------------------- P0-11 a new password ends the rest


def _credentials(app, org):
    owner = org.owner
    pat = app.oauth.create_personal_token(
        owner, {"name": "script", "scopes": ["profile"], "days": 90}
    )["token"]
    with app.db.write() as connection:
        oauth = app.oauth._issue(
            connection,
            org_id=org.org_id,
            client_id="client-x",
            member_id=owner.member_id,
            scopes=["profile"],
        )
    other_session = app.identity.login(owner.email, PASSWORD, org=org.org_id)["token"]
    return pat, oauth["access_token"], oauth["refresh_token"], other_session


def _live_kinds(app, org) -> set[str]:
    with app.db.read() as connection:
        rows = connection.execute(
            "SELECT kind FROM oauth_tokens WHERE org_id = ? AND member_id = ? AND revoked_at IS NULL",
            (org.org_id, org.owner.member_id),
        ).fetchall()
    return {row[0] for row in rows}


def test_a_password_change_revokes_every_token_type_and_other_sessions(cloud):
    org = build_org(cloud, plan="team")
    pat, access, refresh, other_session = _credentials(cloud, org)
    assert _live_kinds(cloud, org) == {"pat", "access", "refresh"}
    assert (
        cloud.oauth.principal(pat) is not None
        and cloud.oauth.principal(access) is not None
    )
    new_token = cloud.identity.change_password(
        org.owner, PASSWORD, "a different long passphrase 42"
    )
    assert _live_kinds(cloud, org) == set()
    assert cloud.oauth.principal(pat) is None, "personal access token"
    assert cloud.oauth.principal(access) is None, "OAuth access token"
    assert cloud.oauth._live(refresh) is None, "OAuth refresh token"
    assert cloud.identity.principal_for_token(other_session) is None, "other session"
    assert cloud.identity.principal_for_token(new_token) is not None, (
        "the new session works"
    )
    entry = next(
        row
        for row in cloud.audit.list(
            org.org_id, limit=50, action="auth.credentials_revoked"
        )
    )
    detail = (
        entry["detail"]
        if isinstance(entry["detail"], dict)
        else json.loads(entry["detail"])
    )
    assert detail["reason"] == "password_change"
    assert (
        detail["oauth_pat"] == 1
        and detail["oauth_access"] == 1
        and detail["oauth_refresh"] == 1
    )
    assert detail["sessions"] >= 2


def test_a_password_reset_revokes_every_token_type(cloud):
    org = build_org(cloud, plan="team")
    pat, access, refresh, other_session = _credentials(cloud, org)
    cloud.identity.request_password_reset(org.owner.email)
    token = token_from_mail(cloud, org.owner.email, "password_reset")
    cloud.identity.reset_password(token, "a different long passphrase 42")
    assert cloud.oauth.principal(pat) is None
    assert cloud.oauth.principal(access) is None
    assert cloud.oauth._live(refresh) is None
    assert cloud.identity.principal_for_token(other_session) is None
    actions = [
        row["action"] for row in cloud.audit.list(org.org_id, limit=50, action="auth.")
    ]
    assert "auth.credentials_revoked" in actions and "auth.password_reset" in actions


# ----------------------------------------------------------------------------- scrypt


def test_password_checks_wait_for_a_free_slot(monkeypatch):
    import threading
    import time

    crypto.set_scrypt_concurrency(1)
    try:
        stored = crypto.hash_password("correct horse battery staple")
        crypto._scrypt_slots.acquire()  # someone else is checking a password right now
        monkeypatch.setattr(crypto, "SCRYPT_WAIT_SECONDS", 0.05)
        from monosage_cloud.errors import Unavailable

        with pytest.raises(Unavailable):
            crypto.verify_password("correct horse battery staple", stored)
        releaser = threading.Timer(0.02, crypto._scrypt_slots.release)
        monkeypatch.setattr(crypto, "SCRYPT_WAIT_SECONDS", 5)
        releaser.start()
        started = time.monotonic()
        assert crypto.verify_password("correct horse battery staple", stored)
        assert time.monotonic() - started < 5
    finally:
        crypto.set_scrypt_concurrency(4)


# ----------------------------------------------------------------------------- outbound calls pinned at call time


class _Redirector:
    """A loopback server that answers every request with a redirect to an internal address."""

    def __init__(self) -> None:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                return

            def _go(self) -> None:
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _go  # noqa: N815

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def redirector():
    stub = _Redirector()
    yield stub
    stub.close()


def test_outbound_calls_refuse_redirects(redirector, monkeypatch):
    from monosage_cloud import outbound

    monkeypatch.setenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", "1")
    with pytest.raises(outbound.OutboundError, match="redirect"):
        outbound.request("GET", f"{redirector.url}/.well-known/openid-configuration")


def test_sso_discovery_and_the_model_endpoint_do_not_follow_redirects(
    redirector, monkeypatch
):
    from monosage_cloud import sso
    from monosage_cloud.errors import Unavailable
    from monosage_cloud.models import ModelGateway

    monkeypatch.setenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", "1")
    with pytest.raises(Unavailable):
        sso._fetch(f"{redirector.url}/.well-known/openid-configuration")
    with pytest.raises(RuntimeError, match="redirect"):
        ModelGateway._tenant_post(f"{redirector.url}/v1/chat/completions", {}, {}, 5)


def test_an_address_that_resolves_inside_the_network_at_call_time_is_refused(
    monkeypatch,
):
    """DNS rebinding: the host was public when it was saved; now it resolves to a private address."""
    import socket

    from monosage_cloud import outbound

    monkeypatch.delenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", raising=False)
    monkeypatch.delenv("AIRA_CLOUD_ALLOW_LOCAL_IDP", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))
        ],
    )
    with pytest.raises(outbound.OutboundError, match="not a public address"):
        outbound.request(
            "GET", "https://idp.example.com/.well-known/openid-configuration"
        )
    with pytest.raises(outbound.OutboundError, match="https"):
        outbound.request("GET", "http://idp.example.com/")


# ----------------------------------------------------------------------------- identity


def test_an_idle_session_ends_and_a_used_one_stays(tmp_path):
    app = make_app(tmp_path, session_idle_seconds=3600)
    made = signup(app)
    token = made["token"]
    app.clock.advance(seconds=1800)
    assert app.identity.principal_for_token(token) is not None, "used within the hour"
    app.clock.advance(seconds=1800)
    assert app.identity.principal_for_token(token) is not None, (
        "the last use was 30 minutes ago"
    )
    app.clock.advance(seconds=3700)
    assert app.identity.principal_for_token(token) is None, "unused for over an hour"
    app.clock.advance(seconds=10)
    assert app.identity.principal_for_token(token) is None, "and it stays ended"
    with app.db.read() as connection:
        revoked = connection.execute(
            "SELECT revoked_at FROM sessions WHERE token_hash = ?",
            (crypto.hash_token(token),),
        ).fetchone()[0]
    assert revoked
    app.close()


def test_operators_have_a_shorter_idle_timeout(cloud):
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    token = cloud.identity.operator_login("ops@aira.test", PASSWORD)["token"]
    assert cloud.identity.principal_for_token(token, kind="operator") is not None
    cloud.clock.advance(seconds=cloud.config.operator_session_idle_seconds + 61)
    assert cloud.identity.principal_for_token(token, kind="operator") is None


def _signup_call(app, email: str, *, adult=True):  # noqa: ANN001, ANN202
    body = {
        "organization": "",
        "name": "Sam Signup",
        "email": email,
        "password": PASSWORD,
    }
    if adult is not None:
        body["adult"] = adult
    return Browser(app).call("POST", "/api/auth/signup-org", body)


def test_sign_up_needs_the_adult_confirmation(cloud):
    for adult in (None, False, "yes"):
        status, body = _signup_call(cloud, "kid@family.test", adult=adult)
        assert status == 400 and body["code"] == "age_confirmation_required", body
    status, body = _signup_call(cloud, "grown@family.test")
    assert status == 201, body
    entry = next(
        iter(cloud.audit.list(body["org"]["org_id"], limit=10, action="org.create"))
    )
    detail = (
        entry["detail"]
        if isinstance(entry["detail"], dict)
        else json.loads(entry["detail"])
    )
    assert detail.get("age_attested") is True


def test_sign_up_refuses_throwaway_mailboxes_and_limits_addresses_and_domains(tmp_path):
    app = make_app(tmp_path, signups_per_ip_hour=2, signups_per_domain_hour=2)
    limiter = app.identity.signup_ip_limiter
    for email in ("someone@mailinator.com", "x@eu.yopmail.com"):
        status, body = _signup_call(app, email)
        assert status == 400 and body["code"] == "disposable_email", body
    assert _signup_call(app, "a@one.test")[0] == 201
    assert _signup_call(app, "b@two.test")[0] == 201
    status, body = _signup_call(app, "c@three.test")
    assert status == 429 and body["code"] == "signup_limited", (
        "the address's hourly limit"
    )
    limiter.reset("signup-ip:127.0.0.1")
    assert _signup_call(app, "d@two.test")[0] == 201
    limiter.reset("signup-ip:127.0.0.1")
    status, body = _signup_call(app, "e@two.test")
    assert status == 429 and "two.test" in body["error"], "the domain's hourly limit"
    for index in range(3):
        limiter.reset("signup-ip:127.0.0.1")
        assert _signup_call(app, f"p{index}@gmail.com")[0] == 201, (
            "large mailbox providers get more room"
        )
    app.close()


def _operator(app):  # noqa: ANN001, ANN202
    app.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    return app.identity.principal_for_token(
        app.identity.operator_login("ops@aira.test", PASSWORD)["token"], kind="operator"
    )


def _sole_owner_with_mfa(app):  # noqa: ANN001, ANN202
    made = signup(app, "Solo Org", "solo@solo.test")
    owner = made["owner"]
    begun = app.identity.mfa_begin(owner)
    app.identity.mfa_enable(owner, crypto.totp_code(begun["secret"], app.clock.now()))
    return made["org"]["org_id"], owner


def test_a_sole_owner_is_recovered_only_after_a_delay_and_can_cancel(cloud):
    from monosage_cloud.errors import Forbidden, Gone, ValidationFailed

    operator = _operator(cloud)
    org_id, owner = _sole_owner_with_mfa(cloud)
    with pytest.raises(ValidationFailed):
        cloud.identity.request_recovery(operator, org_id, owner.member_id, "short")
    started = cloud.identity.request_recovery(
        operator,
        org_id,
        owner.member_id,
        "Ticket 4411: called back on the number on file",
    )
    mail = cloud.mail.latest("solo@solo.test", "account_recovery")
    assert mail and "/#/recovery-cancel/" in mail["body"]
    with pytest.raises(Forbidden) as early:
        cloud.identity.complete_recovery(operator, started["recovery_id"])
    assert early.value.code == "recovery_waiting"
    # the real owner cancels it from the email
    token = mail["body"].split("/#/recovery-cancel/", 1)[1].split()[0]
    status, body = Browser(cloud).call(
        "POST", "/api/auth/recovery/cancel", {"token": token}
    )
    assert status == 200 and body["status"] == "cancelled"
    cloud.clock.advance(seconds=cloud.config.recovery_delay_seconds + 1)
    with pytest.raises(Gone):
        cloud.identity.complete_recovery(operator, started["recovery_id"])
    assert cloud.tenancy.member(org_id, owner.member_id)["mfa_enabled_at"]
    actions = [
        r["action"] for r in cloud.audit.list(org_id, limit=50, action="auth.recovery")
    ]
    assert "auth.recovery_requested" in actions and "auth.recovery_cancelled" in actions


def test_a_completed_recovery_removes_the_factor_and_ends_every_credential(cloud):
    operator = _operator(cloud)
    org_id, owner = _sole_owner_with_mfa(cloud)
    pat = cloud.oauth.create_personal_token(
        owner, {"name": "script", "scopes": ["profile"], "days": 90}
    )["token"]
    started = cloud.identity.request_recovery(
        operator, org_id, owner.member_id, "Ticket 4412: identity document checked"
    )
    cloud.clock.advance(seconds=cloud.config.recovery_delay_seconds + 1)
    done = cloud.identity.complete_recovery(operator, started["recovery_id"])
    assert done["status"] == "completed"
    assert not cloud.tenancy.member(org_id, owner.member_id)["mfa_enabled_at"]
    assert cloud.oauth.principal(pat) is None
    assert cloud.mail.latest("solo@solo.test", "account_recovered")
    actions = [
        r["action"] for r in cloud.audit.list(org_id, limit=50, action="auth.recovery")
    ]
    assert "auth.recovery_completed" in actions
    events = [e["action"] for e in cloud.platform_audit.list(org_id=org_id, limit=50)]
    assert "member.recovery_requested" in events
    assert "member.recovery_completed" in events


def test_recovery_is_refused_when_another_owner_can_reset_it(cloud):
    from cloud_support import add_member

    from monosage_cloud.errors import Conflict

    operator = _operator(cloud)
    org_id, owner = _sole_owner_with_mfa(cloud)
    add_member(cloud, owner, "second@solo.test", "owner")
    with pytest.raises(Conflict) as refused:
        cloud.identity.request_recovery(
            operator, org_id, owner.member_id, "Ticket 4413: called back on file"
        )
    assert refused.value.code == "other_owner"


def test_the_recovery_routes_need_an_operator(cloud):
    org = build_org(cloud, plan="team")
    owner = sign_in(cloud, org.owner)
    path = f"/api/platform/tenants/{org.org_id}/members/{org.owner.member_id}/recovery"
    assert owner.call("POST", path, {"reason": "x" * 20})[0] == 401
    assert owner.call("GET", "/api/platform/recoveries")[0] == 401


# ----------------------------------------------------------------------------- developer platform


def test_service_apps_have_a_bucket_per_organization_and_app():
    from monosage_cloud.gateway import member_bucket
    from monosage_cloud.rbac import Principal

    def app_principal(org: str, client: str) -> Principal:
        return Principal(
            kind="member",
            org_id=org,
            member_id="",
            role="admin",
            extra={"service": True, "oauth": {"client_id": client}},
        )

    keys = {
        member_bucket(app_principal("org-a", "client-1")),
        member_bucket(app_principal("org-b", "client-1")),
        member_bucket(app_principal("org-a", "client-2")),
    }
    assert len(keys) == 3
    person = Principal(kind="member", org_id="org-a", member_id="m1", role="owner")
    assert member_bucket(person) == "member:m1"


def test_a_tokens_last_use_is_written_at_most_once_a_minute(cloud):
    org = build_org(cloud, plan="team")
    pat = cloud.oauth.create_personal_token(
        org.owner, {"name": "script", "scopes": ["profile"], "days": 90}
    )["token"]

    def last_used() -> int | None:
        with cloud.db.read() as connection:
            return connection.execute(
                "SELECT last_used_at FROM oauth_tokens WHERE token_hash = ?",
                (crypto.hash_token(pat),),
            ).fetchone()[0]

    assert cloud.oauth.principal(pat) is not None
    first = last_used()
    cloud.clock.advance(seconds=30)
    cloud.oauth.principal(pat)
    assert last_used() == first
    cloud.clock.advance(seconds=31)
    cloud.oauth.principal(pat)
    assert last_used() == first + 61
