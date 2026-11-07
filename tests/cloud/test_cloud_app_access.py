"""Who may open which app, per role and per member (``monosage_cloud.app_access``).

On top of what the organization subscribes to, its owners and admins say which roles may use each app
(``app_roles``) and give or take an app from one person (``member_apps``). A member without an app gets
``403 app_not_allowed`` on its routes, does not see it in ``/api/apps`` and does not get its cards on Today.
Careers stays with the candidate role, and the account and organization pages are never apps, so an owner can
always come back and change it.
"""

from __future__ import annotations

import pytest
from cloud_support import audit_actions, build_org, sign_in

from monosage_cloud import app_access
from monosage_cloud.rbac import ORG_ROLES

DENIED_HEALTH = "Your organization has not given you Health. Ask an owner or admin."


def flags(browser) -> dict[str, bool]:
    status, data = browser.call("GET", "/api/apps")
    assert status == 200, data
    return {app["id"]: app["allowed"] for app in data["apps"]}


def set_roles(browser, app_id: str, roles) -> tuple[int, dict]:
    return browser.call("PUT", f"/api/org/apps/{app_id}/access", {"roles": roles})


def set_member(browser, member_id: str, allow=(), deny=()) -> tuple[int, dict]:
    return browser.call(
        "PUT",
        f"/api/org/members/{member_id}/apps",
        {"allow": list(allow), "deny": list(deny)},
    )


@pytest.fixture
def org(cloud):
    return build_org(cloud, apps="all", apps_space="personal")


# ---------------------------------------------------------------------------- the decision


def test_every_role_opens_every_app_until_the_organization_says_otherwise(cloud, org):
    policy = cloud.tenancy.policy(org.org_id)
    assert policy["app_roles"] == {} and policy["member_apps"] == {}
    for role in ORG_ROLES:
        assert app_access.decide(policy, role=role, member_id="m", app_id="care") == (
            True,
            "everyone",
        )
    candidate = sign_in(cloud, org.candidate)
    assert all(flags(candidate).values())
    assert candidate.call("GET", "/api/care/medicines")[0] == 200
    # a deny wins over an allow's role, an allow over the role list; Careers stays with candidates
    policy = {
        "app_roles": {"care": ["owner"], "careers": ["candidate"]},
        "member_apps": {
            "a": {"allow": ["care"], "deny": []},
            "b": {"allow": [], "deny": ["care"]},
        },
    }
    assert app_access.decide(policy, role="coach", member_id="a", app_id="care") == (
        True,
        "allow",
    )
    assert app_access.decide(policy, role="owner", member_id="b", app_id="care") == (
        False,
        "deny",
    )
    assert app_access.decide(policy, role="coach", member_id="c", app_id="care") == (
        False,
        "role",
    )
    assert app_access.decide(
        policy, role="candidate", member_id="c", app_id="careers"
    ) == (True, "always")
    assert app_access.decide(policy, role="coach", member_id="c", app_id="careers") == (
        False,
        "role",
    )
    # an old app id is its app's id now (Shop is part of Commerce)
    assert app_access.roles_for({"app_roles": {"commerce": ["owner"]}}, "shop") == [
        "owner"
    ]


def test_an_app_limited_to_some_roles_is_closed_for_the_others(cloud, org):
    owner = sign_in(cloud, org.owner)
    status, body = set_roles(owner, "care", ["owner", "admin", "coach"])
    assert status == 200 and body == {
        "app": "care",
        "roles": ["owner", "admin", "coach"],
        "everyone": False,
    }

    candidate = sign_in(cloud, org.candidate)
    status, body = candidate.call("GET", "/api/care/medicines")
    assert status == 403 and body["code"] == "app_not_allowed", body
    assert (
        body["error"] == DENIED_HEALTH
        and body["app"] == "care"
        and body["name"] == "Health"
    )
    assert flags(candidate)["care"] is False and flags(candidate)["learn"] is True
    entry = next(
        a for a in candidate.call("GET", "/api/apps")[1]["apps"] if a["id"] == "care"
    )
    assert entry["enabled"] is True and entry["allowed_reason"] == DENIED_HEALTH
    assert sign_in(cloud, org.coach).call("GET", "/api/care/medicines")[0] == 200
    assert owner.call("GET", "/api/care/medicines")[0] == 200

    # the Apps page shows it; every role again is Everyone, stored as no entry at all
    page = owner.call("GET", "/api/org/apps")[1]
    care = next(a for a in page["access"] if a["id"] == "care")
    assert (
        care["roles"] == ["owner", "admin", "coach"]
        and page["can_manage_access"] is True
    )
    assert page["roles"] == list(ORG_ROLES)
    assert set_roles(owner, "care", list(ORG_ROLES))[1]["everyone"] is True
    assert "care" not in cloud.tenancy.policy(org.org_id)["app_roles"]
    assert candidate.call("GET", "/api/care/medicines")[0] == 200
    assert set_roles(owner, "care", ["owner"])[0] == 200
    assert set_roles(owner, "care", None)[1] == {
        "app": "care",
        "roles": None,
        "everyone": True,
    }

    events = cloud.audit.list(org.org_id, action="org.app_access")
    assert [e["detail"]["to"] for e in reversed(events)] == [
        ["owner", "admin", "coach"],
        None,
        ["owner"],
        None,
    ]
    assert events[0]["target_id"] == "care"


