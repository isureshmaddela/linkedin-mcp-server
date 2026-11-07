"""Workday accounts in the hosted build: the candidate's own employer sign-ins, encrypted under the organization's key.

The candidate (or an owner or admin in their personal workspace) lists, adds, changes and removes them through the
gateway; a coach, an administrator, an auditor, another candidate, another organization and a support grant never
can. No response, audit entry, export or file at rest holds a password, and the worker's auto-apply reads them in the
bound workspace.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from cloud_support import CONSENTS, build_org, local_module, seed_workspace, sign_in
from cryptography.fernet import Fernet, InvalidToken

from aira import workday_accounts
from monosage_cloud.policy import POLICY
from monosage_cloud.rbac import Principal

BASE = "/api/autopilot/workday-accounts"
HOST = "acme.wd5.myworkdayjobs.com"
OTHER_HOST = "globex.wd1.myworkdayjobs.com"
URL = f"https://{HOST}/en-US/External/job/Remote/Cloud-Architect_R1"
SECRET = "Wd-Secret-Pass-9431!"
NEW_SECRET = "Wd-Changed-Pass-2207!"
EMAIL = "cam.workday@example.com"
ROUTES = (
    "airaapi__workday_accounts__accounts_list",
    "airaapi__workday_accounts__accounts_add",
    "airaapi__workday_accounts__accounts_update",
    "airaapi__workday_accounts__accounts_delete",
    "airaapi__workday_accounts__accounts_settings",
)


def text(body) -> str:
    return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)


def ready(cloud, org, who: str = "candidate"):
    """A candidate with a seeded workspace and the onboarding consents, signed in."""
    member = getattr(org, who)
    seed_workspace(cloud, org.org_id, member.member_id)
    cloud.tenancy.record_consent(member, CONSENTS)
    return sign_in(cloud, member)


def ciphertext(cloud, org_id: str, candidate_id: str, host: str) -> bytes:
    local = local_module()
    with cloud.workspaces.bind(org_id, candidate_id, kind="system", source="test"):
        with local._connect() as connection:
            row = connection.execute(
                "SELECT password_ciphertext FROM workday_accounts WHERE host = ?",
                (host,),
            ).fetchone()
    assert row is not None
    return bytes(row[0])


def no_password_anywhere(root: Path, *secrets: str) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for secret in secrets:
                assert secret.encode() not in data, path


def zip_members(data: bytes, prefix: str = ""):
    """Every file in an export archive, including the candidates' archives inside an organization export."""
    with zipfile.ZipFile(io.BytesIO(data)) as opened:
        for name in opened.namelist():
            content = opened.read(name)
            if name.endswith(".zip"):
                yield from zip_members(content, f"{prefix}{name}/")
            else:
                yield f"{prefix}{name}", content


def test_the_routes_are_the_candidates_own_and_audited():
    for name in ROUTES:
        rule = POLICY[name]
        assert rule.scope == "candidate" and rule.permission == "credentials.manage"
    assert not POLICY["airaapi__workday_accounts__accounts_list"].write
    for name in ROUTES[1:4]:
        assert POLICY[name].write and POLICY[name].audit


