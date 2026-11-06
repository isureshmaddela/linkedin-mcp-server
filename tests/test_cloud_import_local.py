"""Bringing a single-user installation into Monosage Cloud (``python -m monosage_cloud import-local``).

A local account with careers data, a document, and Life data (their own life, a child they look after, a vaccine)
is imported into a member's workspace and organization; the member then sees all of it through the hosted API, and
nobody else does. A second run adds nothing twice; a workspace that has data is only overwritten on request; an
owner without a workspace of their own is asked to start one first (the import never gives that consent).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cloud_support import build_org, seed_workspace, sign_in

from aira.documents import DocumentStore, load_or_create_key
from aira.schema import migrate
from aira.suite import kit, life
from aira.suite.journey import lifebook
from aira.suite.journey import store as journey_store
from monosage_cloud import import_local
from monosage_cloud.__main__ import main as cli
from monosage_cloud.errors import NotFound, ValidationFailed

LOCAL_ID = "0123456789abcdef0123456789abcdef"
RESUME = b"Sam Local resume: ten years of product design. " * 10


def make_local(root: Path, email: str) -> dict[str, str]:
    """A single-user installation as ``auto_apply_assistant`` lays it out: accounts.sqlite3 and one database."""
    (root / "accounts").mkdir(parents=True)
    accounts = sqlite3.connect(root / "accounts.sqlite3")
    accounts.execute(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, "
        "data_path TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    # The saved path is from another computer: the import finds the file under the folder it is given.
    accounts.execute(
        "INSERT INTO users VALUES (?, ?, 'x', ?, 0)",
        (LOCAL_ID, email, f"C:\\elsewhere\\accounts\\{LOCAL_ID}.sqlite3"),
    )
    accounts.commit()
    accounts.close()
    kit.forget_schema()
    connection = sqlite3.connect(root / "accounts" / f"{LOCAL_ID}.sqlite3")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    connection.execute(
        "INSERT INTO settings (key, value) VALUES ('application_full_name', 'Sam Local')"
    )
    connection.execute(
        "INSERT INTO jobs (job_id, title, company, location, job_url, stage, updated_at, source, external_id) VALUES "
        "('job-local-1', 'Product designer', 'Acme', 'Remote', 'https://example.test/jobs/1', 'discovered', "
        "'2026-09-21T00:00:00', 'greenhouse', 'gh-1')"
    )
    document = DocumentStore(
        root / "documents", load_or_create_key(root / "documents.key")
    ).store(
        connection,
        owner_id=LOCAL_ID,
        kind="master_resume",
        filename="resume.txt",
        data=RESUME,
        title="Resume",
        actor="test",
    )
    life.ensure(connection)
    me = life._create(connection, {"name": "Sam", "age_group": "adult"}, owner=True)
    journey_store.ensure(connection)
    own = lifebook.book(connection, me)["life"]
    lifebook.save_life(connection, me, {"birth_date": "1990-05-01"}, own["id"])
    child = lifebook.save_life(
        connection, me, {"name": "Mia", "relation": "child", "birth_date": "2026-01-10"}
    )
    lifebook.save_record(
        connection,
        me,
        child["id"],
        {"kind": "vaccine", "title": "Hepatitis B", "day": "2026-01-11"},
    )
    connection.execute(
        "INSERT INTO airaapi_secrets (name, ciphertext, updated_at) VALUES ('serpapi_api_key', x'00', '2026-09-21')"
    )
    connection.commit()
    connection.close()
    kit.forget_schema()
    return {
        "document_id": document["document_id"],
        "child_id": child["id"],
        "profile_id": me["id"],
    }


@pytest.fixture
def local(tmp_path):
    return tmp_path / "local"


def test_a_local_installation_comes_over_whole_and_stays_private(cloud, local):
    org = build_org(cloud, apps="all")
    made = make_local(local, "cand@acme.test")

    report = import_local.run(cloud, email="cand@acme.test", source=local)
    assert report["member_id"] == org.candidate.member_id
    assert report["documents"] == {"copied": 1, "missing": 0}
    assert report["apps"]["rows_added"] >= 4  # the profile, two lives, the vaccine
    assert report["profile"] == "your local profile is now your Monosage Cloud profile"
    assert any("SerpApi" in item for item in report["left_behind"])

    candidate = sign_in(cloud, org.candidate)
    status, jobs = candidate.call("GET", "/api/jobs")
    assert status == 200
    assert [job["title"] for job in jobs["jobs"]] == ["Product designer"]
    status, body = candidate.call(
        "GET", f"/api/documents/{made['document_id']}/download", raw=True
    )
    assert (
        status == 200 and body == RESUME
    )  # decrypted locally, encrypted again with the organization's key

    status, book = candidate.call("GET", "/api/journey/lives/me")
    assert status == 200
    assert book["life"]["birth_date"] == "1990-05-01"
    status, child = candidate.call("GET", f"/api/journey/lives/{made['child_id']}")
    assert status == 200
    assert child["life"]["name"] == "Mia"
    assert [r["title"] for r in child["records"]["vaccine"]] == ["Hepatitis B"]

    # The keys encrypted with the local key are not carried into the workspace.
    workspace = cloud.workspaces.data_path(org.org_id, org.candidate.member_id)
    with sqlite3.connect(workspace) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM airaapi_secrets").fetchone()[0]
            == 0
        )
        owners = {
            row[0] for row in connection.execute("SELECT owner_id FROM documents")
        }
    assert owners == {org.candidate.member_id}

    # Nobody else in the organization sees the imported life or the documents.
    other = sign_in(cloud, org.other)
    assert other.call("GET", f"/api/journey/lives/{made['child_id']}")[0] == 404
    assert other.call(
        "GET", f"/api/documents/{made['document_id']}/download", raw=True
    )[0] in (403, 404)
    assert cloud.audit.list(org.org_id, limit=20, action="workspace.import_local")


def test_a_workspace_in_use_keeps_its_records_and_nothing_comes_twice(cloud, local):
    org = build_org(cloud, apps="all")
    make_local(local, "cand@acme.test")
    candidate = sign_in(cloud, org.candidate)
    seed_workspace(
        cloud,
        org.org_id,
        org.candidate.member_id,
        document=False,
        job_title="Cloud architect",
    )

    first = import_local.run(cloud, email="cand@acme.test", source=local)
    assert first["workspace"]["mode"] == "added to the workspace"
    titles = sorted(
        job["title"] for job in candidate.call("GET", "/api/jobs")[1]["jobs"]
    )
    assert titles == [
        "Cloud architect",
        "Product designer",
    ]  # theirs is kept, the local one added
    # Their own profile answers stay where both had one.
    status, profile = candidate.call("GET", "/api/profile")
    assert (
        status == 200 and profile["application_profile"]["full_name"] == "Cam Candidate"
    )

    again = import_local.run(cloud, email="cand@acme.test", source=local)
    assert again["workspace"]["rows"] == 0 and again["apps"]["rows_added"] == 0
    assert (
        again["documents"]["copied"] == 1
    )  # the same file, written again under the same id
    assert len(candidate.call("GET", "/api/jobs")[1]["jobs"]) == 2

    # Replacing makes the workspace the local database again.
    replaced = import_local.run(
        cloud, email="cand@acme.test", source=local, replace=True
    )
    assert replaced["workspace"]["mode"] == "copied into the workspace"
    assert [job["title"] for job in candidate.call("GET", "/api/jobs")[1]["jobs"]] == [
        "Product designer"
    ]
    lives = candidate.call("GET", "/api/journey/lives")[1]["lives"]
    assert sorted(item["name"] for item in lives if item["relation"] != "self") == [
        "Mia"
    ]


def test_a_member_who_already_uses_life_keeps_their_profile(cloud, local):
    org = build_org(cloud, apps="all")
    made = make_local(local, "cand@acme.test")
    candidate = sign_in(cloud, org.candidate)
    status, before = candidate.call(
        "GET", "/api/journey/lives/me"
    )  # makes their hosted profile and life
    assert status == 200 and not before["life"]["birth_date"]

    report = import_local.run(cloud, email="cand@acme.test", source=local)
    assert report["profile"] == "joined to your Monosage Cloud profile"
    status, child = candidate.call("GET", f"/api/journey/lives/{made['child_id']}")
    assert (
        status == 200 and child["life"]["name"] == "Mia"
    )  # the child now belongs to their hosted profile


def test_an_owner_starts_their_own_workspace_first(cloud, local):
    org = build_org(cloud, apps="all")
    make_local(local, "owner@acme.test")
    with pytest.raises(ValidationFailed, match="My workspace"):
        import_local.run(cloud, email="owner@acme.test", source=local)
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/workspace/own", {})[0] == 201
    report = import_local.run(cloud, email="owner@acme.test", source=local)
    assert report["member_id"] == org.owner.member_id
    status, jobs = owner.call(
        "GET", "/api/jobs", headers={"X-Aira-Candidate": org.owner.member_id}
    )
    assert status == 200 and len(jobs["jobs"]) == 1


def test_unknown_accounts_are_named(cloud, local):
    build_org(cloud, apps="all")
    make_local(local, "cand@acme.test")
    with pytest.raises(NotFound, match="No local account"):
        import_local.run(cloud, email="nobody@acme.test", source=local)
    with pytest.raises(NotFound, match="No active Monosage Cloud member"):
        import_local.run(cloud, email="cand@acme.test", source=local, org_id="0" * 32)
    with pytest.raises(NotFound, match="No Monosage installation"):
        import_local.run(cloud, email="cand@acme.test", source=local / "missing")


def test_the_command_line_prints_the_report(cloud, local, capsys):
    build_org(cloud, apps="all")
    make_local(local, "cand@acme.test")
    code = cli(
        [
            "--data",
            str(cloud.config.data_root),
            "import-local",
            "--email",
            "cand@acme.test",
            "--source",
            str(local),
        ]
    )
    assert code == 0
    assert '"copied": 1' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("stored", "name"),
    [
        (f"D:\\linkedin\\data\\accounts\\{LOCAL_ID}.sqlite3", f"{LOCAL_ID}.sqlite3"),
        (f"D:/linkedin/data/accounts/{LOCAL_ID}.sqlite3", f"{LOCAL_ID}.sqlite3"),
        (
            "C:\\Users\\sam\\.linkedin-mcp\\auto_apply\\applications.sqlite3",
            "applications.sqlite3",
        ),
        (
            f"/home/sam/.linkedin-mcp/auto_apply/accounts/{LOCAL_ID}.sqlite3",
            f"{LOCAL_ID}.sqlite3",
        ),
        ("applications.sqlite3", "applications.sqlite3"),
    ],
)
def test_a_saved_path_from_any_system_names_its_file(stored, name):
    """The import runs where the installation was not made: a Windows path read on Linux, and the other way round."""
    assert import_local.stored_file_name(stored) == name


@pytest.mark.parametrize(
    "stored",
    [
        "D:\\linkedin\\data\\applications.sqlite3",
        "D:/linkedin/data/applications.sqlite3",
        "/srv/monosage/applications.sqlite3",
    ],
)
def test_the_first_account_is_found_whichever_system_saved_its_path(local, stored):
    make_local(local, "cand@acme.test")
    accounts = sqlite3.connect(local / "accounts.sqlite3")
    accounts.execute("UPDATE users SET data_path = ?", (stored,))
    accounts.commit()
    accounts.close()
    (local / "applications.sqlite3").write_bytes(
        (local / "accounts" / f"{LOCAL_ID}.sqlite3").read_bytes()
    )
    local_id, path = import_local._local_account(local, "cand@acme.test")
    assert (local_id, path) == (LOCAL_ID, local / "applications.sqlite3")