def test_a_members_own_business_apps_follow_their_role_and_their_exceptions(cloud, org):
    owner = sign_in(cloud, org.owner)
    candidate = sign_in(cloud, org.candidate)
    other = sign_in(cloud, org.other)
    coach = sign_in(cloud, org.coach)
    # Commerce (Shop) in each member's own space, for staff only
    assert set_roles(owner, "commerce", ["owner", "admin", "coach"])[0] == 200
    status, body = candidate.call("GET", "/api/shop/products")
    assert status == 403 and body["code"] == "app_not_allowed"
    assert "Commerce" in body["error"]
    assert coach.call("GET", "/api/shop/products")[0] == 200

    # one candidate is given it, one coach has it taken away
    status, view = set_member(owner, org.candidate.member_id, allow=["shop"])
    assert status == 200, view
    assert view["allow"] == ["commerce"] and view["deny"] == []
    row = next(a for a in view["apps"] if a["id"] == "commerce")
    assert (
        row["allowed"] is True and row["by_role"] is False and row["setting"] == "allow"
    )
    assert candidate.call("GET", "/api/shop/products")[0] == 200
    assert (
        candidate.call(
            "POST", "/api/shop/products", {"name": "Mug", "price_cents": 900}
        )[0]
        == 201
    )
    assert other.call("GET", "/api/shop/products")[0] == 403
    assert set_member(owner, org.coach.member_id, deny=["commerce", "care"])[0] == 200
    assert coach.call("GET", "/api/shop/products")[0] == 403
    assert coach.call("GET", "/api/care/medicines")[1]["code"] == "app_not_allowed"
    assert flags(coach)["commerce"] is False and flags(coach)["learn"] is True

    # the member's apps read back for the Members page, and clearing the lists returns them to their role
    status, view = owner.call("GET", f"/api/org/members/{org.coach.member_id}/apps")
    assert (
        status == 200
        and view["deny"] == ["commerce", "care"]
        and view["can_change"] is True
    )
    assert set_member(owner, org.coach.member_id)[1]["deny"] == []
    assert coach.call("GET", "/api/shop/products")[0] == 200
    assert org.coach.member_id not in cloud.tenancy.policy(org.org_id)["member_apps"]
    assert (
        audit_actions(cloud, org.org_id, "member.app_access").count("member.app_access")
        == 3
    )


def test_today_and_pulse_leave_out_the_apps_a_member_was_not_given(cloud, org):
    candidate = sign_in(cloud, org.candidate)
    status, day = candidate.call("GET", "/api/life/today")
    assert status == 200 and "care" in day["apps"], day["apps"]
    owner = sign_in(cloud, org.owner)
    assert set_member(owner, org.candidate.member_id, deny=["care", "social"])[0] == 200
    status, day = candidate.call("GET", "/api/life/today")
    assert status == 200
    assert not {"care", "social", "family"} & set(day["apps"])
    assert all(
        card.get("app") not in {"care", "social", "family"}
        for card in day.get("cards", [])
    )
    status, pulse = candidate.call("GET", "/api/life/pulse")
    assert status == 200 and not {"care", "social", "family"} & set(pulse["apps"])
    # the owner still has them
    assert "care" in owner.call("GET", "/api/life/today")[1]["apps"]


def test_careers_stays_with_candidates_and_staff_can_be_kept_out(cloud, org):
    owner = sign_in(cloud, org.owner)
    status, body = set_roles(owner, "careers", ["owner", "admin", "coach"])
    assert status == 400 and "candidate role always keeps it" in body["error"]
    status, body = set_member(owner, org.candidate.member_id, deny=["careers"])
    assert status == 400 and "cannot be taken away" in body["error"]
    # Careers for candidates only: coaches no longer work in candidates' workspaces, candidates keep theirs
    assert set_roles(owner, "careers", ["candidate"])[0] == 200
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", "/api/jobs")[0] == 200
    coach = sign_in(cloud, org.coach)
    status, body = coach.call("GET", "/api/jobs", as_candidate=org.candidate.member_id)
    assert (
        status == 403
        and body["code"] == "app_not_allowed"
        and body["name"] == "Careers"
    )
    # a candidate's own record shows Careers as locked
    view = owner.call("GET", f"/api/org/members/{org.candidate.member_id}/apps")[1]
    assert next(a for a in view["apps"] if a["id"] == "careers")["locked"] is True


