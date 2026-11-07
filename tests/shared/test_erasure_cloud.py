"""The hosted build's erasure (monosage_cloud/erasure_hooks.py): a removed member, a member deleting their own
account, and the organization's legal hold."""

from __future__ import annotations

import json
import uuid
from contextlib import closing

from cloud_support import PASSWORD, audit_actions, build_org, sign_in

from aira.notify import store as notify_store
from aira.suite import kit
from aira.suite.care import store as care_store
from aira.suite.social import db as social_db
from aira.suite.videos import db as videos_db
from monosage_cloud.db import dumps


def _join_life_and_social(cloud, org, who):
    """The member opens the apps: their life profile (in the organization's database) and a Social profile."""
    browser = sign_in(cloud, who)
    status, me = browser.call("GET", "/api/life/me")
    assert status == 200, me
    handle = f"m{who.member_id[:12]}"
    status, made = browser.call("POST", "/api/social/me", {"handle": handle})
    assert status == 201, made
    profile_id = me["profile"]["id"]
    with cloud.workspaces.org_api_connection(org.org_id) as connection:
        care_store.ensure(connection)
        notify_store.ensure(connection)
        connection.execute(
            "INSERT INTO cr_medicines (id, owner_id, name, created_at, updated_at) VALUES (?, ?, 'Metformin', '', '')",
            (uuid.uuid4().hex, profile_id),
        )
        connection.execute(
            "INSERT INTO nt_push_subs (id, person, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', '')",
            (uuid.uuid4().hex, f"m:{who.member_id}", f"https://push.example/{handle}"),
        )
        connection.commit()
    with closing(videos_db.connect()) as vc:
        vc.execute(
            "INSERT INTO vi_channels (id, person_key, home, name, payout_details, created_at, updated_at) VALUES (?, ?, ?, ?, 'upi:someone', '', '')",
            (uuid.uuid4().hex, f"m:{who.member_id}", org.org_id, handle),
        )
        vc.commit()
    return browser, profile_id


def _org_rows(cloud, org, sql, params=()):
    with cloud.workspaces.org_api_connection(org.org_id) as connection:
        return [dict(r) for r in connection.execute(sql, params).fetchall()]


def _social_account(member_id):
    with closing(social_db.connect()) as sc:
        return sc.execute(
            "SELECT id FROM so_accounts WHERE person_key = ?", (f"m:{member_id}",)
        ).fetchone()


def _channel(member_id):
    with closing(videos_db.connect()) as vc:
        return vc.execute(
            "SELECT id FROM vi_channels WHERE person_key = ?", (f"m:{member_id}",)
        ).fetchone()


def _billing_payload(cloud, org, email):
    event_id = uuid.uuid4().hex
    with cloud.db.write() as connection:
        connection.execute(
            "INSERT INTO billing_events (event_id, provider, type, org_id, payload, received_at) VALUES (?, 'standin', 'invoice.paid', ?, ?, ?)",
            (
                event_id,
                org.org_id,
                dumps({"customer_email": email.upper(), "amount": 900}),
                cloud.clock.now(),
            ),
        )
    return event_id


def _fresh_org(cloud):
    kit.forget_schema()
    social_db.forget()
    videos_db.forget()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "social")
    return org


