"""Online payments (aira/payments): the Razorpay client and signatures, payment requests, webhooks, refunds.

Everything talks to the local stand-in (tests/razorpay_payments_stub.py); the live Razorpay API is never called.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error

import pytest
from razorpay_payments_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub

from aira.payments import apps as _apps  # noqa: F401 - registers the apps
from aira.payments import razorpay
from aira.payments import service as payments
from aira.suite import kit, public
from aira.suite.shop import service as shop
from aira.suite.shop import store as shop_store


class Secrets:
    """The secret store's shape (get/put/delete by name), in memory."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, connection, name):
        return self.values.get(name)

    def put(self, connection, name, value, now=None):
        self.values[name] = value

    def delete(self, connection, name):
        return self.values.pop(name, None) is not None


@pytest.fixture
def stub(monkeypatch):
    server = RazorpayStub().start()
    monkeypatch.setenv(razorpay.API_BASE_ENV, server.base)
    yield server
    server.stop()


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "pay.sqlite3")
    connection.row_factory = sqlite3.Row
    shop_store.ensure(connection)
    yield connection
    connection.close()
    public.reset_index()


@pytest.fixture
def secrets():
    return Secrets()


@pytest.fixture
def rzp(db, stub, secrets):
    """A business in India that takes payments with Razorpay (test mode)."""
    kit.save_business(
        db, {"name": "Chai Co", "currency": "INR", "email": "hi@chai.example"}
    )
    secrets.put(db, kit.RAZORPAY_KEY_SECRET, KEY_SECRET)
    secrets.put(db, kit.RAZORPAY_WEBHOOK_SECRET, WEBHOOK_SECRET)
    kit.save_payment_settings(
        db,
        {
            "provider": "razorpay",
            "razorpay_key_id": KEY_ID,
            "key_secret_saved": True,
            "webhook_secret_saved": True,
        },
    )
    with payments.secrets(secrets):
        yield stub


def an_order(db, *, price=25000, quantity=2, stock=10, email="ana@example.com"):
    product = shop.create_product(
        db,
        {
            "name": "Masala tea",
            "price_cents": price,
            "status": "active",
            "track_stock": True,
            "stock": stock,
        },
    )
    order = shop.place_order(
        db,
        {
            "items": [{"product_id": product["id"], "quantity": quantity}],
            "name": "Ana Rao",
            "email": email,
            "phone": "+91 98450 00000",
        },
    )
    return product, order


def deliver(db, secrets, body, headers):
    return payments.handle_webhook(
        db,
        body,
        signature=headers["X-Razorpay-Signature"],
        event_id=headers["X-Razorpay-Event-Id"],
        secret_store=secrets,
    )


# ---------------------------------------------------------------------------
# the client and the signatures
# ---------------------------------------------------------------------------


def test_signatures_are_hmac_sha256_hex_and_compared_exactly():
    body = b'{"event":"payment_link.paid"}'
    good = razorpay.webhook_signature(body, "s3cret")
    assert len(good) == 64 and razorpay.verify_webhook(body, good, "s3cret")
    assert razorpay.verify_webhook(
        body, good.upper(), "s3cret"
    )  # hex case does not matter
    assert not razorpay.verify_webhook(body + b" ", good, "s3cret")
    assert not razorpay.verify_webhook(body, good, "other")
    assert not razorpay.verify_webhook(body, "", "s3cret")
    assert not razorpay.verify_webhook(body, good, "")
    signed = razorpay.callback_signature(
        payment_link_id="plink_1",
        reference_id="R1",
        status="paid",
        payment_id="pay_1",
        key_secret="k",
    )
    import hashlib
    import hmac

    assert (
        signed == hmac.new(b"k", b"plink_1|R1|paid|pay_1", hashlib.sha256).hexdigest()
    )
    ok = dict(
        payment_link_id="plink_1",
        reference_id="R1",
        status="paid",
        payment_id="pay_1",
        key_secret="k",
    )
    assert razorpay.verify_payment_link_callback(signature=signed, **ok)
    assert not razorpay.verify_payment_link_callback(
        signature=signed, **{**ok, "status": "created"}
    )
    assert not razorpay.verify_payment_link_callback(
        signature=signed, **{**ok, "key_secret": "x"}
    )