def test_settings_are_validated(cloud, org):
    owner = sign_in(cloud, org.owner)
    assert set_roles(owner, "nope", ["owner"])[0] == 404
    status, body = set_roles(owner, "care", ["owner", "boss"])
    assert status == 400 and "Unknown role: boss" in body["error"]
    assert set_roles(owner, "care", "owner")[0] == 400
    assert owner.call("PUT", "/api/org/apps/care/access", {})[0] == 400
    status, body = set_member(owner, org.coach.member_id, allow=["care"], deny=["care"])
    assert status == 400 and "both allowed and denied" in body["error"]
    status, body = set_member(owner, org.coach.member_id, deny=["spaceships"])
    assert status == 400 and "Unknown app" in body["error"]
    assert set_member(owner, "0" * 32, deny=["care"])[0] == 404
    # through the policy too, as the other policy settings
    status, body = owner.call(
        "PATCH", "/api/org/policy", {"app_roles": {"care": ["owner"], "x": ["owner"]}}
    )
    assert status == 400 and "Unknown app" in body["error"]
    status, body = owner.call(
        "PATCH", "/api/org/policy", {"member_apps": {"0" * 32: {"deny": ["care"]}}}
    )
    assert status == 400 and body["error"] == "Choose members of this organization."
    status, body = owner.call(
        "PATCH",
        "/api/org/policy",
        {
            "app_roles": {"care": ["owner", "admin"]},
            "member_apps": {org.coach.member_id: {"allow": ["care"]}},
        },
    )
    assert status == 200, body
    assert body["policy"]["app_roles"] == {"care": ["owner", "admin"]}
    assert body["policy"]["member_apps"] == {
        org.coach.member_id: {"allow": ["care"], "deny": []}
    }
    assert sign_in(cloud, org.coach).call("GET", "/api/care/medicines")[0] == 200
    assert sign_in(cloud, org.candidate).call("GET", "/api/care/medicines")[0] == 403


def test_only_owners_and_admins_change_it_and_only_owners_for_owners(cloud, org):
    for who in ("candidate", "coach", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        assert set_roles(browser, "care", ["owner"])[0] == 403, who
        assert set_member(browser, org.other.member_id, deny=["care"])[0] == 403, who
        assert (
            browser.call("GET", f"/api/org/members/{org.other.member_id}/apps")[0]
            == 403
        ), who
    admin = sign_in(cloud, org.admin)
    assert set_roles(admin, "care", ["owner", "admin", "coach"])[0] == 200
    status, body = set_roles(admin, "care", ["admin"])  # takes Health from the owners
    assert status == 403
    assert set_member(admin, org.owner.member_id, deny=["care"])[0] == 403
    status, body = admin.call(
        "PATCH", "/api/org/policy", {"app_roles": {"learn": ["admin"]}}
    )
    assert status == 403 and "Only an owner" in body["error"]
    view = admin.call("GET", f"/api/org/members/{org.owner.member_id}/apps")[1]
    assert view["can_change"] is False
    assert set_member(admin, org.other.member_id, deny=["care"])[0] == 200


def test_owners_never_lock_themselves_out_of_the_account_and_organization_pages(
    cloud, org
):
    owner = sign_in(cloud, org.owner)
    every = list(app_access.app_catalog.APP_IDS)
    for app_id in every:
        roles = ["candidate"] if app_id == "careers" else []
        assert set_roles(owner, app_id, roles)[0] == 200, app_id
    assert set_member(owner, org.owner.member_id, deny=every)[0] == 200
    assert not any(flags(owner).values())
    assert owner.call("GET", "/api/care/medicines")[0] == 403
    assert owner.call("GET", "/api/shop/products")[0] == 403
    for path in (
        "/api/auth/me",
        "/api/org",
        "/api/org/apps",
        "/api/org/members",
        "/api/org/audit",
        "/api/org/dashboard",
        "/api/apps",
    ):
        assert owner.call("GET", path)[0] == 200, path
    # and gives themselves the apps back
    assert set_member(owner, org.owner.member_id)[0] == 200
    assert set_roles(owner, "care", None)[0] == 200
    assert owner.call("GET", "/api/care/medicines")[0] == 200


def test_one_organizations_settings_never_reach_another(cloud, org):
    other = build_org(
        cloud, "Other Org", slug_email="other.test", apps="all", apps_space="personal"
    )
    owner = sign_in(cloud, org.owner)
    assert set_roles(owner, "care", ["owner"])[0] == 200
    # another organization's member is not found, and its policy is untouched
    assert set_member(owner, other.candidate.member_id, deny=["learn"])[0] == 404
    assert (
        owner.call("GET", f"/api/org/members/{other.candidate.member_id}/apps")[0]
        == 404
    )
    assert cloud.tenancy.policy(other.org_id)["app_roles"] == {}
    assert cloud.tenancy.policy(other.org_id)["member_apps"] == {}
    theirs = sign_in(cloud, other.candidate)
    assert theirs.call("GET", "/api/care/medicines")[0] == 200
    assert flags(theirs)["care"] is True
    assert sign_in(cloud, org.candidate).call("GET", "/api/care/medicines")[0] == 403
