"""Master keys in a KMS (AWS KMS, Vault transit): round trips, fail-closed outages, rewrapping."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from cloud_support import build_org, make_app, seed_workspace
from kms_stub import (
    ACCESS_KEY,
    KEY_ID,
    REGION,
    SECRET_KEY,
    VAULT_TOKEN,
    AwsKmsStub,
    VaultTransitStub,
)

from aira.hosting import HOOKS
from aira_cloud import crypto
from aira_cloud.config import CloudConfig
from aira_cloud.errors import ValidationFailed
from aira_cloud.kms import (
    AwsKmsKey,
    KeyProviderError,
    KmsSettings,
    VaultTransitKey,
    build_master_key,
)

RESUME_TEXT = b"Cam Candidate resume"


@pytest.fixture
def aws():
    stub = AwsKmsStub(key_ids=(KEY_ID, "alias/other")).start()
    yield stub
    stub.stop()


@pytest.fixture
def vault():
    stub = VaultTransitStub(key_names=("aira-master", "other")).start()
    yield stub
    stub.stop()


def aws_settings(stub: AwsKmsStub, key_id: str = KEY_ID, **extra) -> dict:
    return {
        "kms": "aws",
        "kms_key_id": key_id,
        "kms_endpoint": stub.url,
        "kms_region": REGION,
        "kms_access_key": ACCESS_KEY,
        "kms_secret_key": SECRET_KEY,
        **extra,
    }


def vault_settings(stub: VaultTransitStub, key: str = "aira-master", **extra) -> dict:
    return {
        "kms": "vault",
        "kms_key_id": key,
        "kms_vault_addr": stub.url,
        "kms_vault_token": VAULT_TOKEN,
        **extra,
    }


def key_rows(app) -> list[tuple]:
    with app.db.read() as connection:
        return [
            (r["org_id"], r["version"], bytes(r["wrapped_key"]))
            for r in connection.execute(
                "SELECT org_id, version, wrapped_key FROM org_keys ORDER BY org_id, version"
            )
        ]


def make(tmp_path, **settings):
    HOOKS.reset()
    return make_app(tmp_path, **settings)


# --------------------------------------------------------------------------- AWS KMS


def test_organization_keys_are_wrapped_by_aws_kms_and_documents_round_trip(
    tmp_path, aws
):
    app = make(tmp_path, **aws_settings(aws))
    org = build_org(app)
    seed_workspace(app, org.org_id, org.candidate.member_id)
    app.secrets.put(org.org_id, "probe", "SECRET-SERPAPI-VALUE")

    # the wrapped key is a KMS ciphertext, not something the local master key made
    wrapped = key_rows(app)[0][2]
    assert wrapped.startswith(KEY_ID.encode() + b"|")
    assert "TrentService.GenerateDataKey" in aws.calls
    assert aws.rejected == [], "every request carried a valid SigV4 signature"

    # a restart (empty key cache) reads the key through KMS Decrypt and opens the document
    app.secrets.forget(org.org_id)
    assert app.secrets.get(org.org_id, "probe") == "SECRET-SERPAPI-VALUE"
    assert "TrentService.Decrypt" in aws.calls
    done = app.lifecycle.export_candidate(org.candidate, org.candidate.member_id)
    _row, blob = app.lifecycle.export_for(org.candidate, done["export_id"])
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert any(name.startswith("documents/") for name in names)

    # the KMS only ever handled data keys: 32 raw bytes (or a 44-byte Fernet key), never content
    assert aws.plaintext_lengths and set(aws.plaintext_lengths) <= {32, 44}
    assert not aws.saw(RESUME_TEXT) and not aws.saw(b"SECRET-SERPAPI-VALUE")
    assert not (tmp_path / "cloud" / "secrets" / "master.key").exists(), (
        "no local master key is created when a KMS is configured"
    )
    app.close()


def test_a_fresh_process_reads_what_an_earlier_one_wrote(tmp_path, aws):
    first = make(tmp_path, **aws_settings(aws))
    org = build_org(first)
    first.secrets.put(org.org_id, "probe", "kept")
    first.close()
    second = make(tmp_path, **aws_settings(aws))
    assert second.secrets.get(org.org_id, "probe") == "kept"
    second.close()


def test_a_kms_outage_fails_closed_without_touching_the_local_key(tmp_path, aws):
    app = make(tmp_path, **aws_settings(aws))
    org = build_org(app)
    app.secrets.put(org.org_id, "probe", "kept")
    app.secrets.forget(org.org_id)
    aws.fail_with = 500
    with pytest.raises(KeyProviderError) as caught:
        app.secrets.get(org.org_id, "probe")
    assert (
        caught.value.status == 503 and caught.value.code == "key_provider_unavailable"
    )
    assert not (tmp_path / "cloud" / "secrets" / "master.key").exists()
    # and it recovers when the service does
    aws.fail_with = None
    assert app.secrets.get(org.org_id, "probe") == "kept"
    # a service that is not there at all is the same answer
    app.secrets.forget(org.org_id)
    aws.stop()
    with pytest.raises(KeyProviderError):
        app.secrets.get(org.org_id, "probe")
    app.close()


def test_an_outage_reaches_the_caller_as_a_503_not_a_500(tmp_path, aws):
    from cloud_support import sign_in

    app = make(tmp_path, **aws_settings(aws))
    org = build_org(app)
    seed_workspace(app, org.org_id, org.candidate.member_id)
    owner = sign_in(
        app, org.owner
    )  # sets up MFA, so the organization already has a key
    app.secrets.forget(org.org_id)
    aws.fail_with = 503
    # starting MFA setup stores a secret, which needs the organization's key
    status, body = owner.call("POST", "/api/auth/mfa/begin", {})
    assert status == 503 and body.get("code") == "key_provider_unavailable", body
    aws.fail_with = None
    assert owner.call("POST", "/api/auth/mfa/begin", {})[0] == 200
    app.close()


def test_a_wrong_key_id_is_an_error_both_ways(tmp_path, aws):
    app = make(tmp_path, **aws_settings(aws))
    org = build_org(app)
    app.secrets.put(org.org_id, "probe", "x")  # creates the organization's key
    app.close()
    # data wrapped under one KMS key cannot be opened through another
    other = make(tmp_path, **aws_settings(aws, key_id="alias/other"))
    with pytest.raises(KeyProviderError):
        other.secrets.key(org.org_id)
    other.close()
    # an unknown key id fails at wrap time
    provider = build_master_key(
        KmsSettings.from_config(CloudConfig(**aws_settings(aws, key_id="alias/none"))),
        tmp_path,
    )
    with pytest.raises(KeyProviderError):
        provider.wrap(b"x" * 44)


def test_kms_requests_are_signed_per_the_specification(aws):
    provider = AwsKmsKey(
        key_id=KEY_ID,
        region=REGION,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        endpoint=aws.url,
    )
    assert provider.unwrap(provider.wrap(b"k" * 44)) == b"k" * 44
    bad = AwsKmsKey(
        key_id=KEY_ID,
        region=REGION,
        access_key=ACCESS_KEY,
        secret_key="not-the-secret",
        endpoint=aws.url,
    )
    with pytest.raises(KeyProviderError):
        bad.wrap(b"k" * 44)
    assert aws.rejected, "the stub refused the badly signed request"


def test_unwrapped_keys_are_cached_in_memory_only_for_a_short_time(
    tmp_path, aws, monkeypatch
):
    app = make(tmp_path, **aws_settings(aws, kms_cache_seconds=60))
    org = build_org(app)
    app.secrets.forget(org.org_id)
    clock = {"now": 1000.0}
    monkeypatch.setattr(crypto.time, "monotonic", lambda: clock["now"])
    app.secrets.key(org.org_id)
    before = aws.calls.count("TrentService.Decrypt")
    app.secrets.key(org.org_id)
    assert aws.calls.count("TrentService.Decrypt") == before, "served from memory"
    clock["now"] += 61
    app.secrets.key(org.org_id)
    assert aws.calls.count("TrentService.Decrypt") == before + 1, (
        "asked again after the TTL"
    )
    app.close()


def test_rotation_works_with_a_kms_and_old_data_stays_readable(tmp_path, aws):
    app = make(tmp_path, **aws_settings(aws))
    org = build_org(app)
    app.secrets.put(org.org_id, "probe", "before")
    version = app.secrets.rotate(org.org_id)
    assert version == 2
    app.secrets.forget(org.org_id)
    assert app.secrets.get(org.org_id, "probe") == "before"
    assert len(key_rows(app)) >= 2
    app.close()


def test_misconfiguration_is_refused_at_start_up(tmp_path):
    with pytest.raises(ValueError, match="KEY_ID"):
        build_master_key(
            KmsSettings(kind="aws", access_key="a", secret_key="b"), tmp_path
        )
    with pytest.raises(ValueError, match="ACCESS_KEY"):
        build_master_key(KmsSettings(kind="aws", key_id="k"), tmp_path)
    with pytest.raises(ValueError, match="VAULT_ADDR"):
        build_master_key(KmsSettings(kind="vault", key_id="k"), tmp_path)
    with pytest.raises(ValueError, match="AIRA_CLOUD_KMS must be one of"):
        build_master_key(KmsSettings(kind="gcp"), tmp_path)


# --------------------------------------------------------------------------- Vault transit


def test_vault_transit_wraps_organization_keys(tmp_path, vault):
    app = make(tmp_path, **vault_settings(vault))
    org = build_org(app)
    seed_workspace(app, org.org_id, org.candidate.member_id)
    app.secrets.put(org.org_id, "probe", "SECRET-IN-VAULT-TEST")
    assert key_rows(app)[0][2].startswith(b"vault:v1:")
    assert any("datakey/plaintext" in call for call in vault.calls)
    assert all(h.get("x-vault-token") == VAULT_TOKEN for h in vault.headers_seen)
    app.secrets.forget(org.org_id)
    assert app.secrets.get(org.org_id, "probe") == "SECRET-IN-VAULT-TEST"
    assert set(vault.plaintext_lengths) <= {32, 44}
    assert not vault.saw(RESUME_TEXT) and not vault.saw(b"SECRET-IN-VAULT-TEST")
    app.close()


def test_vault_failures_and_a_wrong_token_fail_closed(tmp_path, vault):
    app = make(tmp_path, **vault_settings(vault))
    org = build_org(app)
    app.secrets.put(org.org_id, "probe", "kept")
    app.secrets.forget(org.org_id)
    vault.fail_with = 502
    with pytest.raises(KeyProviderError):
        app.secrets.get(org.org_id, "probe")
    vault.fail_with = None
    wrong = VaultTransitKey(address=vault.url, token="s.wrong", key_name="aira-master")
    with pytest.raises(KeyProviderError):
        wrong.wrap(b"k" * 44)
    other_key = VaultTransitKey(address=vault.url, token=VAULT_TOKEN, key_name="other")
    wrapped = key_rows(app)[0][2]
    with pytest.raises(KeyProviderError):
        other_key.unwrap(wrapped)
    app.close()


# --------------------------------------------------------------------------- rewrapping


def _local_old(tmp_path):
    return build_master_key(KmsSettings(kind="local"), tmp_path / "cloud")


def test_rewrap_moves_every_key_from_the_local_file_to_aws_and_nothing_breaks(
    tmp_path, aws
):
    local = make(tmp_path)
    org = build_org(local, "Rewrap Org")
    two = build_org(local, "Second Org", slug_email="two.test")
    for item in (org, two):
        seed_workspace(local, item.org_id, item.candidate.member_id)
        local.secrets.put(item.org_id, "probe", f"secret-of-{item.org_id[:6]}")
    local.secrets.rotate(org.org_id)  # an older key version must be carried over too
    before = key_rows(local)
    old = _local_old(tmp_path)
    local.close()

    HOOKS.reset()
    app = make_app(tmp_path, **aws_settings(aws))
    checked = app.secrets.rewrap_all(old, app.secrets.provider, verify_only=True)
    assert checked == {
        "keys": len(before),
        "organizations": 2,
        "verified": True,
        "applied": False,
    }
    assert key_rows(app) == before, "--verify-only changes nothing"

    done = app.secrets.rewrap_all(old, app.secrets.provider)
    assert done["applied"] is True and done["keys"] == len(before)
    after = key_rows(app)
    assert [row[:2] for row in after] == [row[:2] for row in before]
    assert all(row[2].startswith(KEY_ID.encode() + b"|") for row in after)

    # the data keys did not change, so everything written before is still readable
    app.secrets.forget(org.org_id)
    app.secrets.forget(two.org_id)
    assert app.secrets.get(org.org_id, "probe") == f"secret-of-{org.org_id[:6]}"
    assert app.secrets.get(two.org_id, "probe") == f"secret-of-{two.org_id[:6]}"
    exported = app.lifecycle.export_candidate(org.candidate, org.candidate.member_id)
    _row, blob = app.lifecycle.export_for(org.candidate, exported["export_id"])
    assert any(
        n.startswith("documents/") for n in zipfile.ZipFile(io.BytesIO(blob)).namelist()
    )
    app.close()


def test_rewrap_aws_to_vault(tmp_path, aws, vault):
    first = make(tmp_path, **aws_settings(aws))
    org = build_org(first)
    first.secrets.put(org.org_id, "probe", "across-providers")
    old = first.secrets.provider
    first.close()
    app = make(tmp_path, **vault_settings(vault))
    app.secrets.rewrap_all(old, app.secrets.provider)
    assert all(row[2].startswith(b"vault:v1:") for row in key_rows(app))
    app.secrets.forget(org.org_id)
    assert app.secrets.get(org.org_id, "probe") == "across-providers"
    app.close()


def test_a_failure_midway_changes_nothing(tmp_path, aws):
    local = make(tmp_path)
    for index in range(3):
        made = build_org(local, f"Org {index}", slug_email=f"o{index}.test")
        local.secrets.put(made.org_id, "probe", "x")
    before = key_rows(local)
    assert len(before) >= 3
    old = _local_old(tmp_path)
    local.close()

    app = make(tmp_path, **aws_settings(aws))
    # the KMS answers for two organizations, then goes away
    real = app.secrets.provider.wrap
    seen = {"n": 0}

    def flaky(data: bytes) -> bytes:
        seen["n"] += 1
        if seen["n"] == 3:
            aws.fail_with = 500
        return real(data)

    app.secrets.provider.wrap = flaky  # type: ignore[method-assign]
    with pytest.raises(KeyProviderError):
        app.secrets.rewrap_all(old, app.secrets.provider)
    assert key_rows(app) == before, "no key was rewrapped"
    aws.fail_with = None
    app.secrets.provider.wrap = real  # type: ignore[method-assign]

    # a key that changes underneath the rewrap rolls the whole thing back
    class Meddling:
        """Wraps through the real provider but edits one stored key between phase one and two."""

        def __init__(self, inner, app):
            self.inner, self.app, self.calls = inner, app, 0

        def wrap(self, data: bytes) -> bytes:
            self.calls += 1
            return self.inner.wrap(data)

        def unwrap(self, data: bytes) -> bytes:
            result = self.inner.unwrap(data)
            if self.calls == 3 and not getattr(self, "done", False):
                self.done = True
                with self.app.db.write() as connection:
                    connection.execute(
                        "UPDATE org_keys SET wrapped_key = ? WHERE (org_id, version) = (SELECT org_id, version FROM org_keys ORDER BY org_id DESC, version DESC LIMIT 1)",
                        (b"changed-by-someone-else",),
                    )
            return result

    meddling = Meddling(app.secrets.provider, app)
    snapshot = key_rows(app)
    with pytest.raises(ValidationFailed):
        app.secrets.rewrap_all(old, meddling)
    current = key_rows(app)
    changed = [now for now, was in zip(current, snapshot, strict=True) if now != was]
    assert [row[2] for row in changed] == [b"changed-by-someone-else"], (
        "only the key someone else edited differs: nothing was rewrapped"
    )
    app.close()


def test_the_old_key_must_actually_open_the_keys(tmp_path, aws):
    local = make(tmp_path)
    made = build_org(local)
    local.secrets.put(made.org_id, "probe", "x")
    local.close()
    app = make(tmp_path, **aws_settings(aws))
    wrong_old = build_master_key(
        KmsSettings(kind="local", master_key=crypto.Fernet.generate_key().decode()),
        tmp_path,
    )
    with pytest.raises(Exception):  # noqa: B017 - InvalidToken from the wrong Fernet key
        app.secrets.rewrap_all(wrong_old, app.secrets.provider)
    app.close()


def test_the_rewrap_command_checks_then_applies_and_audits(tmp_path, aws, monkeypatch):
    from aira_cloud.__main__ import main

    local = make(tmp_path)
    org = build_org(local)
    local.secrets.put(org.org_id, "probe", "through-the-cli")
    before = key_rows(local)
    local.close()

    data = str(tmp_path / "cloud")
    for name, value in {
        "AIRA_CLOUD_KMS": "aws",
        "AIRA_CLOUD_KMS_KEY_ID": KEY_ID,
        "AIRA_CLOUD_KMS_ENDPOINT": aws.url,
        "AIRA_CLOUD_KMS_REGION": REGION,
        "AIRA_CLOUD_KMS_ACCESS_KEY": ACCESS_KEY,
        "AIRA_CLOUD_KMS_SECRET_KEY": SECRET_KEY,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AIRA_CLOUD_MASTER_KEY", raising=False)

    HOOKS.reset()
    assert main(["--data", data, "rewrap-keys", "--verify-only"]) == 0
    check = make_app(tmp_path, **aws_settings(aws))
    assert key_rows(check) == before
    check.close()

    HOOKS.reset()
    assert main(["--data", data, "rewrap-keys"]) == 0
    after = make_app(tmp_path, **aws_settings(aws))
    assert key_rows(after) != before
    assert after.secrets.get(org.org_id, "probe") == "through-the-cli"
    with after.db.read() as connection:
        rows = connection.execute(
            "SELECT action, detail FROM platform_audit_logs WHERE action = 'platform.keys_rewrapped'"
        ).fetchall()
    assert len(rows) == 1 and json.loads(rows[0]["detail"])["to"] == "aws"
    after.close()


def test_the_rewrap_command_refuses_to_invent_an_old_key(
    tmp_path, aws, monkeypatch, capsys
):
    from aira_cloud.__main__ import main

    for name, value in {
        "AIRA_CLOUD_KMS": "aws",
        "AIRA_CLOUD_KMS_KEY_ID": KEY_ID,
        "AIRA_CLOUD_KMS_ENDPOINT": aws.url,
        "AIRA_CLOUD_KMS_REGION": REGION,
        "AIRA_CLOUD_KMS_ACCESS_KEY": ACCESS_KEY,
        "AIRA_CLOUD_KMS_SECRET_KEY": SECRET_KEY,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AIRA_CLOUD_MASTER_KEY_OLD", raising=False)
    HOOKS.reset()
    assert main(["--data", str(tmp_path / "empty"), "rewrap-keys"]) == 2
    assert "No old master key" in capsys.readouterr().err
    assert not (tmp_path / "empty" / "secrets" / "master.key").exists()