def test_a_candidate_adds_lists_changes_and_removes_without_a_password_coming_back(
    cloud,
):
    org = build_org(cloud)
    candidate_id = org.candidate.member_id
    browser = ready(cloud, org)

    status, body = browser.call("GET", BASE)
    assert status == 200 and body == {"accounts": [], "auto_create": False}

    status, body = browser.call(
        "POST",
        BASE,
        {"host": f"https://{HOST}/en-US/External", "email": EMAIL, "password": SECRET},
    )
    assert status == 201, body
    assert SECRET not in text(body) and EMAIL not in text(body)
    assert body["account"]["host"] == HOST and body["account"]["has_password"] is True
    assert "password" not in body["account"]

    # "Create it for me" uses the profile email and a generated password that never leaves the server.
    status, body = browser.call(
        "POST", BASE, {"host": OTHER_HOST, "create_for_me": True}
    )
    assert status == 201 and body["account"]["origin"] == "generated", body
    status, body = browser.call(
        "POST", BASE, {"host": HOST, "email": EMAIL, "password": "again"}
    )
    assert status == 400, body

    status, body = browser.call("GET", BASE)
    assert status == 200
    assert [item["host"] for item in body["accounts"]] == [HOST, OTHER_HOST]
    assert body["accounts"][0]["email"] == workday_accounts.mask_email(EMAIL)
    for item in body["accounts"]:
        assert "password" not in item and "password_ciphertext" not in item
    assert SECRET not in text(body)

    status, body = browser.call("PATCH", f"{BASE}/{HOST}", {"password": NEW_SECRET})
    assert status == 200 and NEW_SECRET not in text(body), body
    status, body = browser.call(
        "POST", "/api/autopilot/workday-settings", {"auto_create": True}
    )
    assert status == 200 and body["auto_create"] is True

    # At rest the password is ciphertext under the organization's data key, and nothing else reads it.
    stored = ciphertext(cloud, org.org_id, candidate_id, HOST)
    assert Fernet(cloud.secrets.key(org.org_id)).decrypt(stored).decode() == NEW_SECRET
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(stored)
    no_password_anywhere(cloud.config.data_root, SECRET, NEW_SECRET)

    # The organization's audit records who changed what, never the value.
    rows = cloud.audit.list(org.org_id, limit=500)
    actions = [row["action"] for row in rows]
    assert "candidate.airaapi__workday_accounts__accounts_add" in actions
    assert "candidate.airaapi__workday_accounts__accounts_update" in actions
    assert SECRET not in text(rows) and NEW_SECRET not in text(rows)
    status, workspace_audit = browser.call("GET", "/api/audit")
    assert status == 200 and NEW_SECRET not in text(workspace_audit)

    # Neither the candidate's own export, a staff export of the candidate, nor the organization's export carries them.
    exports = [
        (org.candidate, cloud.lifecycle.export_candidate(org.candidate, candidate_id)),
        (org.owner, cloud.lifecycle.export_candidate(org.owner, candidate_id)),
        (org.owner, cloud.lifecycle.export_organization(org.owner)),
    ]
    for principal, export in exports:
        _row, archive = cloud.lifecycle.export_for(principal, export["export_id"])
        for name, content in zip_members(archive):
            assert b"password_ciphertext" not in content, name
            assert NEW_SECRET.encode() not in content and stored not in content, name

    status, body = browser.call("DELETE", f"{BASE}/{HOST}")
    assert status == 200 and body == {"ok": True}
    assert browser.call("DELETE", f"{BASE}/{HOST}")[0] == 404
    status, body = browser.call("GET", BASE)
    assert [item["host"] for item in body["accounts"]] == [OTHER_HOST]


def test_staff_other_candidates_and_support_never_reach_them(cloud):
    org = build_org(cloud)
    candidate_id = org.candidate.member_id
    candidate = ready(cloud, org)
    assert (
        candidate.call(
            "POST", BASE, {"host": HOST, "email": EMAIL, "password": SECRET}
        )[0]
        == 201
    )
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "approve")
    attempts = (
        ("GET", BASE, None),
        ("POST", BASE, {"host": OTHER_HOST, "email": EMAIL, "password": "x"}),
        ("PATCH", f"{BASE}/{HOST}", {"password": "taken-over"}),
        ("DELETE", f"{BASE}/{HOST}", None),
        ("POST", "/api/autopilot/workday-settings", {"auto_create": True}),
    )
    for who in ("owner", "admin", "coach", "auditor", "billing", "other"):
        browser = sign_in(cloud, getattr(org, who))
        for method, path, body in attempts:
            status, answer = browser.call(method, path, body, as_candidate=candidate_id)
            assert status in (403, 404), (who, method, path, status, answer)
            assert SECRET not in text(answer)
    # The other candidate's own list is their own, and empty.
    other = ready(cloud, org, "other")
    assert other.call("GET", BASE)[1]["accounts"] == []

    # Even an assigned coach the organization lets into messages holds no credentials permission.
    assert not cloud.authz.can(
        org.coach, "credentials.manage", candidate_id=candidate_id
    )
    for role in ("admin", "auditor"):
        support = Principal(
            kind="operator",
            org_id=org.org_id,
            operator_id="op",
            operator_role="support",
            role=role,
            support_access_id="grant",
        )
        assert not cloud.authz.can(
            support, "credentials.manage", candidate_id=candidate_id
        )

    # Nothing changed.
    status, body = candidate.call("GET", BASE)
    assert [item["host"] for item in body["accounts"]] == [HOST]
    assert body["auto_create"] is False
    assert (
        Fernet(cloud.secrets.key(org.org_id))
        .decrypt(ciphertext(cloud, org.org_id, candidate_id, HOST))
        .decode()
        == SECRET
    )


