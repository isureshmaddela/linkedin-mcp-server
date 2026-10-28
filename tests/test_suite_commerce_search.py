"""Commerce's search box (GET /api/shop/search and /api/bookings/search): what each finds, both builds."""

from __future__ import annotations

import pytest
from cloud_support import build_org, sign_in

import test_careers
from aira.suite import kit

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

WEEK = [{"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)]


@pytest.fixture(autouse=True)
def no_model():
    """Never call the real writing model (a live Ollama may run on this machine)."""
    from aira import suite

    class NoModel:
        def model(self, prompt):
            return None

    previous = suite.gateway()
    suite.set_gateway(NoModel())
    yield
    suite.set_gateway(previous)


def seed(api):
    mug = api.post(
        "/api/shop/products",
        {
            "name": "Speckled mug",
            "price_cents": 2200,
            "status": "active",
            "category": "Mugs",
        },
        expect=201,
    )["product"]
    api.post("/api/shop/products", {"name": "Vase", "price_cents": 4800}, expect=201)
    first = api.post(
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ana Silva",
            "email": "ana@example.com",
        },
        expect=201,
    )["order"]
    api.post(
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ben Okafor",
            "phone": "555 0100",
        },
        expect=201,
    )
    api.post("/api/bookings/staff", {"name": "Maya", "hours": WEEK}, expect=201)
    cut = api.post(
        "/api/bookings/services",
        {
            "name": "Haircut and style",
            "category": "Hair",
            "minutes": 45,
            "price_cents": 3500,
        },
        expect=201,
    )["service"]
    slots = api.get(f"/api/bookings/slots?service_id={cut['id']}&days=7")
    start = [slot["start"] for day in slots["days"] for slot in day["slots"]][2]
    booking = api.post(
        "/api/bookings/bookings",
        {
            "service_id": cut["id"],
            "start": start,
            "name": "Chloe Silva",
            "email": "chloe@example.com",
        },
        expect=201,
    )["booking"]
    return mug, first, cut, booking


def test_the_shop_search_finds_products_and_orders(env, api):
    kit.forget_schema()
    mug, first, _cut, _booking = seed(api)
    found = api.get("/api/shop/search?q=mug")
    assert [p["id"] for p in found["products"]] == [mug["id"]] and found["orders"] == []
    found = api.get("/api/shop/search?q=silva")
    assert found["products"] == [] and [
        o["customer_name"] for o in found["orders"]
    ] == ["Ana Silva"]
    # an order number with or without its #, a phone number, and a category
    assert (
        api.get(f"/api/shop/search?q=%23{first['number']}")["orders"][0]["id"]
        == first["id"]
    )
    assert (
        api.get(f"/api/shop/search?q={first['number']}")["orders"][0]["id"]
        == first["id"]
    )
    assert (
        api.get("/api/shop/search?q=0100")["orders"][0]["customer_name"] == "Ben Okafor"
    )
    assert api.get("/api/shop/search?q=mugs")["products"][0]["id"] == mug["id"]
    # newest orders first, at most `limit` of each
    both = api.get("/api/shop/search?q=example&limit=1")
    assert len(both["orders"]) <= 1
    assert api.get("/api/shop/search?q=%20%20") == {
        "q": "",
        "products": [],
        "orders": [],
    }
    assert api.call("GET", "/api/shop/search?q=mug&limit=0")[0] == 400


def test_the_bookings_search_finds_bookings_customers_and_services(env, api):
    kit.forget_schema()
    _mug, _first, cut, booking = seed(api)
    found = api.get("/api/bookings/search?q=silva")
    assert [b["id"] for b in found["bookings"]] == [booking["id"]]
    assert found["bookings"][0]["local_start"]  # ready to show
    assert [c["name"] for c in found["customers"]] == ["Chloe Silva"]
    assert found["services"] == [] and found["timezone"]
    by_reference = api.get(f"/api/bookings/search?q={booking['reference']}")
    assert by_reference["bookings"][0]["id"] == booking["id"]
    by_service = api.get("/api/bookings/search?q=hair")
    assert [s["id"] for s in by_service["services"]] == [cut["id"]]
    assert (
        by_service["bookings"][0]["id"] == booking["id"]
    )  # its service name matches too
    assert api.get("/api/bookings/search?q=") == {
        "q": "",
        "bookings": [],
        "customers": [],
        "services": [],
    }


def test_hosted_search_is_a_read_for_the_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "shop")
    owner = sign_in(cloud, org.owner)
    status, made = owner.call(
        "POST", "/api/shop/products", {"name": "Teapot", "price_cents": 3000}
    )
    assert status == 201, made
    status, found = sign_in(cloud, org.auditor).call("GET", "/api/shop/search?q=tea")
    assert status == 200 and found["products"][0]["name"] == "Teapot"
    assert (
        sign_in(cloud, org.auditor).call("GET", "/api/bookings/search?q=tea")[0] == 200
    )
    assert sign_in(cloud, org.candidate).call("GET", "/api/shop/search?q=tea")[0] == 403
    other = build_org(cloud, "Other Shop", slug_email="othershop.test")
    cloud.billing.subscribe_app(other.owner, "shop")
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/shop/search?q=tea")
    assert status == 200 and theirs["products"] == []
