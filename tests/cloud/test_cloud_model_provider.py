"""The organization's model in the hosted build: Settings > Integrations (``/api/settings/model-provider``).

An owner or admin chooses the platform model (the default), Claude with the organization's own Anthropic API key, or
(Enterprise) the organization's own OpenAI-compatible endpoint, for everyone in the organization. Claude is the
loopback Messages API stand-in of ``tests/test_model_provider.py`` (``model_provider.BASE_URL`` points at it); the
platform model is the Ollama-style stand-in of ``tests/test_cloud_integrations.py``. Nothing reaches the network.
"""

from __future__ import annotations

import json
import logging

import pytest
from cloud_support import PASSWORD, build_org, make_app, sign_in
from test_cloud_integrations import ModelStub, prepare_job
from test_model_provider import KEY, KEY2, ClaudeStub, _presented_key

from aira import model_provider
from aira.hosting import HOOKS
from monosage_cloud.db import many

ROUTE = "/api/settings/model-provider"
# One answer that serves a fit review and a draft alike.
CLAUDE_REPLY = json.dumps(
    {
        "score": 80,
        "summary": "Strong match (from claude).",
        "strengths": ["AWS"],
        "gaps": [],
        "recommendation": "Apply",
        "tailored_resume": "Tailored resume from Claude.",
        "cover_letter": "Dear hiring team, Claude wrote this.",
    }
)
PROFILE = {
    "resume": "Cloud architect with twelve years of AWS delivery. " * 4,
    "application_profile": {
        "full_name": "Cam",
        "email": "cand@acme.test",
        "phone": "+1 555 0100",
        "location": "Seattle",
    },
}


@pytest.fixture
def claude(monkeypatch):
    stub = ClaudeStub().start()
    stub.reply_text = CLAUDE_REPLY
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(model_provider, "BASE_URL", stub.base_url)
    monkeypatch.setattr(model_provider, "MAX_RETRIES", 0)
    yield stub
    stub.stop()


@pytest.fixture
def platform():
    stub = ModelStub()
    yield stub
    stub.stop()


@pytest.fixture
def app(tmp_path, platform):
    HOOKS.reset()
    cloud = make_app(
        tmp_path,
        model_provider="ollama",
        model_url=platform.url,
        model_name="platform-model",
    )
    yield cloud
    cloud.close()


class Seen:
    """Every response body a test got back, to prove a key never appears in one."""

    def __init__(self) -> None:
        self.bodies: list[str] = []

    def call(self, browser, method, path, body=None, expect=None):  # noqa: ANN001, ANN201
        status, data = browser.call(method, path, body)
        self.bodies.append(
            json.dumps(data)
            if not isinstance(data, bytes)
            else data.decode("utf-8", "replace")
        )
        if expect is not None:
            assert status == expect, (method, path, status, data)
        return status, data

    def assert_absent(self, *keys: str) -> None:
        for key in keys:
            assert not any(key in body for body in self.bodies), (
                "a key came back in a response"
            )


def draft(app, org, browser) -> None:  # noqa: ANN001
    assert browser.call("POST", "/api/profile", PROFILE)[0] == 200
    job_id = prepare_job(app, org, org.candidate)
    status, body = browser.call("POST", f"/api/jobs/{job_id}/generate", {})
    assert status == 200, body


