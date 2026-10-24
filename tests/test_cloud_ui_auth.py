# ruff: noqa: F811
"""The small routes the sign-in, onboarding and coach screens add."""

from __future__ import annotations

from cloud_support import build_org, sign_in  # noqa: F401

from aira_cloud.policy import LINKEDIN_NOTICE


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
