# ruff: noqa: F811
"""The small routes the sign-in, onboarding and coach screens add."""

from __future__ import annotations

from cloud_support import build_org, sign_in  # noqa: F401

from monosage_cloud.policy import LINKEDIN_NOTICE


def test_linkedin_notice_is_the_text_the_api_enforces(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    candidate = sign_in(cloud, org.candidate)
    status, data = candidate.call("GET", "/api/me/linkedin-notice")
    assert status == 200
    assert data["notice"] in LINKEDIN_NOTICE and "User Agreement" in data["notice"]
    assert "acknowledge" not in data["notice"]


def test_linkedin_notice_needs_a_session(cloud):
    from cloud_support import Browser

    status, data = Browser(cloud).call("GET", "/api/me/linkedin-notice")
    assert status == 401


def test_the_sign_in_page_is_one_page_for_everyone():
    """No "Monosage staff" link and no separate operator form: the console uses the same sign-in."""
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "auto_apply_assistant"
    page = (static / "cloud-auth.js").read_text(encoding="utf-8")
    console = (static / "cloud-platform.js").read_text(encoding="utf-8")
    assert "Monosage staff" not in page and "ca-staff" not in page
    assert '"/api/auth/login"' in page and "data.operator" in page
    assert "Platform sign-in" not in console and "Operator email" not in console
    assert "/api/platform/auth/login" not in console, "the console signs in on the page"
    assert 'cloud.auth.render("login"' in console


def test_the_sign_in_answer_says_where_the_page_goes(cloud):
    from cloud_support import PASSWORD, Browser, signup

    signup(cloud)
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="support")
    member = Browser(cloud).login("owner@acme.test")
    assert member["next"] == "home" and member["signed_in"] == ["member"]
    status, operator = Browser(cloud).call(
        "POST", "/api/auth/login", {"email": "ops@aira.test", "password": PASSWORD}
    )
    assert status == 200 and operator["next"] == "console"
    assert operator["operator"] == {"role": "support", "mfa_enrolled": False}
