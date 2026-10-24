"""The restore drill: one tenant comes back from backup and no other tenant is touched."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from cloud_support import PASSWORD, build_org, local_module, seed_workspace, sign_in  # noqa: F401

from aira_cloud.errors import Conflict, ValidationFailed


def fingerprint(app, org_id: str) -> dict:
    """Everything that makes up one tenant: its rows and the checksum of each of its files."""
    manifest = json.loads(
        zipfile.ZipFile(io.BytesIO(app.backup.backup_org(org_id))).read("manifest.json")
    )
    files = {
        name: digest
        for name, digest in manifest["checksums"].items()
        if not name.endswith(".sqlite3")
    }
    return {
        "control": manifest["checksums"]["control.json"],
        "tables": manifest["tables"],
        "files": files,
    }


def candidate_jobs(app, org, who) -> list[str]:
    local = local_module()
    with app.workspaces.bind(org.org_id, who.member_id, kind="system"):
        with local._connect() as connection:
            return [
                row["title"]
                for row in connection.execute("SELECT title FROM jobs ORDER BY title")
            ]


def test_one_tenant_is_restored_and_the_others_are_untouched(cloud):
    a = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    b = build_org(cloud, "Beta Org", plan="team", slug_email="beta.test")
    for org in (a, b):
        for who in ("candidate", "other"):
            seed_workspace(
                cloud,
                org.org_id,
                getattr(org, who).member_id,
                job_title=f"{org.owner.email.split('@')[1]} job",
            )
    cloud.tenancy.update_policy(
        a.owner, {"daily_cap_per_candidate": 7, "cover_letters": False}
    )
    cloud.tenancy.assign_coach(a.owner, a.coach.member_id, a.candidate.member_id, "act")
    cloud.secrets.put(a.org_id, "note", "a secret that must survive")
    document = seed_workspace(
        cloud, a.org_id, a.candidate.member_id, profile=False, resume=False
    )["document_id"]
    b_before = fingerprint(cloud, b.org_id)

    archive = cloud.backup.backup_org(a.org_id)
    manifest = cloud.backup.verify(archive)
    assert manifest["org_id"] == a.org_id and manifest["tables"]["members"] == 7
    names = zipfile.ZipFile(io.BytesIO(archive)).namelist()
    assert (
        "control.json" in names
        and any(n.endswith("workspace.sqlite3") for n in names)
        and any(n.endswith(".bin") for n in names)
    )
    assert not any(n.startswith("files/_exports") for n in names)
    assert (
        b"a secret that must survive" not in archive
        and b"Cam Candidate resume" not in archive
    ), "the backup holds only encrypted data"

    # a disaster in tenant A: a candidate leaves (workspace deleted), the coach is removed, the policy is changed, a file is lost
    cloud.tenancy.remove_member(a.owner, a.other.member_id)
    cloud.tenancy.remove_member(a.owner, a.coach.member_id)
    cloud.tenancy.update_policy(a.owner, {"daily_cap_per_candidate": 3})
    for blob in cloud.workspaces.candidate_dir(a.org_id, a.candidate.member_id).glob(
        "*.bin"
    ):
        blob.unlink()
    assert len(cloud.tenancy.list_members(a.owner)) == 5

    with pytest.raises(Conflict) as raised:
        cloud.backup.restore_org(archive)
    assert raised.value.code == "exists"
    result = cloud.backup.restore_org(archive, replace=True)
    assert result["org_id"] == a.org_id and result["audit"]["ok"]

    # tenant A is back as it was when the backup was taken
    members = cloud.tenancy.list_members(a.owner)
    assert len(members) == 7 and {m["email"] for m in members} >= {
        a.coach.email,
        a.other.email,
    }
    assert (
        cloud.tenancy.policy(a.org_id)["daily_cap_per_candidate"] == 7
        and cloud.tenancy.policy(a.org_id)["cover_letters"] is False
    )
    assert len(cloud.tenancy.list_assignments(a.owner)) == 1
    assert candidate_jobs(cloud, a, a.other) == ["alpha.test job"], (
        "the departed candidate's workspace database is back"
    )
    assert cloud.secrets.get(a.org_id, "note") == "a secret that must survive", (
        "the organization's keys came back with it"
    )
    local = local_module()
    with cloud.workspaces.bind(a.org_id, a.candidate.member_id, kind="system"):
        with local._connect() as connection:
            _, content = local._document_store().read(
                connection, owner_id=a.candidate.member_id, document_id=document
            )
    assert content.startswith(b"Cam Candidate resume.")
    assert cloud.billing.subscription(a.org_id)["status"] == "active"
    assert cloud.audit.verify(a.org_id)["ok"]
    # sessions are not backed up: everyone signs in again, and it works
    assert cloud.identity.login(a.owner.email, PASSWORD)["token"]
    # tenant B is exactly as it was
    assert fingerprint(cloud, b.org_id) == b_before
    assert candidate_jobs(cloud, b, b.candidate) == ["beta.test job"]
    assert [r["action"] for r in cloud.platform_audit.list(org_id=a.org_id)][
        0
    ] == "tenant.restore"


def test_a_deleted_tenant_can_be_restored_and_nothing_else_changes(cloud):
    a = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    b = build_org(cloud, "Beta Org", plan="team", slug_email="beta.test")
    seed_workspace(cloud, a.org_id, a.candidate.member_id, job_title="Cloud Architect")
    b_before = fingerprint(cloud, b.org_id)
    archive = cloud.backup.backup_org(a.org_id)
    cloud.lifecycle.delete_org(a.org_id, reason="accident", trigger_type="scheduler")
    assert (
        cloud.billing.subscription(a.org_id)["status"] == "deleted"
        and not cloud.workspaces.org_dir(a.org_id).exists()
    )
    cloud.backup.restore_org(archive, replace=True)
    assert cloud.billing.subscription(a.org_id)["status"] == "active"
    assert candidate_jobs(cloud, a, a.candidate) == ["Cloud Architect"]
    assert cloud.identity.login(a.owner.email, PASSWORD, org=a.org_id)["token"]
    assert fingerprint(cloud, b.org_id) == b_before


def test_a_tampered_or_foreign_archive_is_refused_before_anything_changes(cloud):
    a = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    seed_workspace(cloud, a.org_id, a.candidate.member_id, job_title="Cloud Architect")
    archive = cloud.backup.backup_org(a.org_id)
    rewritten = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(archive)) as source,
        zipfile.ZipFile(rewritten, "w") as target,
    ):
        for item in source.namelist():
            data = source.read(item)
            if item == "control.json":
                data = data.replace(b"Alpha Org", b"Evil Org!")
            target.writestr(item, data)
    members_before = len(cloud.tenancy.list_members(a.owner))
    with pytest.raises(ValidationFailed) as raised:
        cloud.backup.restore_org(rewritten.getvalue(), replace=True)
    assert raised.value.code == "bad_backup" and "altered" in raised.value.message
    with pytest.raises(ValidationFailed):
        cloud.backup.verify(b"not a zip")
    assert len(cloud.tenancy.list_members(a.owner)) == members_before


def test_the_command_line_writes_and_restores_a_backup(cloud, tmp_path, monkeypatch):
    from aira_cloud.__main__ import main

    a = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    seed_workspace(cloud, a.org_id, a.candidate.member_id, job_title="Cloud Architect")
    monkeypatch.setenv("AIRA_CLOUD_DATA", str(cloud.config.data_root))
    out = tmp_path / "alpha.zip"
    assert (
        main(["backup-org", "--org", a.org_id, "--out", str(out)]) == 0
        and out.stat().st_size > 1000
    )
    assert main(["restore-org", "--file", str(out), "--verify-only"]) == 0
    cloud.tenancy.remove_member(a.owner, a.coach.member_id)
    assert main(["restore-org", "--file", str(out), "--replace"]) == 0
    assert len(cloud.tenancy.list_members(a.owner)) == 7


def _rebuild(archive: bytes, edit, *, fix_checksums: bool = True) -> bytes:
    """An archive with ``edit(name, data)`` applied to its files and the manifest's checksums made to match."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        files = {n: source.read(n) for n in source.namelist() if n != "manifest.json"}
        manifest = json.loads(source.read("manifest.json"))
    files = edit(files)
    if fix_checksums:
        import hashlib

        manifest["checksums"] = {
            n: hashlib.sha256(d).hexdigest() for n, d in files.items()
        }
    with zipfile.ZipFile(out, "w") as target:
        for name, data in files.items():
            target.writestr(name, data)
        target.writestr("manifest.json", json.dumps(manifest))
    return out.getvalue()


