"""Business hub > Customers through the single-user server: search, the contact's page and timeline, editing,
merging, Send to Sales; the subscriber confirmation sent again; the hosted build's rules for the new routes."""

from __future__ import annotations

import pytest

import test_careers
from aira.suite import kit, public

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@pytest.fixture(autouse=True)
def fresh():
    kit.forget_schema()
    yield
    public.reset_index()


def _order(api, **customer):
    mug = api.post(
        "/api/shop/products",
        {"name": "Mug", "price_cents": 1500, "status": "draft"},
        expect=201,
    )["product"]
    return api.post(
        "/api/shop/orders",
        {"items": [{"product_id": mug["id"]}], "name": "Ana Silva", **customer},
        expect=201,
    )["order"]


def test_customers_search_page_edit_merge_and_send_to_sales(env, api):
    order = _order(
        api, email="ana@example.com", phone="+91 98765 43210", mark_paid=True
    )
    assert "payments" in api.get(f"/api/shop/orders/{order['id']}")["order"]
    api.post(
        "/api/marketing/subscribers",
        {"email": "ANA@example.com", "name": "Ana"},
        expect=201,
    )
    other = _order(api, email="ana.silva@work.example")
    listed = api.get("/api/suite/customers")
    assert listed["total"] == 2 and set(listed["apps"]) == {"shop", "marketing"}
    found = api.get("/api/suite/customers?q=9876543210")["contacts"]
    assert len(found) == 1 and found[0]["email"] == "ana@example.com"
    keep = found[0]["id"]
    page = api.get(f"/api/suite/customers/{keep}")
    kinds = {item["kind"] for item in page["timeline"]}
    assert {"order", "payment", "subscriber"} <= kinds
    assert page["paid"] == [{"currency": "USD", "cents": 1500}]
    edited = api.call(
        "PATCH",
        f"/api/suite/customers/{keep}",
        {"tags": "vip, wholesale", "notes": "Prefers calls"},
    )
    assert edited[0] == 200 and edited[1]["contact"]["tags"] == ["vip", "wholesale"]
    second = next(
        c for c in api.get("/api/suite/customers")["contacts"] if c["id"] != keep
    )
    merged = api.post(f"/api/suite/customers/{keep}/merge", {"other_id": second["id"]})
    assert merged["moved"] == 1 and merged["contact"]["id"] == keep
    assert api.get("/api/suite/customers")["total"] == 1
    status, body = api.call("GET", f"/api/suite/customers/{second['id']}")
    assert status == 404
    page = api.get(f"/api/suite/customers/{keep}")
    assert sum(1 for item in page["timeline"] if item["kind"] == "order") == 2
    assert other["id"] in {item["ref"] for item in page["timeline"]}
    # Send to Sales: a deal in the pipeline, once
    sent = api.call("POST", f"/api/suite/customers/{keep}/sales", {})
    assert sent[0] == 201 and sent[1]["deal"]["stage"] == "new"
    again = api.call("POST", f"/api/suite/customers/{keep}/sales", {})
    assert again[0] == 200 and again[1]["deal"]["id"] == sent[1]["deal"]["id"]
    deals = api.get("/api/studio/deals")["deals"]
    assert [deal["id"] for deal in deals] == [sent[1]["deal"]["id"]]
    assert (
        api.get(f"/api/suite/customers/{keep}")["deals"][0]["ref"]
        == sent[1]["deal"]["id"]
    )
    nothing = api.get(
        f"/api/suite/sales/handoff?app=marketing&object_type=lead&object_id={'0' * 32}"
    )
    assert nothing["deal"] is None and nothing["sales_available"] is True
    status, body = api.call(
        "POST",
        "/api/suite/sales/handoff",
        {"app": "shop", "object_type": "order", "object_id": order["id"]},
    )
    assert status == 400
    status, body = api.call(
        "POST", f"/api/suite/customers/{keep}/merge", {"other_id": keep}
    )
    assert status == 400


def test_a_subscriber_who_has_confirmed_is_not_asked_again(env, api):
    made = api.post(
        "/api/marketing/subscribers", {"email": "sam@example.com"}, expect=201
    )["subscriber"]
    status, body = api.call(
        "POST", f"/api/marketing/subscribers/{made['id']}/confirm-again", {}
    )
    assert status == 400 and "not confirmed yet" in body["error"]
    counts = api.get("/api/marketing/subscribers")["counts"]
    assert counts == {"subscribed": 1, "unsubscribed": 0, "pending": 0}


def test_the_new_routes_have_hosted_rules():
    from aira.airaapi import http as airaapi_http
    from monosage_cloud.policy import POLICY

    airaapi_http.load_route_modules()
    wanted = {
        "customers_list": "suite.read",
        "customers_get": "suite.read",
        "customers_update": "suite.manage",
        "customers_merge": "suite.manage",
        "customers_to_sales": "suite.manage",
        "sales_handoff_get": "suite.read",
        "sales_handoff": "suite.manage",
        "subscribers_confirm_again": "suite.manage",
    }
    found = {}
    for entry in airaapi_http.AIRA_ROUTES:
        name = getattr(entry.handler, "__name__", "")
        if name in wanted and entry.handler.__module__.startswith("aira.suite."):
            found[name] = POLICY[entry.attr].permission
    assert found == wanted
