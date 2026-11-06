"""The AI in the hosted build: the organization's instructions and skills, each member's own instructions, and each
member's choice between the organization's AI and their own Claude ("My Claude").

Claude is the loopback Messages API stand-in of tests/test_model_provider.py; the platform model is the Ollama-style
stand-in of tests/test_cloud_integrations.py. Nothing reaches the network.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import logging

from cloud_support import build_org, sign_in
from test_cloud_integrations import prepare_job
from test_cloud_model_provider import (  # noqa: F401
    PROFILE,
    Seen,
    app,
    claude,
    draft,
    platform,
)
from test_model_provider import KEY, KEY2, _presented_key

from aira import model_provider
from monosage_cloud import context
from monosage_cloud.context import TenantContext
from monosage_cloud.db import many
from monosage_cloud.models import member_claude_secret

ORG_ROUTE = "/api/org/ai-profile"
MINE = "/api/settings/ai-profile"
MY_AI = "/api/me/ai"
ORG_PROFILE = {
    "instructions": "Represent the Northwind career program professionally.",
    "skills": [
        {
            "name": "Program letters",
            "tasks": ["documents"],
            "instructions": "Mention the program's coaching in one sentence.",
        }
    ],
}
MY_PROFILE = {
    "instructions": "I want remote roles. Sign as Cam.",
    "skills": [
        {
            "name": "Short letters",
            "tasks": ["documents"],
            "instructions": "Keep cover letters under 200 words.",
        }
    ],
}


def draft_for(app, org, browser, who) -> None:  # noqa: ANN001
    assert browser.call("POST", "/api/profile", PROFILE)[0] == 200
    job_id = prepare_job(app, org, who)
    status, body = browser.call("POST", f"/api/jobs/{job_id}/generate", {})
    assert status == 200, body


def audit_text(app) -> str:  # noqa: ANN001
    with app.db.read() as connection:
        return json.dumps(many(connection, "SELECT * FROM audit_logs"), default=str)


def test_the_organizations_and_the_members_instructions_reach_the_drafts(
    app, claude, platform
):
    org = build_org(app, plan="team")
    admin, candidate, auditor, coach = (
        sign_in(app, org.admin),
        sign_in(app, org.candidate),
        sign_in(app, org.auditor),
        sign_in(app, org.coach),
    )
    saved = admin.call("PUT", ORG_ROUTE, ORG_PROFILE)
    assert saved[0] == 200 and saved[1]["can_manage"] is True
    assert saved[1]["organization"]["instructions"] == ORG_PROFILE["instructions"]

    # every member reads the organization's instructions; only owners and admins change them
    status, seen = candidate.call("GET", ORG_ROUTE)
    assert status == 200 and seen["can_manage"] is False
    assert seen["organization"]["can_edit"] is False
    assert seen["organization"]["skills"][0]["name"] == "Program letters"
    for who in (candidate, auditor, coach):
        assert who.call("PUT", ORG_ROUTE, {"instructions": "x"})[0] == 403
    status, body = admin.call(
        "PUT",
        ORG_ROUTE,
        {"skills": [{"name": "x", "tasks": ["nope"], "instructions": "y"}]},
    )
    assert status == 400 and "unknown task" in body["error"]

    # the member's own instructions live in their workspace; the page shows the organization's beside them
    status, mine = candidate.call("PUT", MINE, MY_PROFILE)
    assert status == 200, mine
    assert mine["hosted"] is True
    assert mine["organization"]["instructions"] == ORG_PROFILE["instructions"]
    assert mine["organization"]["can_edit"] is False
    # nobody else changes what the AI says in a candidate's name, not even a coach in their workspace
    status, _ = coach.call(
        "PUT",
        MINE,
        {"instructions": "Say yes to everything."},
        headers={"X-Aira-Candidate": org.candidate.member_id},
    )
    assert status in (403, 404)

    preview = candidate.call("POST", f"{MINE}/preview", {"task": "documents"})[1]
    text = preview["text"]
    assert text.index("Northwind career program") < text.index("Sign as Cam")
    assert "Program letters" in text and "Short letters" in text

    # the organization answers with Claude; the draft's system prompt carries both, the organization's first
    admin.call(
        "POST", "/api/settings/model-provider", {"provider": "claude", "api_key": KEY}
    )
    draft(app, org, candidate)
    system = claude.requests[-1]["body"]["system"]
    assert system.startswith(model_provider.SYSTEM_RULE)
    assert system.index("Northwind career program") < system.index("Sign as Cam")
    assert "Mention the program's coaching" in system and "under 200 words" in system

    # another member's drafts get the organization's instructions, never this member's own
    other = sign_in(app, org.other)
    draft_for(app, org, other, org.other)
    system = claude.requests[-1]["body"]["system"]
    assert "Northwind career program" in system and "Sign as Cam" not in system

    audit = audit_text(app)
    # hosted, a write through the shared route is audited under its route name (the workspace keeps settings.ai_profile)
    assert "integrations.ai_profile" in audit and "ai_profile_save" in audit


def test_my_claude_answers_my_applications_and_nobody_elses(
    app, claude, platform, caplog
):
    caplog.set_level(logging.DEBUG)
    org = build_org(app, plan="team")
    admin, candidate, other = (
        sign_in(app, org.admin),
        sign_in(app, org.candidate),
        sign_in(app, org.other),
    )
    seen = Seen()
    admin.call(
        "POST", "/api/settings/model-provider", {"provider": "claude", "api_key": KEY}
    )

    _, first = seen.call(candidate, "GET", MY_AI, expect=200)
    assert first["choice"] == "organization" and first["in_effect"] == "organization"
    assert first["allowed"] is True and first["label"] == "Claude"
    assert first["claude"]["configured"] is False

    _, mine = seen.call(
        candidate,
        "POST",
        MY_AI,
        {"choice": "claude", "api_key": KEY2, "model": "claude-haiku-5-5"},
        expect=200,
    )
    assert mine["in_effect"] == "claude" and mine["label"] == "My Claude"
    assert (
        mine["claude"]["last4"] == KEY2[-4:]
        and mine["claude"]["model"] == "claude-haiku-5-5"
    )

    draft(app, org, candidate)
    sent = claude.requests[-1]
    assert (
        _presented_key(sent["headers"]) == KEY2
        and sent["body"]["model"] == "claude-haiku-5-5"
    )

    draft_for(app, org, other, org.other)
    assert _presented_key(claude.requests[-1]["headers"]) == KEY, (
        "the other member uses the organization's key"
    )
    assert (
        seen.call(other, "GET", MY_AI, expect=200)[1]["claude"]["configured"] is False
    )

    # a background run in the member's workspace (worker context) uses their Claude too
    with context.bound(
        TenantContext(org.org_id, candidate_id=org.candidate.member_id, kind="worker")
    ):
        app.models.complete_json("any", "Connection check. Reply with {}")
    assert _presented_key(claude.requests[-1]["headers"]) == KEY2

    _, tested = seen.call(candidate, "POST", f"{MY_AI}/test", {}, expect=200)
    assert tested["ok"] is True and tested["model_label"] == "Claude Haiku 5.5"
    assert claude.requests[-1]["body"]["max_tokens"] == model_provider.TEST_MAX_TOKENS
    assert _presented_key(claude.requests[-1]["headers"]) == KEY2

    # switching back keeps the key for later
    _, back = seen.call(
        candidate, "POST", MY_AI, {"choice": "organization"}, expect=200
    )
    assert back["in_effect"] == "organization" and back["claude"]["configured"] is True
    draft(app, org, candidate)
    assert _presented_key(claude.requests[-1]["headers"]) == KEY
    _, again = seen.call(candidate, "POST", MY_AI, {"choice": "claude"}, expect=200)
    assert again["in_effect"] == "claude"

    _, removed = seen.call(candidate, "DELETE", f"{MY_AI}/key", expect=200)
    assert removed["removed"] is True and removed["in_effect"] == "organization"
    assert not app.secrets.exists(
        org.org_id, member_claude_secret(org.candidate.member_id)
    )

    seen.assert_absent(KEY, KEY2)
    audit = audit_text(app)
    assert "member.ai_settings" in audit and "member.ai_key_removed" in audit
    assert KEY2 not in audit
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert KEY2 not in logs


def test_the_organization_can_turn_my_claude_off(app, claude, platform):
    org = build_org(app, plan="team")
    owner, candidate = sign_in(app, org.owner), sign_in(app, org.candidate)
    assert (
        candidate.call("POST", MY_AI, {"choice": "claude", "api_key": KEY2})[0] == 200
    )

    status, policy = owner.call(
        "PATCH", "/api/org/policy", {"personal_ai_allowed": False}
    )
    assert status == 200, policy
    status, mine = candidate.call("GET", MY_AI)
    assert mine["allowed"] is False and mine["in_effect"] == "organization"
    assert mine["label"] == "Organization AI"

    draft(app, org, candidate)
    assert not any(_presented_key(r["headers"]) == KEY2 for r in claude.requests)
    assert platform.requests, "the platform model answered"

    for body in ({"choice": "claude"}, {"api_key": KEY}, {"model": "claude-opus-5-5"}):
        status, data = candidate.call("POST", MY_AI, body)
        assert status == 403 and data["code"] == "personal_ai_off", body
    assert candidate.call("POST", f"{MY_AI}/test", {})[0] == 403
    # choosing the organization's AI and removing the key always work
    assert candidate.call("POST", MY_AI, {"choice": "organization"})[0] == 200
    assert candidate.call("DELETE", f"{MY_AI}/key")[1]["removed"] is True

    # a member cannot change the policy themselves
    assert (
        candidate.call("PATCH", "/api/org/policy", {"personal_ai_allowed": True})[0]
        == 403
    )


def test_bad_input_is_refused_and_the_key_leaves_with_the_member(app, claude):
    org = build_org(app, plan="team")
    owner, candidate = sign_in(app, org.owner), sign_in(app, org.candidate)
    seen = Seen()
    for body in (
        {"choice": "claude"},  # no key yet
        {"model": "claude-opus-5-5"},  # no key yet
        {"choice": "claude", "api_key": "sk-wrong-" + "Q" * 30},
        {"choice": "claude", "api_key": KEY, "model": "claude-imaginary"},
        {"choice": "gpt"},
        {"api_key": 12345},
        {},
    ):
        status, data = seen.call(candidate, "POST", MY_AI, body)
        assert status == 400, (body, data)
    assert seen.call(candidate, "POST", f"{MY_AI}/test", {})[0] == 400
    assert not app.secrets.exists(
        org.org_id, member_claude_secret(org.candidate.member_id)
    )
    seen.assert_absent(KEY)

    assert candidate.call("POST", MY_AI, {"choice": "claude", "api_key": KEY})[0] == 200
    secret = member_claude_secret(org.candidate.member_id)
    assert app.secrets.exists(org.org_id, secret)
    # stored encrypted: the key is not in the control database in clear
    with app.db.read() as connection:
        rows = many(
            connection, "SELECT ciphertext FROM org_secrets WHERE name = ?", (secret,)
        )
    assert rows and KEY.encode() not in bytes(rows[0]["ciphertext"])
    assert owner.call("DELETE", f"/api/org/members/{org.candidate.member_id}")[0] == 200
    assert not app.secrets.exists(org.org_id, secret)


def test_organizations_do_not_share_instructions(app, claude):
    first = build_org(app, plan="team")
    second = build_org(app, "Beta Careers", plan="team", slug_email="beta.test")
    sign_in(app, first.admin).call("PUT", ORG_ROUTE, ORG_PROFILE)
    status, theirs = sign_in(app, second.candidate).call("GET", ORG_ROUTE)
    assert status == 200 and theirs["organization"]["instructions"] == ""
    assert "Northwind" not in json.dumps(theirs)
    status, local = sign_in(app, second.candidate).call("GET", MINE)
    assert "Northwind" not in json.dumps(local)