def test_another_organization_cannot_see_or_touch_them(cloud):
    alpha = build_org(cloud, "Alpha Org", slug_email="alpha.test")
    beta = build_org(cloud, "Beta Org", slug_email="beta.test")
    alpha_candidate = ready(cloud, alpha)
    beta_candidate = ready(cloud, beta)
    assert (
        alpha_candidate.call(
            "POST", BASE, {"host": HOST, "email": EMAIL, "password": SECRET}
        )[0]
        == 201
    )
    target = alpha.candidate.member_id
    for browser in (beta_candidate, sign_in(cloud, beta.owner)):
        for method, path, body in (
            ("GET", BASE, None),
            ("PATCH", f"{BASE}/{HOST}", {"password": "x"}),
            ("DELETE", f"{BASE}/{HOST}", None),
        ):
            status, answer = browser.call(method, path, body, as_candidate=target)
            assert status == 404, (method, path, status, answer)
    # The same employer site in another organization is a separate account under a separate key.
    assert beta_candidate.call("GET", BASE)[1]["accounts"] == []
    assert (
        beta_candidate.call(
            "POST", BASE, {"host": HOST, "email": EMAIL, "password": NEW_SECRET}
        )[0]
        == 201
    )
    alpha_cipher = ciphertext(cloud, alpha.org_id, target, HOST)
    with pytest.raises(InvalidToken):
        Fernet(cloud.secrets.key(beta.org_id)).decrypt(alpha_cipher)
    assert (
        Fernet(cloud.secrets.key(alpha.org_id)).decrypt(alpha_cipher).decode() == SECRET
    )
    assert cloud.workspaces.data_path(
        alpha.org_id, target
    ) != cloud.workspaces.data_path(beta.org_id, beta.candidate.member_id)


def test_an_owner_keeps_their_own_in_their_personal_workspace(cloud):
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/workspace/own", {})[0] == 201
    mine = org.owner.member_id
    status, body = owner.call(
        "POST",
        BASE,
        {"host": HOST, "email": EMAIL, "password": SECRET},
        as_candidate=mine,
    )
    assert status == 201 and SECRET not in text(body), body
    assert owner.call("GET", BASE, as_candidate=mine)[1]["accounts"][0]["host"] == HOST
    admin = sign_in(cloud, org.admin)
    assert admin.call("GET", BASE, as_candidate=mine)[0] in (403, 404)


def test_hosted_auto_apply_gets_the_decrypted_account_in_the_bound_workspace(
    cloud, monkeypatch
):
    org = build_org(cloud)
    candidate_id = org.candidate.member_id
    browser = ready(cloud, org)
    assert (
        browser.call("POST", BASE, {"host": HOST, "email": EMAIL, "password": SECRET})[
            0
        ]
        == 201
    )
    local = local_module()
    seen = {}

    class Captured(Exception):
        pass

    def fake_engine(request):
        seen["account"] = request.workday_account
        raise Captured

    monkeypatch.setattr(local, "run_apply_engine", fake_engine)
    # The worker binds the candidate's workspace (monosage_cloud/worker.py) and runs the same apply code as the local build.
    with cloud.workspaces.bind(
        org.org_id, candidate_id, kind="worker", source="autopilot"
    ):
        with pytest.raises(Captured):
            local._apply_with_engine(
                {"job_id": "job-wd-1", "title": "Cloud Architect", "company": "Acme"},
                URL,
                "workday",
                {},
                False,
            )
        account = seen["account"]
        assert account is not None and account.host == HOST
        assert account.email == EMAIL and account.password == SECRET
        account.on_result("active", "")
    status, body = browser.call("GET", BASE)
    assert (
        body["accounts"][0]["state"] == "active" and body["accounts"][0]["last_used_at"]
    )

    # Another organization's worker, bound to its own candidate, finds no account for that site.
    beta = build_org(cloud, "Beta Org", slug_email="beta.test")
    seed_workspace(cloud, beta.org_id, beta.candidate.member_id)
    with cloud.workspaces.bind(
        beta.org_id, beta.candidate.member_id, kind="worker", source="autopilot"
    ):
        assert (
            workday_accounts.credentials_for_run(
                local._connect, local._airaapi_secret_store, URL
            )
            is None
        )


def test_key_rotation_re_encrypts_the_passwords_before_old_keys_go(cloud):
    org = build_org(cloud)
    candidate_id = org.candidate.member_id
    browser = ready(cloud, org)
    assert (
        browser.call("POST", BASE, {"host": HOST, "email": EMAIL, "password": SECRET})[
            0
        ]
        == 201
    )
    old_key = cloud.secrets.key(org.org_id)
    before = ciphertext(cloud, org.org_id, candidate_id, HOST)
    detail = cloud.lifecycle.rotate_org_keys(org.org_id)
    assert detail["workday_accounts"] == 1 and detail["keys_retired"] == 1
    after = ciphertext(cloud, org.org_id, candidate_id, HOST)
    assert after != before
    with pytest.raises(InvalidToken):
        Fernet(old_key).decrypt(after)
    local = local_module()
    with cloud.workspaces.bind(org.org_id, candidate_id, kind="worker"):
        account = workday_accounts.credentials_for_run(
            local._connect, local._airaapi_secret_store, URL
        )
    assert account is not None and account.password == SECRET
    assert browser.call("GET", BASE)[1]["accounts"][0]["host"] == HOST


def test_the_hosted_settings_page_shows_the_card_only_to_the_accounts_owner():
    card = (
        Path(__file__).resolve().parents[2]
        / "auto_apply_assistant"
        / "views-workday.js"
    ).read_text(encoding="utf-8")
    assert 'Aira.cloud.can("credentials.manage")' in card
    assert "!Aira.cloud ?" not in card