def test_key_ids_tell_test_from_live_and_the_api_base_must_be_safe(monkeypatch):
    assert razorpay.mode_of("rzp_test_abcDEF123") == "test"
    assert razorpay.mode_of("rzp_live_abcDEF123") == "live"
    assert razorpay.mode_of("pk_test_123") == "" and not razorpay.valid_key_id(
        "rzp_prod_123456"
    )
    monkeypatch.delenv(razorpay.API_BASE_ENV, raising=False)
    assert razorpay.api_base() == "https://api.razorpay.com/v1"
    monkeypatch.setenv(razorpay.API_BASE_ENV, "http://evil.example/v1")
    with pytest.raises(razorpay.RazorpayError, match="https"):
        razorpay.api_base()
    monkeypatch.setenv(razorpay.API_BASE_ENV, "http://127.0.0.1:9/v1")
    assert razorpay.api_base() == "http://127.0.0.1:9/v1"
    with pytest.raises(ValueError, match="rzp_test_"):
        razorpay.Client("sk_live_x", "secret")
    assert (
        razorpay.minor_units(12345, "INR") == 12345
        and razorpay.minor_units(120000, "JPY") == 1200
    )


def test_the_client_creates_fetches_and_cancels_links_with_basic_auth(stub):
    client = razorpay.Client(KEY_ID, KEY_SECRET)
    link = client.create_payment_link(
        amount=50000,
        currency="inr",
        reference_id="MSREF1",
        description="Order #1001",
        customer={
            "name": "Ana",
            "email": "ana@example.com",
            "contact": "+919845000000",
            "other": "dropped",
        },
        notify={"sms": True},
        callback_url="https://shop.example.com/p/token",
        expire_by=2_000_000_000,
        notes={"a": "1", "empty": ""},
    )
    sent = stub.calls_to("POST", "/payment_links")[0]
    assert sent["authorized"]
    body = sent["body"]
    assert (
        body["amount"] == 50000
        and body["currency"] == "INR"
        and body["reference_id"] == "MSREF1"
    )
    assert body["callback_method"] == "get" and body["callback_url"].endswith(
        "/p/token"
    )
    assert body["notify"] == {"sms": True, "email": False} and body["notes"] == {
        "a": "1"
    }
    assert body["customer"] == {
        "name": "Ana",
        "email": "ana@example.com",
        "contact": "+919845000000",
    }
    assert link["short_url"].startswith("https://rzp.io/")
    assert client.fetch_payment_link(link["id"])["status"] == "created"
    assert client.cancel_payment_link(link["id"])["status"] == "cancelled"
    with pytest.raises(razorpay.RazorpayError, match="already cancelled") as refused:
        client.cancel_payment_link(link["id"])
    assert refused.value.status == 400 and refused.value.code == "BAD_REQUEST_ERROR"
    order = client.create_order(amount=100, currency="INR", receipt="r1")
    assert client.fetch_order(order["id"])["receipt"] == "r1"
    assert client.check() == {"ok": True, "mode": "test"}
    with pytest.raises(ValueError, match="not a Razorpay"):
        client.fetch_payment("../payments")
    with pytest.raises(ValueError, match="at least"):
        client.create_payment_link(
            amount=50, currency="INR", reference_id="x", description="d"
        )