def test_a_backup_with_forged_contents_cannot_reach_another_organization(cloud):
    a = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    b = build_org(cloud, "Beta Org", plan="team", slug_email="beta.test")
    seed_workspace(cloud, a.org_id, a.candidate.member_id, job_title="Cloud Architect")
    good = cloud.backup.backup_org(a.org_id)
    assert cloud.backup.verify(good)["signed"] is True
    members_of_b = len(cloud.tenancy.list_members(b.owner))

    def with_foreign_owner(files):
        control = json.loads(files["control.json"])
        row = dict(control["tables"]["members"][0])
        row.update(
            org_id=b.org_id, member_id="f" * 32, email="mallory@evil.test", role="owner"
        )
        control["tables"]["members"].append(row)
        files["control.json"] = json.dumps(control).encode()
        return files

    forged = _rebuild(good, with_foreign_owner)
    # the checksums were fixed up, but the signature covers them: refused
    with pytest.raises(ValidationFailed):
        cloud.backup.restore_org(forged, replace=True)
    # an operator who explicitly trusts an unsigned archive is still protected from rows of another organization
    with pytest.raises(ValidationFailed) as raised:
        cloud.backup.restore_org(forged, replace=True, allow_unsigned=True)
    assert "another organization" in raised.value.message
    assert len(cloud.tenancy.list_members(b.owner)) == members_of_b

    def with_unknown_table(files):
        control = json.loads(files["control.json"])
        control["tables"]["cloud_kv"] = [{"key": "agent_secret", "value": "owned"}]
        files["control.json"] = json.dumps(control).encode()
        return files

    with pytest.raises(ValidationFailed) as raised:
        cloud.backup.restore_org(
            _rebuild(good, with_unknown_table), replace=True, allow_unsigned=True
        )
    assert "may not restore" in raised.value.message

    def with_escaping_path(files):
        files["files/../../escaped.txt"] = b"x"
        return files

    with pytest.raises(ValidationFailed) as raised:
        cloud.backup.restore_org(
            _rebuild(good, with_escaping_path), replace=True, allow_unsigned=True
        )
    assert "unsafe file path" in raised.value.message


def test_an_archive_from_another_deployment_is_refused_unless_trusted(cloud, tmp_path):
    from cloud_support import make_app

    a = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    archive = cloud.backup.backup_org(a.org_id)
    other = make_app(tmp_path / "elsewhere")
    with pytest.raises(ValidationFailed) as raised:
        other.backup.verify(archive)
    assert "not signed by this deployment" in raised.value.message
    assert other.backup.verify(archive, allow_unsigned=True)["signed"] is False
    other.close()