def test_removing_a_member_erases_their_life_social_videos_and_redacts_platform_records(
    cloud,
):
    org = _fresh_org(cloud)
    email = org.candidate.email
    _, profile_id = _join_life_and_social(cloud, org, org.candidate)
    _, other_profile = _join_life_and_social(cloud, org, org.other)
    event_id = _billing_payload(cloud, org, email)
    with cloud.db.read() as connection:
        mails_before = connection.execute(
            "SELECT COUNT(*) FROM mail_outbox"
        ).fetchone()[0]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM mail_outbox WHERE recipient = ?", (email,)
            ).fetchone()[0]
            >= 1
        )
    assert _social_account(org.candidate.member_id) and _channel(
        org.candidate.member_id
    )

    cloud.tenancy.remove_member(org.owner, org.candidate.member_id)

    # their life apps in the organization's database
    assert (
        _org_rows(
            cloud,
            org,
            "SELECT id FROM life_profiles WHERE member_ref = ?",
            (org.candidate.member_id,),
        )
        == []
    )
    assert (
        _org_rows(
            cloud, org, "SELECT id FROM cr_medicines WHERE owner_id = ?", (profile_id,)
        )
        == []
    )
    assert (
        _org_rows(
            cloud,
            org,
            "SELECT id FROM nt_push_subs WHERE person = ?",
            (f"m:{org.candidate.member_id}",),
        )
        == []
    )
    # the other member's are untouched
    assert (
        len(
            _org_rows(
                cloud,
                org,
                "SELECT id FROM cr_medicines WHERE owner_id = ?",
                (other_profile,),
            )
        )
        == 1
    )
    assert _social_account(org.other.member_id) and _channel(org.other.member_id)
    # the shared Social and Videos databases
    assert _social_account(org.candidate.member_id) is None
    assert _channel(org.candidate.member_id) is None
    # the platform's mail and billing payloads keep their rows without the email
    with cloud.db.read() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM mail_outbox").fetchone()[0]
            >= mails_before
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM mail_outbox WHERE kind != 'data_export_ready' AND (LOWER(recipient) = ? OR LOWER(body) LIKE ?)",
                (email, f"%{email}%"),
            ).fetchone()[0]
            == 0
        )
        # the link to their own data, sent as they leave, still reaches them
        assert cloud.mail.latest(email, "data_export_ready")
        payload = connection.execute(
            "SELECT payload FROM billing_events WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
    assert email not in payload.lower() and json.loads(payload)["amount"] == 900
    # the receipt: counts only
    assert "member.erasure" in audit_actions(cloud, org.org_id)
    receipt = next(
        r
        for r in cloud.audit.list(org.org_id, limit=50)
        if r["action"] == "member.erasure"
    )
    detail = (
        receipt["detail"]
        if isinstance(receipt["detail"], dict)
        else json.loads(receipt["detail"])
    )
    assert (
        detail["status"] == "done"
        and detail["erased"]["care"] == 1
        and detail["erased"]["social"] > 0
    )
    assert (
        detail["redacted"]["billing_events"] == 1
        and detail["redacted"]["mail_outbox"] >= 1
    )
    text = json.dumps(detail)
    assert (
        email not in text
        and profile_id not in text
        and org.candidate.member_id not in text
    )


def test_a_member_deletes_their_own_account(cloud):
    org = _fresh_org(cloud)
    browser, profile_id = _join_life_and_social(cloud, org, org.other)
    status, refused = browser.call(
        "POST", "/api/me/delete-account", {"password": "not my password"}
    )
    assert status == 401, refused
    assert _social_account(org.other.member_id)

    status, done = browser.call(
        "POST", "/api/me/delete-account", {"password": PASSWORD}
    )

    assert status == 200 and done == {"deleted": True}
    assert (
        _social_account(org.other.member_id) is None
        and _channel(org.other.member_id) is None
    )
    assert (
        _org_rows(
            cloud, org, "SELECT id FROM cr_medicines WHERE owner_id = ?", (profile_id,)
        )
        == []
    )
    with cloud.db.read() as connection:
        member = connection.execute(
            "SELECT email, name, status FROM members WHERE member_id = ?",
            (org.other.member_id,),
        ).fetchone()
    assert (
        member["status"] == "removed"
        and member["email"].endswith("@deleted.invalid")
        and member["name"] == ""
    )
    assert browser.call("GET", "/api/auth/me")[0] == 401  # signed out everywhere
    assert {"member.erasure", "member.erased"} <= set(audit_actions(cloud, org.org_id))
    # the organization's only owner closes the organization instead
    owner = sign_in(cloud, org.owner)
    status, refused = owner.call(
        "POST", "/api/me/delete-account", {"password": PASSWORD}
    )
    assert status == 409, refused


def test_under_a_legal_hold_only_social_and_videos_are_erased(cloud):
    org = _fresh_org(cloud)
    _, profile_id = _join_life_and_social(cloud, org, org.candidate)
    with cloud.db.write() as connection:
        policy = {**cloud.tenancy.policy(org.org_id), "legal_hold": True}
        connection.execute(
            "UPDATE organizations SET policy = ? WHERE org_id = ?",
            (dumps(policy), org.org_id),
        )
    cloud.tenancy.invalidate_policy(org.org_id)

    cloud.tenancy.remove_member(org.owner, org.candidate.member_id)

    assert (
        _social_account(org.candidate.member_id) is None
        and _channel(org.candidate.member_id) is None
    )
    # the organization's records wait for the hold to end
    assert (
        len(
            _org_rows(
                cloud,
                org,
                "SELECT id FROM cr_medicines WHERE owner_id = ?",
                (profile_id,),
            )
        )
        == 1
    )
    receipt = next(
        r
        for r in cloud.audit.list(org.org_id, limit=50)
        if r["action"] == "member.erasure"
    )
    detail = (
        receipt["detail"]
        if isinstance(receipt["detail"], dict)
        else json.loads(receipt["detail"])
    )
    assert detail["organization_legal_hold"] is True and set(detail["erased"]) <= {
        "social",
        "videos",
    }