def test_the_client_explains_refusals_and_outages(stub):
    wrong = razorpay.Client(KEY_ID, "not-the-secret")
    with pytest.raises(razorpay.RazorpayError, match="key id or key secret") as refused:
        wrong.check()
    assert refused.value.status == 401
    client = razorpay.Client(KEY_ID, KEY_SECRET)
    stub.fail_next.append(503)
    with pytest.raises(razorpay.RazorpayError, match="trouble"):
        client.check()
    stub.fail_next.append(429)
    with pytest.raises(razorpay.RazorpayError, match="too many"):
        client.check()

    def offline(request, timeout):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(razorpay.RazorpayError, match="Could not reach Razorpay"):
        razorpay.Client(KEY_ID, KEY_SECRET, opener=offline).check()


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_payment_settings_validate_and_public_pages_never_see_secrets(db):
    kit.save_business(
        db,
        {
            "name": "Chai Co",
            "currency": "INR",
            "payment_link": "https://pay.example/chai",
        },
    )
    assert (
        kit.payment_settings(db)["provider"] == "manual"
    )  # an old business with a payment link
    with pytest.raises(ValueError, match="rzp_test_"):
        kit.save_payment_settings(
            db, {"provider": "razorpay", "razorpay_key_id": "key_123"}
        )
    with pytest.raises(ValueError, match="key id"):
        kit.save_payment_settings(db, {"provider": "razorpay", "razorpay_key_id": ""})
    with pytest.raises(ValueError, match="UPI ID"):
        kit.save_payment_settings(db, {"provider": "none", "upi_id": "not a vpa"})
    saved = kit.save_payment_settings(
        db, {"provider": "razorpay", "razorpay_key_id": KEY_ID, "upi_id": "chai@okaxis"}
    )
    assert kit.payment_mode(saved) == "test" and not kit.online_ready(
        saved
    )  # no key secret yet
    seen = kit.public_business(db)
    assert (
        seen["online_payments"] is False and seen["payment_link"] == ""
    )  # Razorpay replaces the fixed link
    assert seen["upi_id"] == "chai@okaxis" and seen["upi_name"] == "Chai Co"
    kit.save_payment_settings(db, {"key_secret_saved": True})
    seen = kit.public_business(db)
    assert seen["online_payments"] is True
    assert not any("secret" in key or "razorpay" in key for key in seen)
    kit.save_business(db, {"currency": "USD"})
    assert kit.public_business(db)["upi_id"] == ""  # UPI is for rupees


# ---------------------------------------------------------------------------
# a payment from start to end
# ---------------------------------------------------------------------------


def test_a_shop_order_paid_by_payment_link_is_marked_paid_once_by_the_webhook(
    db, rzp, secrets
):
    _, order = an_order(db)
    made = payments.create_request(db, "shop", "order", order["id"])
    assert (
        made["status"] == "created"
        and made["amount_cents"] == 50000
        and made["currency"] == "INR"
    )
    assert made["mode"] == "test" and made["short_url"].startswith("https://rzp.io/")
    sent = rzp.calls_to("POST", "/payment_links")[0]["body"]
    assert sent["amount"] == 50000 and sent["reference_id"] == made["reference"]
    assert (
        sent["notes"]["monosage_request"] == made["id"]
        and sent["description"] == f"Order #{order['number']}"
    )
    assert sent["customer"]["email"] == "ana@example.com"
    assert sent["callback_url"].endswith(
        public.link_for(db, "shop", "order", order["id"])["path"]
    )
    # pressing Pay again reuses the open link
    assert payments.create_request(db, "shop", "order", order["id"])["id"] == made["id"]
    assert len(rzp.calls_to("POST", "/payment_links")) == 1

    payment = rzp.pay(made["provider_ref"])
    body, headers = rzp.webhook(
        "payment_link.paid", link_id=made["provider_ref"], payment=payment
    )
    assert deliver(db, secrets, body, headers) == {
        "ok": True,
        "event": "payment_link.paid",
        "outcome": "paid",
    }
    paid = shop_store.get_order(db, order["id"])
    assert (
        paid["payment_status"] == "paid"
        and payment["id"] in paid["history"][-1]["note"]
    )
    assert kit.revenue(db)["total_cents"] == 50000
    request = payments.get(db, made["id"])
    assert (
        request["status"] == "paid"
        and request["payment_id"] == payment["id"]
        and request["method"] == "upi"
    )
    # the same delivery again (Razorpay retries) changes nothing
    assert deliver(db, secrets, body, headers)["duplicate"] is True
    # a new delivery of the same payment (another event id) is still counted once
    body2, headers2 = rzp.webhook(
        "payment_link.paid", link_id=made["provider_ref"], payment=payment
    )
    assert deliver(db, secrets, body2, headers2)["outcome"] == "already_paid"
    assert kit.revenue(db)["payments"] == 1
    with pytest.raises(ValueError, match="already paid"):
        payments.create_request(db, "shop", "order", order["id"])


