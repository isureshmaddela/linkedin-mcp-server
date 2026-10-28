"""The Stripe adapter against the official ``stripe/stripe-mock`` server.

stripe-mock validates every request (parameter names, nesting, required fields) against Stripe's published
OpenAPI specification, so this proves the adapter speaks Stripe's real wire format, which the in-repo stub cannot.
Skipped when Docker is not available. Webhook signatures stay covered in test_cloud_billing.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest

from aira_cloud.billing_providers import StripeProvider
from aira_cloud.config import CloudConfig, FakeClock

IMAGE = "stripe/stripe-mock:latest"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="Docker is not available")


@pytest.fixture(scope="module")
def mock_url():
    if (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True
        ).returncode
        != 0
    ):
        pulled = subprocess.run(
            ["docker", "pull", IMAGE], capture_output=True, timeout=600
        )
        if pulled.returncode != 0:
            pytest.skip("stripe-mock image could not be pulled")
    name = f"aira-deploy-stripe-mock-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            "127.0.0.1::12111",
            IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        port = (
            subprocess.run(
                ["docker", "port", name, "12111"], capture_output=True, text=True
            )
            .stdout.split(":")[-1]
            .strip()
            .splitlines()[0]
        )
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{url}/v1/customers", timeout=2)
                break
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.5)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def stripe(mock_url):
    config = CloudConfig(stripe_secret_key="sk_test_mock", stripe_api_base=mock_url)
    return StripeProvider(config, FakeClock(1_800_000_000))


def _fixture(mock_url: str, path: str) -> dict:
    request = urllib.request.Request(
        f"{mock_url}{path}", headers={"Authorization": "Bearer sk_test_mock"}
    )
    return json.load(urllib.request.urlopen(request, timeout=10))


def test_customer_and_checkout_requests_validate_against_the_openapi_spec(stripe):
    customer = stripe.create_customer("org-1", "Acme Outplacement", "owner@acme.test")
    assert customer.startswith("cus_")
    session = stripe.create_checkout(
        org_id="org-1",
        customer_id=customer,
        plan_id="team",
        plan_name="Team",
        seats=25,
        seat_cents=1100,
        currency="usd",
        success_url="https://aira.example/billing?ok=1",
        cancel_url="https://aira.example/billing",
    )
    assert session["id"].startswith("cs_") and session["url"].startswith("https://")


def test_plan_change_cancel_portal_and_invoice_item_validate(stripe):
    updated = stripe.update_subscription(
        "sub_123",
        plan_id="enterprise",
        plan_name="Enterprise",
        seats=80,
        seat_cents=1400,
        currency="usd",
        prorate=True,
    )
    assert updated["id"].startswith("sub_")
    stripe.update_subscription(
        "sub_123",
        plan_id="starter",
        plan_name="Starter",
        seats=5,
        seat_cents=900,
        currency="usd",
        prorate=False,
    )
    assert stripe.cancel_subscription("sub_123", at_period_end=True)["id"]
    assert stripe.cancel_subscription("sub_123", at_period_end=False)["id"]
    assert stripe.portal_url("cus_123", "https://aira.example/billing").startswith(
        "https://"
    )
    assert stripe.add_invoice_item("cus_123", 4000, "Monosage overage 2026-09", "usd")


def test_the_adapter_reads_the_shapes_stripe_actually_returns(stripe, mock_url):
    """Normalize events built from stripe-mock's own object fixtures (current API shapes)."""
    invoice = _fixture(mock_url, "/v1/invoices/in_123")
    invoice["metadata"] = {}
    invoice["parent"]["subscription_details"]["subscription"] = "sub_live_1"
    invoice["parent"]["subscription_details"]["metadata"] = {"org_id": "org-1"}
    paid = stripe.normalize(
        {"id": "evt_1", "type": "invoice.paid", "data": {"object": invoice}}
    )
    assert paid and paid["subscription_id"] == "sub_live_1"
    assert paid["org_id"] == "org-1" and paid["period_end"]
    assert paid["invoice_id"] == "in_123" and paid["currency"] == "usd"

    subscription = _fixture(mock_url, "/v1/subscriptions/sub_123")
    subscription["metadata"] = {"org_id": "org-1", "plan_id": "team", "seats": "25"}
    event = stripe.normalize(
        {
            "id": "evt_2",
            "type": "customer.subscription.updated",
            "data": {"object": subscription},
        }
    )
    assert event and event["period_start"] and event["period_end"], (
        "the billing period moved onto the items in newer API versions"
    )
    assert event["plan_id"] == "team" and event["seats"] == 25

    session = _fixture(mock_url, "/v1/checkout/sessions/cs_123")
    session["metadata"] = {"org_id": "org-1", "plan_id": "team", "seats": "25"}
    session["customer"], session["subscription"] = "cus_9", "sub_9"
    done = stripe.normalize(
        {
            "id": "evt_3",
            "type": "checkout.session.completed",
            "data": {"object": session},
        }
    )
    assert done and done["subscription_id"] == "sub_9" and done["seats"] == 25


def test_app_add_on_items_validate_against_the_openapi_spec(stripe):
    """An app add-on is a second subscription item: price_data with a product, a quantity and metadata.app_id."""
    updated = stripe.update_subscription(
        "sub_123",
        plan_id="team",
        plan_name="Team",
        seats=25,
        seat_cents=1100,
        currency="usd",
        prorate=True,
        addons=[{"app_id": "leads", "name": "Leads", "seat_cents": 400, "seats": 25}],
    )
    assert updated["id"].startswith("sub_")
    without = stripe.update_subscription(
        "sub_123",
        plan_id="team",
        plan_name="Team",
        seats=25,
        seat_cents=1100,
        currency="usd",
        prorate=False,
    )
    assert without["id"].startswith("sub_")
