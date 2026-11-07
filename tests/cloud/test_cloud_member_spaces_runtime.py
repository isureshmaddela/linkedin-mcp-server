"""Member spaces outside a signed-in request: public links, background work, keys, removal, exports and mail.

With ``apps_space = "personal"`` every member has their own business apps in ``tenants/<org>/<member>/apps.sqlite3``
(monosage_cloud/spaces.py). Their customers reach that database through public links without signing in, the
scheduler ticks it, key rotation re-encrypts its secrets, removal and exports follow the data policy, and its
customer email can go out through the organization's mail server.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
from aira.airaapi.store import now_utc
from aira.leads import mail as transport
from aira.suite import kit, public
from monosage_cloud import dialect
from monosage_cloud.db import table_columns
from monosage_cloud.schema import MIGRATIONS
from monosage_cloud.worker import HANDLERS

COMPANY = {"X-Aira-Space": "company"}
NOW = now_utc()


@public.page("studio", "space_test_page")
def _space_page(connection, link):
    return {"business": kit.business(connection)["name"], "ref": link["ref_id"]}


@public.action("studio", "space_test_page", "buy")
def _space_buy(connection, link, payload, visitor):
    kit.record_revenue(
        connection,
        "studio",
        source_type="test",
        source_id=str(payload.get("name") or "x"),
        amount_cents=2500,
        label="Test",
    )
    connection.commit()
    return 201, {"ok": True}


@pytest.fixture
def org(cloud):
    kit.forget_schema()
    return build_org(cloud, apps=["studio"], apps_space="personal")


def _link(cloud, org_id, member_id="", ref="r1"):
    binding = (
        cloud.workspaces.bind_member_apps(org_id, member_id)
        if member_id
        else cloud.workspaces.bind_org_api(org_id)
    )
    with binding:
        with local._connect() as connection:
            return public.create_link(
                connection, "studio", "space_test_page", ref, title="Demo"
            )


def _index_row(cloud, token):
    with cloud.db.read() as connection:
        return connection.execute(
            "SELECT org_id, member_id FROM public_links WHERE token_hash = ?",
            (public.hash_token(token),),
        ).fetchone()


def _set_policy(cloud, owner, **values):
    status, body = sign_in(cloud, owner).call("PATCH", "/api/org/policy", values)
    assert status == 200, body
    cloud.tenancy.invalidate_policy(owner.org_id)


def _files(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _download(cloud, org_id, export_id) -> dict[str, bytes]:
    with cloud.db.read() as connection:
        row = connection.execute(
            "SELECT path FROM exports WHERE export_id = ? AND org_id = ?",
            (export_id, org_id),
        ).fetchone()
    with open(row["path"], "rb") as handle:
        return _files(handle.read())


# ---------------------------------------------------------------------------- the migration


def test_public_links_record_the_member_on_sqlite_and_the_postgres_ddl_is_plain(cloud):
    version, name, script = next(m for m in MIGRATIONS if m[1] == "public_link_members")
    assert version == 9 and "member_id" in script
    with cloud.db.read() as connection:
        assert "member_id" in table_columns(connection, "public_links")
    from monosage_cloud.db import _statements

    statements = [dialect.ddl(s) for s in _statements(script)]
    assert statements[0].startswith(
        "ALTER TABLE public_links ADD COLUMN member_id TEXT NOT NULL DEFAULT ''"
    )
    assert statements[1].startswith("CREATE INDEX public_links_member")


# ---------------------------------------------------------------------------- public links


def test_a_link_made_in_a_members_space_runs_in_their_database(cloud, org):
    member = sign_in(cloud, org.candidate)
    status, saved = member.call("POST", "/api/suite/business", {"name": "Cam Crafts"})
    assert status == 200, saved
    owner = sign_in(cloud, org.owner)
    status, _ = owner.call(
        "POST", "/api/suite/business", {"name": "Acme Agency"}, headers=COMPANY
    )
    assert status == 200

    mine = _link(cloud, org.org_id, org.candidate.member_id)
    company = _link(cloud, org.org_id, ref="r2")
    assert dict(_index_row(cloud, mine["token"])) == {
        "org_id": org.org_id,
        "member_id": org.candidate.member_id,
    }
    assert _index_row(cloud, company["token"])["member_id"] == ""

    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{mine['token']}")
    assert status == 200 and page["business"]["name"] == "Cam Crafts", page
    status, page = visitor.call("GET", f"/api/public/{company['token']}")
    assert status == 200 and page["business"]["name"] == "Acme Agency"
    status, _ = visitor.call(
        "POST", f"/api/public/{mine['token']}/buy", {"name": "Ana"}
    )
    assert status == 201
    # the sale is the member's, not the organization's
    assert member.call("GET", "/api/suite/home")[1]["revenue"]["total_cents"] == 2500
    company_home = owner.call("GET", "/api/suite/home", headers=COMPANY)[1]
    assert company_home["revenue"]["total_cents"] == 0
    status, html = visitor.call("GET", mine["path"], raw=True)
    assert status == 200

    # in shared mode nobody can open a member's space: its links rest (and the organization's keep working)
    _set_policy(cloud, org.owner, apps_space="shared")
    assert visitor.call("GET", f"/api/public/{mine['token']}")[0] == 404
    assert visitor.call("GET", f"/api/public/{company['token']}")[0] == 200
    _set_policy(cloud, org.owner, apps_space="personal")
    assert visitor.call("GET", f"/api/public/{mine['token']}")[0] == 200


def test_a_removed_members_links_stop_and_their_space_goes_to_them(cloud, org):
    member = sign_in(cloud, org.candidate)
    member.call("POST", "/api/suite/business", {"name": "Cam Crafts"})
    mine = _link(cloud, org.org_id, org.candidate.member_id)
    path = cloud.workspaces.member_apps_path(org.org_id, org.candidate.member_id)
    assert path.exists()
    result = cloud.tenancy.remove_member(org.owner, org.candidate.member_id)
    assert result["data_policy"] == "candidate_keeps"
    assert Browser(cloud).call("GET", f"/api/public/{mine['token']}")[0] == 404
    assert not path.exists() and _index_row(cloud, mine["token"]) is None
    # the departure hand-over carries their business
    files = _download(cloud, org.org_id, result["export_id"])
    space = json.loads(files["business_apps.json"])
    assert space["member_id"] == org.candidate.member_id
    assert any("Cam Crafts" in json.dumps(rows) for rows in space["tables"].values())


def test_staff_leaving_take_their_space_unless_the_organization_keeps_member_data(
    cloud, org
):
    admin = sign_in(cloud, org.admin)
    assert admin.call("POST", "/api/suite/business", {"name": "Ada's Studio"})[0] == 200
    coach = sign_in(cloud, org.coach)
    assert coach.call("POST", "/api/suite/business", {"name": "Coach Co"})[0] == 200
    admin_path = cloud.workspaces.member_apps_path(org.org_id, org.admin.member_id)
    coach_path = cloud.workspaces.member_apps_path(org.org_id, org.coach.member_id)
    coach_link = _link(cloud, org.org_id, org.coach.member_id)

    # candidate_keeps (the default): the admin gets a download of their business and it is deleted
    result = cloud.tenancy.remove_member(org.owner, org.admin.member_id)
    assert result["business_apps"] == "deleted" and not admin_path.exists()
    files = _download(cloud, org.org_id, result["export_id"])
    assert set(files) == {"business_apps.json"}
    assert cloud.mail.latest("admin@acme.test", "data_export_ready") is not None

    # an active member's space is never in the organization's export
    export = cloud.lifecycle.export_organization(org.owner)
    names = set(_download(cloud, org.org_id, export["export_id"]))
    assert not any(name.startswith("member_spaces/") for name in names)

    # organization_keeps: the space stays (and so is in the organization's export), its links stop
    _set_policy(cloud, org.owner, candidate_data_policy="organization_keeps")
    result = cloud.tenancy.remove_member(org.owner, org.coach.member_id)
    assert result["business_apps"] == "kept" and coach_path.exists()
    assert Browser(cloud).call("GET", f"/api/public/{coach_link['token']}")[0] == 404
    export = cloud.lifecycle.export_organization(org.owner)
    files = _download(cloud, org.org_id, export["export_id"])
    kept = json.loads(files[f"member_spaces/{org.coach.member_id}.json"])
    assert "Coach Co" in json.dumps(kept["tables"])


def test_ending_only_the_careers_workspace_keeps_the_members_business(cloud, org):
    member_id = org.candidate.member_id
    sign_in(cloud, org.candidate).call("POST", "/api/suite/business", {"name": "Cam"})
    space = cloud.workspaces.member_apps_path(org.org_id, member_id)
    careers = cloud.workspaces.data_path(org.org_id, member_id)
    cloud.workspaces.delete(org.org_id, member_id, keep_member_apps=True)
    assert space.exists() and not careers.exists()
    cloud.workspaces.delete_member_apps(org.org_id, member_id)
    assert not space.exists() and not space.parent.exists()


# ---------------------------------------------------------------------------- exports


def test_a_members_own_export_carries_their_space_and_staff_exports_do_not(cloud, org):
    member = sign_in(cloud, org.candidate)
    member.call("POST", "/api/suite/business", {"name": "Cam Crafts"})
    with cloud.workspaces.bind_member_apps(org.org_id, org.candidate.member_id):
        with local._connect() as connection:
            local._airaapi_secret_store().put(
                connection, kit.SMTP_PASSWORD_SECRET, "hunter2", NOW
            )
            connection.commit()
    status, own = member.call("POST", "/api/org/exports", {})
    assert status == 201, own
    files = _download(cloud, org.org_id, own["export_id"])
    space = json.loads(files["business_apps.json"])
    assert "Cam Crafts" in json.dumps(space["tables"])
    assert (
        "airaapi_secrets" not in space["tables"]
        and b"hunter2" not in files["business_apps.json"]
    )
    # an administrator exporting the candidate gets the careers workspace, not the private business
    status, by_admin = sign_in(cloud, org.admin).call(
        "POST", "/api/org/exports", {"candidate_id": org.candidate.member_id}
    )
    assert status == 201, by_admin
    assert "business_apps.json" not in _download(
        cloud, org.org_id, by_admin["export_id"]
    )


# ---------------------------------------------------------------------------- background work and mail


def _org_mail(cloud, org_id):
    with cloud.workspaces.bind_org_api(org_id):
        with local._connect() as connection:
            kit.save_email_settings(
                connection,
                {
                    "from_name": "Acme Careers",
                    "from_email": "hello@acme.test",
                    "smtp_host": "smtp.acme.test",
                },
            )
            local._airaapi_secret_store().put(
                connection, kit.SMTP_PASSWORD_SECRET, "org-pw", NOW
            )
            connection.commit()


def _queue(cloud, org_id, member_id, to):
    with cloud.workspaces.bind_member_apps(org_id, member_id):
        with local._connect() as connection:
            message = kit.queue_email(
                connection,
                "bookings",
                "booking_reminder",
                to=to,
                subject="See you tomorrow",
                body="Your booking is tomorrow.",
            )
            kit.set_outbox_status(connection, [message["id"]], "approved")


def test_member_spaces_are_ticked_and_send_through_the_organizations_mail(
    cloud, org, monkeypatch
):
    member_id = org.candidate.member_id
    sign_in(cloud, org.candidate).call("POST", "/api/suite/business", {"name": "Cam"})
    sign_in(cloud, org.owner).call(
        "POST", "/api/suite/business", {"name": "Acme"}, headers=COMPANY
    )
    _org_mail(cloud, org.org_id)
    _queue(cloud, org.org_id, member_id, "client@example.com")
    sent = []
    monkeypatch.setattr(
        transport,
        "smtp_send",
        lambda settings, password, message: sent.append(
            (password, message["From"], message["Reply-To"], message["To"])
        ),
    )

    assert (
        cloud.scheduler.enqueue_due()["suite.tick"] == 2
    )  # the organization and the member
    assert cloud.scheduler.enqueue_due()["suite.tick"] == 0  # once per window each
    with cloud.db.read() as connection:
        jobs = [
            dict(r)
            for r in connection.execute(
                "SELECT job_id, org_id, payload, dedupe_key FROM queue_jobs WHERE kind = 'suite.tick'"
            )
        ]
    member_jobs = [j for j in jobs if member_id in j["dedupe_key"]]
    assert len(member_jobs) == 1
    job = {**member_jobs[0], "payload": json.loads(member_jobs[0]["payload"])}
    result = HANDLERS["suite.tick"](cloud, job)
    assert result["mail"] == {"sent": 1, "failed": 0}
    password, sender, reply_to, recipient = sent[0]
    assert password == "org-pw" and recipient == "client@example.com"
    assert sender.startswith("Cand via Acme Careers") and "hello@acme.test" in sender
    assert reply_to == org.candidate.email

    # the member's own mail settings win once they are complete
    with cloud.workspaces.bind_member_apps(org.org_id, member_id):
        with local._connect() as connection:
            kit.save_email_settings(
                connection,
                {
                    "from_name": "Cam",
                    "from_email": "cam@cam.test",
                    "smtp_host": "smtp.cam.test",
                },
            )
            local._airaapi_secret_store().put(
                connection, kit.SMTP_PASSWORD_SECRET, "own-pw", NOW
            )
            connection.commit()
    _queue(cloud, org.org_id, member_id, "second@example.com")
    status, body = sign_in(cloud, org.candidate).call(
        "POST", "/api/suite/outbox/send", {}
    )
    assert status == 200 and body["sent"] == 1, body
    password, sender, reply_to, _ = sent[-1]
    assert (
        password == "own-pw" and "cam@cam.test" in sender and reply_to == "cam@cam.test"
    )

    # the organization's own apps never borrow anything: without their settings nothing is lent to them
    with cloud.workspaces.bind_org_api(org.org_id):
        assert kit.lent_mail() is None


def test_the_hub_shows_a_member_the_mail_they_are_lent(cloud, org):
    member = sign_in(cloud, org.candidate)
    member.call("POST", "/api/suite/business", {"name": "Cam"})
    status, view = member.call("GET", "/api/suite/email")
    assert status == 200 and view["ready"] is False and view["lent"] is None
    _org_mail(cloud, org.org_id)
    view = member.call("GET", "/api/suite/email")[1]
    assert view["lent"] == {
        "from_name": "Cand via Acme Careers",
        "from_email": "hello@acme.test",
        "reply_to": org.candidate.email,
    }
    assert member.call("GET", "/api/suite/home")[1]["email_ready"] is True


def test_removed_members_and_shared_mode_are_not_ticked(cloud, org):
    member_id = org.candidate.member_id
    sign_in(cloud, org.candidate).call("POST", "/api/suite/business", {"name": "Cam"})
    _set_policy(cloud, org.owner, apps_space="shared")
    assert cloud.scheduler.enqueue_due()["suite.tick"] == 0
    with pytest.raises(Exception, match="not in use"):
        HANDLERS["suite.tick"](
            cloud,
            {"org_id": org.org_id, "job_id": "j1", "payload": {"member_id": member_id}},
        )
    _set_policy(
        cloud,
        org.owner,
        apps_space="personal",
        candidate_data_policy="organization_keeps",
    )
    cloud.tenancy.remove_member(org.owner, member_id)
    assert cloud.workspaces.member_apps_path(org.org_id, member_id).exists()
    assert cloud.scheduler.enqueue_due()["suite.tick"] == 0


# ---------------------------------------------------------------------------- keys and usage


def test_key_rotation_re_encrypts_secrets_in_every_member_space(cloud, org):
    member_id = org.candidate.member_id
    sign_in(cloud, org.candidate).call("POST", "/api/suite/business", {"name": "Cam"})
    with cloud.workspaces.bind_member_apps(org.org_id, member_id):
        with local._connect() as connection:
            local._airaapi_secret_store().put(
                connection, "payments_key", "sk_member", NOW
            )
            connection.commit()
    path = cloud.workspaces.member_apps_path(org.org_id, member_id)

    def ciphertext():
        with sqlite3.connect(path) as raw:
            return raw.execute(
                "SELECT ciphertext FROM airaapi_secrets WHERE name = 'payments_key'"
            ).fetchone()[0]

    before = ciphertext()
    detail = cloud.lifecycle.rotate_org_keys(org.org_id)
    assert detail["api_secrets"] >= 1 and ciphertext() != before
    with cloud.workspaces.bind_member_apps(org.org_id, member_id):
        with local._connect() as connection:
            assert (
                local._airaapi_secret_store().get(connection, "payments_key")
                == "sk_member"
            )


def test_leads_in_member_spaces_count_toward_the_organizations_limit(cloud, org):
    member_id = org.candidate.member_id
    with cloud.workspaces.bind_member_apps(org.org_id, member_id):
        with local._connect() as connection:
            connection.executemany(
                "INSERT INTO leads (id, list_id, created_at, updated_at) VALUES (?, 'l1', '', '')",
                [("a",), ("b",)],
            )
            connection.commit()
    assert cloud.workspaces.org_leads(org.org_id) == 2