def test_a_webhook_with_a_bad_signature_is_refused_before_anything_is_read(
    db, rzp, secrets
):
    _, order = an_order(db)
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = rzp.pay(made["provider_ref"])
    body, headers = rzp.webhook(
        "payment_link.paid",
        link_id=made["provider_ref"],
        payment=payment,
        secret="wrong",
    )
    with pytest.raises(PermissionError, match="signature"):
        deliver(db, secrets, body, headers)
    with pytest.raises(PermissionError):
        payments.handle_webhook(db, b"not json", signature="", secret_store=secrets)
    secrets.delete(db, kit.RAZORPAY_WEBHOOK_SECRET)
    good, good_headers = rzp.webhook(
        "payment_link.paid", link_id=made["provider_ref"], payment=payment
    )
    with pytest.raises(PermissionError, match="webhook secret"):
        deliver(db, secrets, good, good_headers)
    assert shop_store.get_order(db, order["id"])["payment_status"] == "unpaid"
    assert kit.count(db, "SELECT COUNT(*) FROM payment_events") == 0
    with pytest.raises(ValueError, match="too large"):
        payments.handle_webhook(
            db,
            b"x" * (payments.MAX_WEBHOOK_BYTES + 1),
            signature="",
            secret_store=secrets,
        )


@pytest.mark.parametrize(
    ("amount", "currency", "says"),
    [(49999, "INR", "₹499.99"), (50000, "USD", "$500.00")],
)
def test_a_payment_for_another_amount_or_currency_is_a_mismatch_not_paid(
    db, rzp, secrets, amount, currency, says
):
    _, order = an_order(db)
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = rzp.pay(made["provider_ref"], amount=amount, currency=currency)
    body, headers = rzp.webhook(
        "payment_link.paid", link_id=made["provider_ref"], payment=payment
    )
    assert deliver(db, secrets, body, headers)["outcome"] == "mismatch"
    assert shop_store.get_order(db, order["id"])["payment_status"] == "unpaid"
    request = payments.get(db, made["id"])
    assert request["status"] == "mismatch" and says in request["problem"]
    assert kit.revenue(db)["total_cents"] == 0
    shown = payments.list_requests(db, status="attention")
    assert [item["id"] for item in shown] == [made["id"]]
    assert kit.activity(db, limit=1)[0]["kind"] == "payment_problem"


def test_link_expired_cancelled_and_failed_attempts(db, rzp, secrets):
    _, order = an_order(db)
    made = payments.create_request(db, "shop", "order", order["id"])
    failed = {
        "id": "pay_failed1",
        "amount": 50000,
        "currency": "INR",
        "status": "failed",
        "notes": {"monosage_request": made["id"]},
        "error_description": "Payment was declined by the bank",
    }
    body, headers = rzp.webhook("payment.failed", payment=failed)
    assert deliver(db, secrets, body, headers)["outcome"] == "failed_attempt"
    request = payments.get(db, made["id"])
    assert (
        request["status"] == "created" and "declined by the bank" in request["problem"]
    )
    rzp.links[made["provider_ref"]]["status"] = "expired"
    body, headers = rzp.webhook("payment_link.expired", link_id=made["provider_ref"])
    assert deliver(db, secrets, body, headers)["outcome"] == "closed"
    assert payments.get(db, made["id"])["status"] == "expired"
    # a new link is made for the same order once the old one closed
    again = payments.create_request(db, "shop", "order", order["id"])
    assert again["id"] != made["id"]
    body, headers = rzp.webhook("payment_link.cancelled", link_id=again["provider_ref"])
    assert deliver(db, secrets, body, headers)["outcome"] == "closed"
    assert payments.get(db, again["id"])["status"] == "cancelled"
    body, headers = rzp.webhook("payment.captured", payment=failed)
    assert deliver(db, secrets, body, headers)["outcome"] == "ignored"


def test_an_amount_change_cancels_the_open_link_and_makes_a_new_one(db, rzp):
    _, order = an_order(db)
    first = payments.create_request(db, "shop", "order", order["id"])
    db.execute("UPDATE sh_orders SET total_cents = 40000 WHERE id = ?", (order["id"],))
    db.commit()
    second = payments.create_request(db, "shop", "order", order["id"])
    assert second["id"] != first["id"] and second["amount_cents"] == 40000
    assert payments.get(db, first["id"])["status"] == "cancelled"
    assert rzp.links[first["provider_ref"]]["status"] == "cancelled"


