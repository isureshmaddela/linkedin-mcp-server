"""Launch tokens: the hosted account opens a local installation of the apps as the signed-in candidate.

A candidate asks ``GET /api/launch/local`` and is redirected to the local installation with a one-time token;
the installation validates it once with a signed ``POST /api/internal/launch/validate``. Every refusal the
design names is pinned here: the wrong person, the wrong return address, the wrong secret or purpose, a used
or expired token, a suspended organization, and too many requests.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from cloud_support import Browser, audit_actions, build_org, make_app, sign_in

from aira.hosting import HOOKS
from aira_cloud.agent import sign

SECRET = "launch-secret-" + "x" * 30
LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def account(tmp_path: Path):
    HOOKS.reset()
    app = make_app(tmp_path, local_app_url=LOCAL, launch_secret=SECRET)
    yield app
    app.close()


def issue(browser: Browser, back: str = LOCAL + "/", **kw):
    return browser.call(
        "GET",
        f"/api/launch/local?return={quote(back, safe='')}",
        headers={"Accept": "application/json"},
        **kw,
    )


def validate(
    app, token: str, *, secret: str = SECRET, purpose: str = "launch", nonce: str = ""
):
    raw = json.dumps({"token": token}).encode()
    stamp = app.clock.now()
    nonce = nonce or __import__("secrets").token_hex(12)
    headers = {
        "X-Aira-Agent-Timestamp": str(stamp),
        "X-Aira-Agent-Nonce": nonce,
        "X-Aira-Agent-Signature": sign(secret, stamp, nonce, raw, purpose),
    }
    return Browser(app).call(
        "POST", "/api/internal/launch/validate", {"token": token}, headers=headers
    )


def token_of(data: dict) -> str:
    return data["redirect"].split("token=", 1)[1]


def test_a_candidate_is_sent_to_the_local_installation_with_a_one_time_token(account):
    org = build_org(account, plan="team")
    candidate = sign_in(account, org.candidate)
    status, data = issue(candidate)
    assert status == 200, data
    assert (
        data["redirect"].startswith(f"{LOCAL}/#/launch?token=")
        and data["expires_in"] == 60
    )
    token = token_of(data)
    with account.db.read() as connection:
        rows = [dict(r) for r in connection.execute("SELECT * FROM launch_tokens")]
    assert (
        len(rows) == 1
        and rows[0]["org_id"] == org.org_id
        and rows[0]["member_id"] == org.candidate.member_id
    )
    assert token not in json.dumps(rows), "stored hashed"
    assert rows[0]["return_url"] == LOCAL + "/" and rows[0]["app"] == "local"
    # a browser gets a redirect instead of JSON
    status, _ = candidate.call("GET", f"/api/launch/local?return={LOCAL}/", raw=True)
    assert status == 302 and candidate.header("Location").startswith(
        f"{LOCAL}/#/launch?token="
    )
    assert candidate.header("Cache-Control") == "no-store"
    # validated once: who it is, which organization, which apps
    status, claims = validate(account, token)
    assert status == 200, claims
    assert claims["email"] == org.candidate.email and claims["role"] == "candidate"
    assert (
        claims["org_id"] == org.org_id
        and claims["org_name"]
        and claims["app"] == "local"
    )
    assert "careers" in claims["apps"]
    status, again = validate(account, token)
    assert status == 410 and "already used" in again["error"]
    assert {"auth.launch_issued", "auth.launch_local"} <= set(
        audit_actions(account, org.org_id, "auth.launch")
    )


def test_a_token_expires_after_a_minute(account):
    org = build_org(account, plan="team")
    token = token_of(issue(sign_in(account, org.candidate))[1])
    account.clock.advance(seconds=61)
    status, body = validate(account, token)
    assert status == 410 and "expired" in body["error"]


def test_only_the_right_secret_and_purpose_validate(account):
    org = build_org(account, plan="team")
    token = token_of(issue(sign_in(account, org.candidate))[1])
    assert validate(account, token, secret="wrong-secret-" + "y" * 30)[0] == 401
    assert validate(account, token, purpose="broker")[0] == 401
    assert validate(account, token, purpose="agent")[0] == 401
    assert validate(account, "not-a-token")[0] == 404
    # a signature cannot be replayed with the same nonce, even for a different token
    status, _ = validate(account, token, nonce="c" * 24)
    assert status == 200
    assert validate(account, token, nonce="c" * 24)[0] == 401


def test_the_return_address_must_be_the_local_installation(account):
    org = build_org(account, plan="team")
    candidate = sign_in(account, org.candidate)
    for bad in (
        "https://evil.example/",
        "http://127.0.0.1:9999/",
        "http://127.0.0.1:8765.evil.example/",
        "http://127.0.0.1:8765/?next=x",
        "http://127.0.0.1:8765/#/jobs",
        "http://user:pw@127.0.0.1:8765/",
        "javascript:alert(1)",
    ):
        status, body = issue(candidate, bad)
        assert status == 400, (bad, status, body)
    status, data = issue(candidate, "http://127.0.0.1:8765/app/")
    assert status == 200 and data["redirect"].startswith(
        "http://127.0.0.1:8765/app/#/launch?token="
    )
    status, data = issue(candidate, "")
    assert status == 200 and data["redirect"].startswith(f"{LOCAL}/#/launch?token=")


def test_staff_cannot_open_a_candidates_installation(account):
    org = build_org(account, plan="team")
    for who in (org.owner, org.admin, org.coach, org.auditor, org.billing):
        status, body = issue(sign_in(account, who))
        assert status == 403, (who.role, status, body)
        assert "candidate" in body["error"]
    coach = sign_in(account, org.coach)
    status, _ = issue(coach, as_candidate=org.candidate.member_id)
    assert status == 403, (
        "acting for a candidate does not open their installation either"
    )
    assert Browser(account).call("GET", "/api/launch/local")[0] == 401


def test_a_suspended_or_read_only_organization_cannot_launch(account):
    org = build_org(account, plan="team")
    candidate = sign_in(account, org.candidate)
    token = token_of(issue(candidate)[1])
    with account.db.write() as connection:
        connection.execute(
            "UPDATE organizations SET status = 'suspended' WHERE org_id = ?",
            (org.org_id,),
        )
    account.entitlements.invalidate(org.org_id)
    status, body = issue(candidate)
    assert status in (401, 402, 403), (status, body)
    status, body = validate(account, token)
    assert status in (402, 403) and body.get("code") == "subscription_inactive", (
        "an issued token stops working too"
    )


def test_a_removed_member_cannot_finish_a_launch(account):
    org = build_org(account, plan="team")
    token = token_of(issue(sign_in(account, org.candidate))[1])
    account.tenancy.remove_member(org.owner, org.candidate.member_id)
    status, body = validate(account, token)
    assert status == 410 and "no longer a member" in body["error"]


def test_issuing_is_rate_limited_per_member(account):
    org = build_org(account, plan="team")
    candidate = sign_in(account, org.candidate)
    statuses = [issue(candidate)[0] for _ in range(6)]
    assert statuses[:5] == [200] * 5 and statuses[5] == 429
    assert issue(sign_in(account, org.other))[0] == 200, (
        "another member has their own allowance"
    )


def test_the_feature_is_off_until_both_settings_exist(tmp_path):
    HOOKS.reset()
    for overrides in (
        {},
        {"local_app_url": LOCAL},
        {"launch_secret": SECRET},
        {"local_app_url": LOCAL, "launch_secret": "short"},
    ):
        app = make_app(
            tmp_path / str(len(overrides)) / str(hash(str(overrides)) % 1000),
            **overrides,
        )
        try:
            org = build_org(app, plan="team")
            status, body = issue(sign_in(app, org.candidate))
            assert status == 409 and body["code"] == "not_configured", overrides
            assert validate(app, "anything", secret=SECRET)[0] in (401, 404), overrides
        finally:
            app.close()


def test_old_tokens_are_swept_by_housekeeping(account):
    org = build_org(account, plan="team")
    issue(sign_in(account, org.candidate))
    account.clock.advance(seconds=2 * 3600)
    account.lifecycle._housekeeping()
    with account.db.read() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM launch_tokens").fetchone()[0] == 0
        )