def test_an_admin_chooses_claude_and_the_organizations_drafts_go_to_it(
    app, claude, platform, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    org = build_org(app, plan="team")
    admin, candidate, auditor = (
        sign_in(app, org.admin),
        sign_in(app, org.candidate),
        sign_in(app, org.auditor),
    )
    seen = Seen()

    _, before = seen.call(candidate, "GET", ROUTE, expect=200)
    assert before["hosted"] is True and before["scope"] == "organization"
    assert before["provider"] == "platform" and before["can_manage"] is False
    assert [m["id"] for m in before["claude"]["models"]] == list(
        model_provider.CLAUDE_MODELS
    )

    _, saved = seen.call(
        admin,
        "POST",
        ROUTE,
        {"provider": "claude", "api_key": KEY, "model": "claude-opus-5-5"},
        expect=200,
    )
    assert (
        saved["provider"] == "claude"
        and saved["ready"] is True
        and saved["can_manage"] is True
    )
    assert saved["claude"] == saved["claude"] | {
        "configured": True,
        "last4": KEY[-4:],
        "model": "claude-opus-5-5",
    }

    # every member sees the organization's choice; only those who may read integrations see the last four characters
    _, mine = seen.call(candidate, "GET", ROUTE, expect=200)
    assert (
        mine["provider"] == "claude"
        and mine["claude"]["last4"] is None
        and mine["can_manage"] is False
    )
    _, audited = seen.call(auditor, "GET", ROUTE, expect=200)
    assert audited["claude"]["last4"] == KEY[-4:] and audited["can_manage"] is False

    draft(app, org, candidate)
    assert claude.requests, "the organization's Claude key answered the draft"
    last = claude.requests[-1]
    assert (
        _presented_key(last["headers"]) == KEY
        and last["body"]["model"] == "claude-opus-5-5"
    )
    assert "untrusted data" in last["body"]["system"]
    assert not any(r["path"].endswith("/api/chat") for r in platform.requests), (
        "the platform model was not asked"
    )
    assert app.usage.used(org.org_id, "model_calls") >= 1, (
        "a call through the organization's key is still metered"
    )
    assert app.usage.used(org.org_id, "cover_letters") == 1

    _, tested = seen.call(admin, "POST", f"{ROUTE}/test", {}, expect=200)
    assert (
        tested["ok"] is True
        and tested["provider"] == "claude"
        and tested["model_label"] == "Claude Opus 5.5"
    )
    assert claude.requests[-1]["body"]["max_tokens"] == model_provider.TEST_MAX_TOKENS

    # a new key replaces the old one; the model stays
    _, replaced = seen.call(admin, "POST", ROUTE, {"api_key": KEY2}, expect=200)
    assert (
        replaced["claude"]["last4"] == KEY2[-4:]
        and replaced["claude"]["model"] == "claude-opus-5-5"
    )

    # the integrations page shows the same choice
    _, integrations = seen.call(admin, "GET", "/api/org/integrations", expect=200)
    assert integrations["model"]["choice"]["provider"] == "claude"

    seen.assert_absent(KEY, KEY2)
    with app.db.read() as connection:
        audit = json.dumps(many(connection, "SELECT * FROM audit_logs"), default=str)
    assert (
        "integrations.model_provider" in audit
        and KEY not in audit
        and KEY2 not in audit
    )
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert KEY not in logs and KEY2 not in logs
    for path in tmp_path.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert KEY.encode() not in data and KEY2.encode() not in data, (
                f"a key in {path.name}"
            )


def test_only_an_owner_or_admin_changes_the_model(app, claude):
    org = build_org(app, plan="team")
    owner = sign_in(app, org.owner)
    seen = Seen()
    for who in ("candidate", "coach", "auditor", "billing"):
        browser = sign_in(app, getattr(org, who))
        for method, path, body in (
            ("POST", ROUTE, {"provider": "claude", "api_key": KEY}),
            ("POST", f"{ROUTE}/test", {}),
            ("DELETE", f"{ROUTE}/key", None),
        ):
            status, _ = seen.call(browser, method, path, body)
            assert status == 403, (who, method, path)
    assert app.models.in_effect(org.org_id) == ("platform", None)
    assert claude.requests == []
    # the owner may; a candidate's workspace header changes nothing about who decides
    seen.call(owner, "POST", ROUTE, {"provider": "claude", "api_key": KEY}, expect=200)
    status, _ = sign_in(app, org.candidate).call(
        "POST",
        ROUTE,
        {"provider": "platform"},
        headers={"X-Aira-Candidate": org.candidate.member_id},
    )
    assert status == 403 and app.models.in_effect(org.org_id)[0] == "claude"
    seen.assert_absent(KEY)


def test_removing_the_key_falls_back_to_the_platform_model(app, claude, platform):
    org = build_org(app, plan="team")
    admin, candidate = sign_in(app, org.admin), sign_in(app, org.candidate)
    seen = Seen()
    seen.call(admin, "POST", ROUTE, {"provider": "claude", "api_key": KEY}, expect=200)
    _, removed = seen.call(admin, "DELETE", f"{ROUTE}/key", expect=200)
    assert removed["removed"] is True and removed["provider"] == "platform"
    assert (
        removed["claude"]["configured"] is False and removed["claude"]["last4"] is None
    )
    draft(app, org, candidate)
    assert claude.requests == [], "Claude is no longer asked"
    assert platform.requests and platform.requests[-1]["path"].endswith("/api/chat")
    assert '"platform-model"' in platform.requests[-1]["body"]
    # nothing to test against the platform model; removing again is harmless
    assert seen.call(admin, "POST", f"{ROUTE}/test", {})[0] == 400
    assert seen.call(admin, "DELETE", f"{ROUTE}/key", expect=200)[1]["removed"] is False
    seen.assert_absent(KEY)


def test_choosing_the_platform_model_keeps_the_key_for_later(app, claude, platform):
    org = build_org(app, plan="team")
    admin, candidate = sign_in(app, org.admin), sign_in(app, org.candidate)
    admin.call("POST", ROUTE, {"provider": "claude", "api_key": KEY})
    status, body = admin.call("POST", ROUTE, {"provider": "platform"})
    assert (
        status == 200
        and body["provider"] == "platform"
        and body["claude"]["configured"] is True
    )
    draft(app, org, candidate)
    assert claude.requests == [] and platform.requests
    status, body = admin.call("POST", ROUTE, {"provider": "claude"})
    assert status == 200 and body["provider"] == "claude"


def test_bad_input_is_refused_without_echoing_a_key(app, claude):
    org = build_org(app, plan="team")
    admin = sign_in(app, org.admin)
    seen = Seen()
    not_a_key = "sk-wrong-" + "Q" * 30
    for body in (
        {"provider": "claude"},  # no key saved yet
        {"provider": "claude", "api_key": not_a_key},
        {"provider": "claude", "api_key": KEY, "model": "claude-imaginary"},
        {"provider": "gpt"},
        {"api_key": 12345},
        {},
    ):
        status, data = seen.call(admin, "POST", ROUTE, body)
        assert status == 400, (body, data)
    seen.assert_absent(not_a_key, KEY)
    assert app.models.in_effect(org.org_id) == ("platform", None)
    assert not app.secrets.exists(org.org_id, "anthropic_api_key")


def test_the_own_endpoint_stays_an_enterprise_feature(
    app, claude, platform, monkeypatch
):
    monkeypatch.setenv(
        "AIRA_CLOUD_ALLOW_LOCAL_URLS", "1"
    )  # the stand-in listens on loopback
    org = build_org(app, plan="team")
    owner, candidate = sign_in(app, org.owner), sign_in(app, org.candidate)
    endpoint = {"url": platform.url, "model": "tenant-model", "api_key": "tenant-key"}
    status, data = owner.call("GET", ROUTE)
    assert (
        data["openai"]["available"] is False
        and data["openai"]["required_plan"] == "enterprise"
    )
    status, data = owner.call(
        "POST", ROUTE, {"provider": "openai", "endpoint": endpoint}
    )
    assert status == 402 and data["required_plan"] == "enterprise"
    # Claude with the organization's own key is on every plan
    assert owner.call("POST", ROUTE, {"provider": "claude", "api_key": KEY})[0] == 200

    app.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    operator = app.identity.principal_for_token(
        app.identity.operator_login("ops@aira.test", PASSWORD)["token"], kind="operator"
    )
    app.billing.activate_enterprise(operator, org.org_id, seats=100)
    status, data = owner.call(
        "POST", ROUTE, {"provider": "openai", "endpoint": endpoint}
    )
    assert (
        status == 200
        and data["provider"] == "openai"
        and data["openai"]["configured"] is True
    )
    assert "tenant-key" not in json.dumps(data) and data["claude"]["configured"] is True
    draft(app, org, candidate)
    assert platform.requests[-1]["path"].endswith("/chat/completions")
    assert platform.requests[-1]["headers"].get("authorization") == "Bearer tenant-key"
    # saving the endpoint again without a key keeps the stored key
    status, data = owner.call(
        "POST", ROUTE, {"provider": "openai", "endpoint": {**endpoint, "api_key": ""}}
    )
    assert status == 200
    assert (
        json.loads(app.secrets.get(org.org_id, "model_endpoint"))["key"] == "tenant-key"
    )
    # back to Claude: the endpoint stays saved but no longer answers
    status, data = owner.call("POST", ROUTE, {"provider": "claude"})
    assert (
        status == 200
        and data["provider"] == "claude"
        and data["openai"]["configured"] is True
    )
    # removing the endpoint while Claude is in use leaves Claude in use
    assert owner.call("DELETE", "/api/org/integrations/model")[0] == 200
    assert app.models.in_effect(org.org_id)[0] == "claude"


def test_organizations_do_not_share_a_model_choice(app, claude, platform):
    first = build_org(app, plan="team")
    second = build_org(app, "Beta Careers", plan="team", slug_email="beta.test")
    sign_in(app, first.admin).call(
        "POST", ROUTE, {"provider": "claude", "api_key": KEY}
    )
    theirs = sign_in(app, second.admin)
    status, data = theirs.call("GET", ROUTE)
    assert (
        status == 200
        and data["provider"] == "platform"
        and data["claude"]["configured"] is False
    )
    assert KEY[-4:] not in json.dumps(data)
    # removing "the key" in the second organization touches only its own (none)
    assert theirs.call("DELETE", f"{ROUTE}/key")[1]["removed"] is False
    assert app.models.in_effect(first.org_id)[0] == "claude"
    draft(app, second, sign_in(app, second.candidate))
    assert claude.requests == [] and platform.requests, (
        "the second organization's drafts use the platform model"
    )
    with app.db.read() as connection:
        rows = many(
            connection,
            "SELECT org_id FROM org_secrets WHERE name = 'anthropic_api_key'",
        )
    assert [row["org_id"] for row in rows] == [first.org_id]