def test_the_customer_return_is_checked_then_confirmed_with_razorpay(db, rzp, secrets):
    _, order = an_order(db)
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = rzp.pay(made["provider_ref"])
    forged = rzp.return_params(made["provider_ref"], payment["id"], key_secret="guess")
    assert payments.confirm_return(db, forged)["verified"] is False
    assert shop_store.get_order(db, order["id"])["payment_status"] == "unpaid"
    shown = payments.confirm_return(
        db, rzp.return_params(made["provider_ref"], payment["id"])
    )
    assert shown["verified"] and shown["paid"] and shown["status"] == "paid"
    assert shop_store.get_order(db, order["id"])["payment_status"] == "paid"
    # the webhook that follows changes nothing
    body, headers = rzp.webhook(
        "payment_link.paid", link_id=made["provider_ref"], payment=payment
    )
    assert deliver(db, secrets, body, headers)["outcome"] == "already_paid"
    assert kit.revenue(db)["payments"] == 1


def test_a_payment_for_an_order_cancelled_meanwhile_needs_the_business(
    db, rzp, secrets
):
    _, order = an_order(db)
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = rzp.pay(made["provider_ref"])
    shop.cancel(db, order["id"])  # the shop cancelled it: its link closed here
    assert payments.get(db, made["id"])["status"] == "cancelled"
    body, headers = rzp.webhook(
        "payment_link.paid", link_id=made["provider_ref"], payment=payment
    )
    assert deliver(db, secrets, body, headers)["outcome"] == "needs_attention"
    request = payments.get(db, made["id"])
    assert (
        request["status"] == "mismatch"
        and "could not be marked paid" in request["problem"]
    )
    assert kit.revenue(db)["total_cents"] == 0


# ---------------------------------------------------------------------------
# refunds
# ---------------------------------------------------------------------------


def paid_order(db, rzp, secrets, **kw):
    product, order = an_order(db, **kw)
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = rzp.pay(made["provider_ref"])
    deliver(
        db,
        secrets,
        *rzp.webhook(
            "payment_link.paid", link_id=made["provider_ref"], payment=payment
        ),
    )
    return product, order, made, payment


def test_partial_then_full_refunds_are_negative_lines_and_return_the_stock(
    db, rzp, secrets
):
    product, order, made, payment = paid_order(db, rzp, secrets)
    assert shop_store.get_product(db, product["id"])["stock"] == 8
    with pytest.raises(ValueError, match="At most"):
        payments.refund(db, made["id"], amount_cents=60000)
    view = payments.refund(
        db, made["id"], amount_cents=10000, note="One box was broken"
    )
    assert (
        view["status"] == "partially_refunded"
        and view["refunded_cents"] == 10000
        and view["refundable_cents"] == 40000
    )
    sent = rzp.calls_to("POST", f"/payments/{payment['id']}/refund")[0]["body"]
    assert sent["amount"] == 10000 and sent["notes"]["monosage_request"] == made["id"]
    partly = shop_store.get_order(db, order["id"])
    assert partly["payment_status"] == "paid" and partly["refunded_cents"] == 10000
    assert (
        shop_store.get_product(db, product["id"])["stock"] == 8
    )  # a partial refund keeps the order going
    totals = kit.revenue(db)
    assert (
        totals["total_cents"] == 40000
        and totals["refunded_cents"] == 10000
        and totals["payments"] == 1
    )
    lines = kit.revenue_lines(db)
    refund_line = next(line for line in lines if line["kind"] == "refund")
    assert (
        refund_line["amount_cents"] == -10000
        and refund_line["refund_of"] == order["id"]
    )
    assert kit.refunded_cents(db, "shop", "order", order["id"]) == 10000
    shop.refund(db, order["id"], secret_store=secrets)  # the rest, through Razorpay
    done = shop_store.get_order(db, order["id"])
    assert (
        done["payment_status"] == "refunded"
        and done["fulfilment_status"] == "cancelled"
        and done["restocked"]
    )
    assert shop_store.get_product(db, product["id"])["stock"] == 10
    assert payments.get(db, made["id"])["status"] == "refunded"
    assert kit.revenue(db)["total_cents"] == 0
    with pytest.raises(ValueError, match="Only a paid"):
        payments.refund(db, made["id"])


