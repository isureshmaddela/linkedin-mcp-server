"""Member spaces: every member has their own business apps and keeps what they earn (``monosage_cloud.spaces``).

A new organization gives each member a space of their own (``apps_space = "personal"``): a candidate, a coach or an
owner each open their own Shop, and nobody else reads it. The organization's own apps are opened with
``X-Aira-Space: company`` by owners, admins, coaches and the members it names. What a member earns in their space
is what the AI app's Earn tab and Life's earning goals count for them. An organization made before spaces keeps
working the old way (``"shared"``).
"""

from __future__ import annotations

import pytest
from cloud_support import build_org, sign_in, signup

from aira.suite import kit
from monosage_cloud import spaces

COMPANY = {"X-Aira-Space": "company"}


def products(browser, headers=None) -> list[str]:
    status, data = browser.call("GET", "/api/shop/products", headers=headers)
    assert status == 200, data
    return [item["name"] for item in data["products"]]


def add_product(browser, name: str, headers=None) -> None:
    status, data = browser.call(
        "POST",
        "/api/shop/products",
        {"name": name, "price_cents": 2500},
        headers=headers,
    )
    assert status == 201, data


@pytest.fixture
def org(cloud):
    return build_org(cloud, apps="all", apps_space="personal")


def test_a_new_organization_gives_every_member_a_space_of_their_own(cloud, org):
    fresh = signup(cloud, "Fresh Co", "owner@fresh.test")
    assert cloud.tenancy.policy(fresh["org"]["org_id"])["apps_space"] == "personal"
    assert cloud.tenancy.policy(org.org_id)["apps_space"] == "personal"
    candidate = sign_in(cloud, org.candidate)
    me = candidate.call("GET", "/api/auth/me")[1]
    assert me["spaces"] == {
        "mode": "personal",
        "apps": list(spaces.PERSONAL_APPS),
        "own": True,
        "company": False,
    }
    assert (
        sign_in(cloud, org.owner).call("GET", "/api/auth/me")[1]["spaces"]["company"]
        is True
    )

    # A candidate could not open the business apps before spaces; now they run their own shop.
    add_product(candidate, "Hand-made mug")
    assert products(candidate) == ["Hand-made mug"]
    assert cloud.workspaces.member_apps_path(
        org.org_id, org.candidate.member_id
    ).is_file()

    # Nobody else sees it: another candidate, the owner in their own space, or the owner in the company's apps.
    other = sign_in(cloud, org.other)
    owner = sign_in(cloud, org.owner)
    assert products(other) == []
    assert products(owner) == []
    assert products(owner, COMPANY) == []

    # The owner's own shop and the company's shop are two different shops.
    add_product(owner, "Owner's side project")
    add_product(owner, "Company merchandise", COMPANY)
    assert products(owner) == ["Owner's side project"]
    assert products(owner, COMPANY) == ["Company merchandise"]
    assert products(candidate) == ["Hand-made mug"]


def test_the_company_space_is_for_staff_and_the_members_it_names(cloud, org):
    candidate = sign_in(cloud, org.candidate)
    status, data = candidate.call("GET", "/api/shop/products", headers=COMPANY)
    assert status == 403 and data["code"] == "company_space_forbidden"
    coach = sign_in(cloud, org.coach)
    assert coach.call("GET", "/api/shop/products", headers=COMPANY)[0] == 200

    owner = sign_in(cloud, org.owner)
    status, policy = owner.call(
        "PATCH", "/api/org/policy", {"company_members": [org.candidate.member_id]}
    )
    assert status == 200, policy
    cloud.tenancy.invalidate_policy(org.org_id)
    assert candidate.call("GET", "/api/shop/products", headers=COMPANY)[0] == 200
    assert candidate.call("GET", "/api/auth/me")[1]["spaces"]["company"] is True

    status, data = owner.call(
        "PATCH", "/api/org/policy", {"company_members": ["0" * 32]}
    )
    assert status == 400
    status, data = owner.call("PATCH", "/api/org/policy", {"apps_space": "everyone"})
    assert status == 400


def test_what_a_member_earns_is_theirs(cloud, org):
    candidate = sign_in(cloud, org.candidate)
    owner = sign_in(cloud, org.owner)
    assert candidate.call("GET", "/api/shop/products")[0] == 200  # opens their space
    connection = cloud.workspaces.member_apps_connection(
        org.org_id, org.candidate.member_id
    )
    try:
        kit.record_revenue(
            connection,
            "shop",
            source_type="order",
            source_id="o-1",
            amount_cents=4200,
            label="Mug",
        )
        connection.commit()
    finally:
        connection.close()

    status, hub = candidate.call("GET", "/api/suite/revenue")
    assert status == 200 and hub["summary"]["this_month_cents"] == 4200
    status, earn = candidate.call("GET", "/api/ai/earn")
    assert status == 200, earn
    if earn.get("may_see"):
        assert earn["this_month_cents"] == 4200  # the AI app counts their own space

    # Not the company's revenue, and not another member's.
    def month(browser, headers=None) -> int:
        return browser.call("GET", "/api/suite/revenue", headers=headers)[1]["summary"][
            "this_month_cents"
        ]

    assert month(owner, COMPANY) == 0
    assert month(owner) == 0
    assert month(sign_in(cloud, org.other)) == 0


def test_an_organization_from_before_spaces_keeps_working_together(cloud, org):
    owner = sign_in(cloud, org.owner)
    assert owner.call("PATCH", "/api/org/policy", {"apps_space": "shared"})[0] == 200
    cloud.tenancy.invalidate_policy(org.org_id)
    add_product(owner, "Shared catalogue item")
    coach = sign_in(cloud, org.coach)
    assert products(coach) == [
        "Shared catalogue item"
    ]  # everyone works in the organization's apps
    assert products(owner, COMPANY) == ["Shared catalogue item"]
    candidate = sign_in(cloud, org.candidate)
    assert (
        candidate.call("GET", "/api/shop/products")[0] == 403
    )  # as before spaces: staff only
    assert candidate.call("GET", "/api/auth/me")[1]["spaces"]["mode"] == "shared"


def test_spaces_never_cross_organizations(cloud, org):
    other_org = build_org(
        cloud, "Other Org", slug_email="other.test", apps="all", apps_space="personal"
    )
    mine = sign_in(cloud, org.candidate)
    theirs = sign_in(cloud, other_org.candidate)
    add_product(mine, "Mine only")
    assert products(theirs) == []
    assert (
        cloud.workspaces.member_apps_path(
            org.org_id, org.candidate.member_id
        ).parent.parent.name
        == org.org_id
    )


def test_which_routes_have_spaces():
    assert spaces.personal_route("/api/shop/products") == "commerce"
    assert spaces.personal_route("/api/bookings/services") == "commerce"
    assert spaces.personal_route("/api/leads/runs") == "marketing"
    assert spaces.personal_route("/api/suite/revenue") == "hub"
    assert spaces.personal_route("/api/sites/projects") == "sites"
    # Ads is paid through the organization; the life apps were always per person; Careers has workspaces.
    for path in (
        "/api/ads/campaigns",
        "/api/journey/lives",
        "/api/jobs",
        "/api/life/today",
    ):
        assert spaces.personal_route(path) == ""
