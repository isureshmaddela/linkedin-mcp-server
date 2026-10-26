"""Aira's operators moderate Social (one network for the whole platform) from the platform console."""

from __future__ import annotations

from cloud_support import build_org, sign_in
from test_cloud_ui_billing import operator_browser

from aira.suite import kit
from aira.suite.social import db


def test_operators_hide_reported_posts_suspend_authors_and_edit_blocked_words(cloud):
    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    cloud.billing.subscribe_app(acme.owner, "social")
    author = sign_in(cloud, acme.owner)
    assert db.path() == cloud.config.data_root / "social.sqlite3"
    status, me = author.call("POST", "/api/social/me", {"handle": "acmeboss"})
    assert status == 201, me
    status, made = author.call(
        "POST",
        "/api/social/posts",
        {"text": "Buy cheap pills here", "audience": "public"},
    )
    assert status == 201, made
    post_id = made["post"]["id"]
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "social")
    reporter = sign_in(cloud, other.owner)
    assert reporter.call("POST", "/api/social/me", {"handle": "watcher"})[0] == 201
    status, filed = reporter.call(
        "POST",
        "/api/social/reports",
        {"target_type": "post", "target_id": post_id, "reason": "Spam"},
    )
    assert status in (200, 201), filed

    # members never reach the console; support operators can moderate
    assert author.call("GET", "/api/platform/social/reports")[0] in (401, 403)
    support = operator_browser(cloud, "support@aira.test", role="support")
    status, queue = support.call("GET", "/api/platform/social/reports")
    assert status == 200
    [item] = queue["reports"]
    assert item["target_type"] == "post" and "cheap pills" in item["preview"]
    assert item["author"]["handle"] == "acmeboss"
    assert support.call("GET", "/api/platform/social/reports?status=bogus")[0] == 400

    status, done = support.call(
        "POST", f"/api/platform/social/reports/{item['id']}", {"action": "hide"}
    )
    assert status == 200 and done == {"action": "hide"}
    assert reporter.call("GET", f"/api/social/posts/{post_id}")[0] == 404
    assert support.call("GET", "/api/platform/social/reports")[1]["reports"] == []
    resolved = support.call("GET", "/api/platform/social/reports?status=resolved")[1]
    assert [r["action"] for r in resolved["reports"]] == ["hide"]
    assert (
        support.call(
            "POST", f"/api/platform/social/reports/{'0' * 32}", {"action": "hide"}
        )[0]
        == 404
    )

    author_id = item["author"]["id"]
    assert support.call(
        "POST", f"/api/platform/social/accounts/{author_id}/suspend", {}
    )[1] == {"suspended": True}
    assert author.call("POST", "/api/social/posts", {"text": "Hello again"})[0] in (
        403,
        423,
    )
    assert support.call(
        "POST",
        f"/api/platform/social/accounts/{author_id}/suspend",
        {"suspended": False},
    )[1] == {"suspended": False}
    assert author.call("POST", "/api/social/posts", {"text": "Hello again"})[0] == 201
    assert (
        support.call("POST", f"/api/platform/social/accounts/{'0' * 32}/suspend", {})[0]
        == 404
    )

    status, saved = support.call(
        "PUT", "/api/platform/social/blocklist", {"words": ["pills", "  scam "]}
    )
    assert status == 200 and set(saved["blocklist"]) >= {"pills", "scam"}
    status, masked = author.call("POST", "/api/social/posts", {"text": "More pills"})
    assert status == 201 and masked["post"]["text"] == "More *****"
    assert (
        support.call("PUT", "/api/platform/social/blocklist", {"words": "x"})[0] == 400
    )

    actions = [
        row["action"]
        for row in cloud.platform_audit.list(limit=50)
        if row["action"].startswith("social.")
    ]
    assert {
        "social.moderate",
        "social.suspend",
        "social.unsuspend",
        "social.blocklist",
    } <= set(actions)


def test_social_data_follows_the_person_out_of_aira(cloud):
    """Social lives outside every organization's database: exports include it, removal and deletion erase it."""
    import io
    import json
    import zipfile
    from contextlib import closing

    from aira.suite.social import service

    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    cloud.billing.subscribe_app(acme.owner, "social")
    people = {}
    for who in ("owner", "candidate", "coach"):
        browser = sign_in(cloud, getattr(acme, who))
        status, made = browser.call("POST", "/api/social/me", {"handle": f"acme{who}"})
        assert status == 201, (who, made)
        assert (
            browser.call("POST", "/api/social/posts", {"text": f"Hi from {who}"})[0]
            == 201
        )
        people[who] = made["account"]["id"]

    exported = cloud.lifecycle.export_candidate(acme.owner, acme.candidate.member_id)
    with cloud.db.read() as connection:
        path = connection.execute(
            "SELECT path FROM exports WHERE export_id = ?", (exported["export_id"],)
        ).fetchone()[0]
    with zipfile.ZipFile(io.BytesIO(open(path, "rb").read())) as archive:
        social = json.loads(archive.read("social.json"))
    assert social["account"]["handle"] == "acmecandidate"
    assert [p["text"] for p in social["posts"]] == ["Hi from candidate"]
    assert "person_key" not in social["account"]

    def remaining() -> set[str]:
        with closing(db.connect()) as sc:
            return set(service.accounts_for(sc, home=acme.org_id))

    cloud.tenancy.remove_member(acme.owner, acme.coach.member_id)
    assert remaining() == {people["owner"], people["candidate"]}
    with closing(db.connect()) as sc:
        assert (
            sc.execute(
                "SELECT COUNT(*) FROM so_posts WHERE author_id = ?", (people["coach"],)
            ).fetchone()[0]
            == 0
        )
    cloud.lifecycle.delete_org(
        acme.org_id, reason="test", trigger_type="scheduler", force=True
    )
    assert remaining() == set()