def test_a_pending_refund_lands_with_the_refund_processed_webhook_once(
    db, rzp, secrets
):
    _, order, made, payment = paid_order(db, rzp, secrets)
    rzp.refund_status = "pending"
    view = payments.refund(db, made["id"])
    assert (
        view["status"] == "paid"
        and view["refunds"][0]["status"] == "pending"
        and view["refundable_cents"] == 0
    )
    assert kit.revenue(db)["total_cents"] == 50000  # not yet
    refund = next(iter(rzp.refunds.values()))
    refund["status"] = "processed"
    event = rzp.webhook("refund.processed", payment=payment, refund=refund)
    assert deliver(db, secrets, *event)["outcome"] == "refunded"
    assert (
        deliver(
            db,
            secrets,
            *rzp.webhook("refund.processed", payment=payment, refund=refund),
        )["outcome"]
        == "refunded"
    )
    assert (
        kit.revenue(db)["total_cents"] == 0
        and kit.revenue(db)["refunded_cents"] == 50000
    )
    assert shop_store.get_order(db, order["id"])["payment_status"] == "refunded"


def test_a_failed_refund_is_shown_and_a_dashboard_refund_is_recorded(db, rzp, secrets):
    _, order, made, payment = paid_order(db, rzp, secrets)
    rzp.refund_status = "pending"
    payments.refund(db, made["id"], amount_cents=5000)
    refund = next(iter(rzp.refunds.values()))
    refund["status"] = "failed"
    assert (
        deliver(
            db, secrets, *rzp.webhook("refund.failed", payment=payment, refund=refund)
        )["outcome"]
        == "refund_failed"
    )
    view = payments.view(db, payments.get(db, made["id"]))
    assert (
        view["refunds"][0]["status"] == "failed"
        and "refund failed" in view["problem"].lower()
    )
    assert view["refundable_cents"] == 50000
    # the business refunds in the Razorpay dashboard instead: the webhook records it here
    dashboard = {
        "id": "rfnd_dash0001",
        "entity": "refund",
        "amount": 20000,
        "currency": "INR",
        "payment_id": payment["id"],
        "status": "processed",
        "notes": {},
    }
    assert (
        deliver(
            db,
            secrets,
            *rzp.webhook("refund.processed", payment=payment, refund=dashboard),
        )["outcome"]
        == "refunded"
    )
    assert payments.get(db, made["id"])["refunded_minor"] == 20000
    assert kit.revenue(db)["total_cents"] == 30000


def test_a_shop_order_paid_by_hand_is_refunded_by_hand_with_a_dated_line(db):
    kit.save_business(db, {"name": "Chai Co", "currency": "INR"})
    product, order = an_order(db)
    shop.mark_paid(db, order["id"])
    shop.refund(db, order["id"], amount_cents=5000, note="late delivery")
    assert shop_store.get_order(db, order["id"])["payment_status"] == "paid"
    assert kit.revenue(db)["total_cents"] == 45000
    shop.refund(db, order["id"])
    assert shop_store.get_order(db, order["id"])["payment_status"] == "refunded"
    assert shop_store.get_product(db, product["id"])["stock"] == 10
    assert kit.revenue(db)["total_cents"] == 0 and kit.revenue(db)["payments"] == 1


# ---------------------------------------------------------------------------
# what the business must set up first
# ---------------------------------------------------------------------------


def test_a_link_needs_razorpay_its_secret_and_something_to_pay(db, stub, secrets):
    kit.save_business(db, {"name": "Chai Co", "currency": "INR"})
    _, order = an_order(db)
    with pytest.raises(ValueError, match="Turn on Razorpay"):
        payments.create_request(db, "shop", "order", order["id"], secret_store=secrets)
    kit.save_payment_settings(db, {"provider": "razorpay", "razorpay_key_id": KEY_ID})
    with pytest.raises(ValueError, match="key secret"):
        payments.create_request(db, "shop", "order", order["id"], secret_store=secrets)
    with pytest.raises(LookupError):
        payments.create_request(
            db, "shop", "invoice", order["id"], secret_store=secrets
        )
    secrets.put(db, kit.RAZORPAY_KEY_SECRET, KEY_SECRET)
    shop.cancel(db, order["id"])
    with pytest.raises(ValueError, match="cancelled"):
        payments.create_request(db, "shop", "order", order["id"], secret_store=secrets)
    assert stub.calls == []


def test_the_webhook_address_names_the_business_and_is_not_a_page(db):
    path = payments.webhook_path(db)
    token = path.rsplit("/", 1)[-1]
    assert path.startswith("/api/payments/razorpay/webhook/") and len(token) >= 40
    assert payments.webhook_path(db) == path  # the same address every time
    assert payments.is_webhook_link(public.resolve(db, token))
    with pytest.raises(LookupError):
        public.view(db, token)
    assert json.dumps(payments.WEBHOOK_EVENTS)
