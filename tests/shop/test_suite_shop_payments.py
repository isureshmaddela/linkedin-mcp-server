"""Shop and anonymous orders (P0-13): unpaid orders lapse and give their stock back, caps per contact and visitor,
the storefront's bot checks, confirmations only once paid (or a few a day by hand), GST on orders, booking deposits."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from razorpay_payments_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub
from test_payments_razorpay import Secrets, deliver

from aira.payments import apps as _apps  # noqa: F401
from aira.payments import razorpay
from aira.payments import service as payments
from aira.suite import background, kit, public
from aira.suite.shop import service, store


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "shop.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(connection, {"name": "Chai Co", "currency": "INR"})
    yield connection
    connection.close()
    public.reset_index()


@pytest.fixture
def online(db, monkeypatch):
    server = RazorpayStub().start()
    monkeypatch.setenv(razorpay.API_BASE_ENV, server.base)
    secrets = Secrets()
    secrets.put(db, kit.RAZORPAY_KEY_SECRET, KEY_SECRET)
    secrets.put(db, kit.RAZORPAY_WEBHOOK_SECRET, WEBHOOK_SECRET)
    kit.save_payment_settings(
        db,
        {"provider": "razorpay", "razorpay_key_id": KEY_ID, "key_secret_saved": True},
    )
    with payments.secrets(secrets):
        yield server, secrets
    server.stop()


def tea(db, stock=5, **values):
    return service.create_product(
        db,
        {
            "name": "Tea",
            "price_cents": 20000,
            "status": "active",
            "track_stock": True,
            "stock": stock,
            **values,
        },
    )


def place(
    db, product, *, email="ana@example.com", phone="", quantity=1, visitor=None, **raw
):
    return service.place_order(
        db,
        {
            "items": [{"product_id": product["id"], "quantity": quantity}],
            "name": "Ana",
            "email": email,
            "phone": phone,
            **raw,
        },
        visitor=visitor,
    )


# ---------------------------------------------------------------------------
# lapsing unpaid orders
# ---------------------------------------------------------------------------


def test_an_unpaid_order_paid_by_hand_lapses_after_48_hours_and_gives_its_stock_back(
    db,
):
    mug = tea(db)
    order = place(db, mug, quantity=2)
    assert not order["pay_online"]
    assert kit.parse_time(order["expires_at"]) - kit.utcnow() > timedelta(hours=47)
    assert store.get_product(db, mug["id"])["stock"] == 3
    assert service.expire_unpaid(db) == 0
    later = kit.utcnow() + timedelta(hours=49)
    assert service.expire_unpaid(db, moment=later) == 1
    lapsed = store.get_order(db, order["id"])
    assert (
        lapsed["fulfilment_status"] == "cancelled"
        and lapsed["expired_at"]
        and lapsed["restocked"]
    )
    assert "Not paid in time" in lapsed["history"][-1]["note"]
    assert store.get_product(db, mug["id"])["stock"] == 5
    assert service.expire_unpaid(db, moment=later) == 0  # once


def test_paid_says_paid_and_shop_entered_orders_never_lapse(db):
    mug = tea(db)
    paid = place(db, mug)
    service.mark_paid(db, paid["id"])
    told = place(db, mug)
    service.customer_says_paid(db, told["id"], {"reference": "UTR123"})
    entered = service.place_order(
        db,
        {"items": [{"product_id": mug["id"]}], "name": "Walk In", "phone": "12345"},
        source="manual",
    )
    assert entered["expires_at"] == ""
    assert service.expire_unpaid(db, moment=kit.utcnow() + timedelta(days=5)) == 0
    assert store.get_product(db, mug["id"])["stock"] == 2


def test_an_online_order_lapses_after_30_minutes_and_a_grace_and_its_link_closes(
    db, online
):
    mug = tea(db)
    order = place(db, mug)
    assert order["pay_online"]
    assert (
        timedelta(minutes=29)
        < kit.parse_time(order["expires_at"]) - kit.utcnow()
        <= timedelta(minutes=30)
    )
    made = payments.create_request(db, "shop", "order", order["id"])
    sent = online[0].calls_to("POST", "/payment_links")[0]["body"]
    assert (
        abs(sent["expire_by"] - int(kit.parse_time(order["expires_at"]).timestamp()))
        <= 1
    )
    assert (
        service.expire_unpaid(db, moment=kit.utcnow() + timedelta(minutes=35)) == 0
    )  # the grace
    assert service.expire_unpaid(db, moment=kit.utcnow() + timedelta(minutes=45)) == 1
    assert payments.get(db, made["id"])["status"] == "expired"
    assert store.get_product(db, mug["id"])["stock"] == 5


def test_a_payment_that_lands_after_the_order_lapsed_takes_the_stock_again(db, online):
    stub, secrets = online
    mug = tea(db, stock=1)
    order = place(db, mug)
    made = payments.create_request(db, "shop", "order", order["id"])
    service.expire_unpaid(db, moment=kit.utcnow() + timedelta(hours=1))
    assert store.get_product(db, mug["id"])["stock"] == 1
    payment = stub.pay(made["provider_ref"])
    assert (
        deliver(
            db,
            secrets,
            *stub.webhook(
                "payment_link.paid", link_id=made["provider_ref"], payment=payment
            ),
        )["outcome"]
        == "paid"
    )
    revived = store.get_order(db, order["id"])
    assert (
        revived["payment_status"] == "paid" and revived["fulfilment_status"] == "placed"
    )
    assert store.get_product(db, mug["id"])["stock"] == 0
    assert (
        payments.get(db, made["id"])["problem"]
        == "Paid after the link was closed here."
    )


def test_a_late_payment_for_stock_sold_meanwhile_is_shown_to_the_business(db, online):
    stub, secrets = online
    mug = tea(db, stock=1)
    order = place(db, mug)
    made = payments.create_request(db, "shop", "order", order["id"])
    service.expire_unpaid(db, moment=kit.utcnow() + timedelta(hours=1))
    place(db, mug, email="bo@example.com")  # someone else bought the last one
    payment = stub.pay(made["provider_ref"])
    assert (
        deliver(
            db,
            secrets,
            *stub.webhook(
                "payment_link.paid", link_id=made["provider_ref"], payment=payment
            ),
        )["outcome"]
        == "needs_attention"
    )
    request = payments.get(db, made["id"])
    assert request["status"] == "mismatch" and "lapsed" in request["problem"]
    assert store.get_order(db, order["id"])["payment_status"] == "unpaid"


def test_the_background_task_lapses_orders(db):
    import aira.suite.shop.shop_routes  # noqa: F401 - registers the task

    mug = tea(db)
    order = place(db, mug)
    db.execute(
        "UPDATE sh_orders SET expires_at = ? WHERE id = ?",
        (kit.iso(kit.utcnow() - timedelta(minutes=1)), order["id"]),
    )
    db.commit()
    assert background.TASKS["shop"](db) == {"lapsed": 1}


def test_expiry_settings_are_checked(db):
    with pytest.raises(ValueError, match="16 minutes"):
        store.save_settings(db, {"unpaid_expiry_online_minutes": 5})
    saved = store.save_settings(
        db, {"unpaid_expiry_online_minutes": 0, "unpaid_expiry_manual_hours": 0}
    )
    assert saved["unpaid_expiry_manual_hours"] == 0
    assert place(db, tea(db))["expires_at"] == ""  # 0 = never


# ---------------------------------------------------------------------------
# caps and bot checks
# ---------------------------------------------------------------------------


def test_one_email_phone_or_visitor_places_only_so_many_orders_an_hour(db):
    store.save_settings(
        db, {"max_orders_per_contact_hour": 2, "max_orders_per_visitor_hour": 3}
    )
    mug = tea(db, stock=100)
    visitor = {"client": "203.0.113.9"}
    place(db, mug, visitor=visitor)
    place(db, mug, visitor=visitor)
    with pytest.raises(PermissionError, match="contact details"):
        place(db, mug, visitor=visitor)
    place(db, mug, email="", phone="+91 90000 00001", visitor=visitor)
    with pytest.raises(PermissionError, match="this connection"):
        place(db, mug, email="new@example.com", visitor=visitor)
    place(
        db, mug, email="new@example.com", visitor={"client": "198.51.100.7"}
    )  # another visitor
    place(db, mug)  # the shop's own entry (no visitor) is not capped
    assert store.get_order(
        db, place(db, mug, email="x@example.com", visitor={"client": "192.0.2.1"})["id"]
    )["client_key"] not in ("", "192.0.2.1")


def test_the_storefront_form_refuses_a_filled_honeypot_a_missing_token_and_a_rush(
    db, monkeypatch
):
    with pytest.raises(ValueError, match="could not take"):
        service.check_form(
            db, {"website": "http://spam.example", "form_token": service.form_token(db)}
        )
    with pytest.raises(ValueError, match="expired"):
        service.check_form(db, {})
    with pytest.raises(ValueError, match="expired"):
        service.check_form(db, {"form_token": "1700000000.deadbeef"})
    with pytest.raises(ValueError, match="quick"):
        service.check_form(db, {"form_token": service.form_token(db)})
    service.check_form(
        db,
        {
            "form_token": service.form_token(
                db, moment=kit.utcnow() - timedelta(seconds=5)
            )
        },
    )
    with pytest.raises(ValueError, match="expired"):
        service.check_form(
            db,
            {
                "form_token": service.form_token(
                    db, moment=kit.utcnow() - timedelta(days=2)
                )
            },
        )
    assert service.storefront(db)["form_token"].count(".") == 1


# ---------------------------------------------------------------------------
# confirmations
# ---------------------------------------------------------------------------


def confirmations(db, address="ana@example.com"):
    return kit.count(
        db,
        "SELECT COUNT(*) FROM suite_outbox WHERE kind = 'order_confirmation' AND recipient = ?",
        (address,),
    )


def test_an_online_order_is_confirmed_only_once_it_is_paid(db, online):
    stub, secrets = online
    order = place(db, tea(db))
    assert confirmations(db) == 0
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = stub.pay(made["provider_ref"])
    deliver(
        db,
        secrets,
        *stub.webhook(
            "payment_link.paid", link_id=made["provider_ref"], payment=payment
        ),
    )
    receipt = kit.fetch_one(
        db, "SELECT * FROM suite_outbox WHERE kind = 'payment_receipt'"
    )
    assert receipt and "1 x Tea" in receipt["body"]
    assert confirmations(db) == 0


def test_an_order_paid_by_hand_is_confirmed_at_most_three_times_a_day_per_address(db):
    mug = tea(db, stock=50)
    for _ in range(5):
        place(db, mug)
    assert confirmations(db) == service.CONFIRMATIONS_PER_ADDRESS_DAY
    place(db, mug, email="bo@example.com")
    assert confirmations(db, "bo@example.com") == 1


# ---------------------------------------------------------------------------
# GST and tax-inclusive prices
# ---------------------------------------------------------------------------

GSTIN_KA = "29AAGCB7383J1Z4"


def test_gst_splits_inside_the_state_and_is_igst_to_another(db):
    kit.save_business(db, {"tax_mode": "gst", "tax_id": GSTIN_KA, "tax_rate_bp": 1800})
    profile = kit.business(db)
    assert profile["state_code"] == "29"
    mug = tea(db, hsn_sac="0902")
    inside = service.price(db, [{"product_id": mug["id"]}], customer_state="Karnataka")
    assert inside["tax_cents"] == 3600 and inside["total_cents"] == 23600
    assert [(line["label"], line["cents"]) for line in inside["tax_lines"]] == [
        ("CGST", 1800),
        ("SGST", 1800),
    ]
    assert inside["lines"][0]["hsn_sac"] == "0902"
    other = service.price(db, [{"product_id": mug["id"]}], customer_state="27")
    assert [(line["label"], line["cents"]) for line in other["tax_lines"]] == [
        ("IGST", 3600)
    ]
    assert other["customer_state"] == "27"
    with pytest.raises(ValueError, match="not an Indian state"):
        service.price(db, [{"product_id": mug["id"]}], customer_state="Atlantis")
    order = place(db, mug, customer_state="27")
    saved = store.get_order(db, order["id"])
    assert saved["customer_state"] == "27" and saved["tax_lines"][0]["label"] == "IGST"
    shown = service.public_order(saved)
    assert (
        shown["tax_lines"][0]["cents"] == 3600
        and shown["items"][0]["hsn_sac"] == "0902"
    )
    assert (
        service.storefront(db)["home_state"] == "29"
        and len(service.storefront(db)["gst_states"]) > 30
    )


def test_tax_inclusive_prices_add_nothing_and_flat_tax_still_works(db):
    kit.save_business(db, {"tax_rate_bp": 1800, "prices_include_tax": True})
    mug = tea(db)
    quote = service.price(db, [{"product_id": mug["id"]}])
    assert (
        quote["total_cents"] == 20000
        and quote["tax_cents"] == 3051
        and quote["prices_include_tax"]
    )
    kit.save_business(
        db, {"prices_include_tax": False, "tax_rate_bp": 750, "tax_label": "VAT"}
    )
    flat = service.price(db, [{"product_id": mug["id"]}])
    assert flat["tax_cents"] == 1500 and flat["total_cents"] == 21500
    assert flat["tax_lines"] == [{"label": "VAT", "rate_bp": 750, "cents": 1500}]


def test_gst_needs_a_real_gstin_and_rupees(db):
    with pytest.raises(ValueError, match="GSTIN"):
        kit.save_business(
            db, {"tax_mode": "gst", "tax_id": "29AAGCB7383J1Z5"}
        )  # wrong check character
    kit.save_business(db, {"currency": "USD"})
    with pytest.raises(ValueError, match="INR"):
        kit.save_business(db, {"tax_mode": "gst", "tax_id": GSTIN_KA})
    with pytest.raises(ValueError, match="HSN"):
        tea(db, hsn_sac="12ab")


# ---------------------------------------------------------------------------
# booking deposits
# ---------------------------------------------------------------------------


def test_a_booking_deposit_then_the_rest_through_payment_links(db, online, monkeypatch):
    from aira.suite.bookings import service as bookings
    from aira.suite.bookings import store as bookings_store

    stub, secrets = online
    bookings_store.ensure(db)
    monkeypatch.setattr(bookings, "_message", lambda *args, **kwargs: None)
    booking_id = kit.new_id()
    start = kit.iso(kit.utcnow() + timedelta(days=3))
    db.execute(
        """INSERT INTO bk_bookings (id, reference, service_id, service_name, staff_id, staff_name, customer_id, customer_name,
        customer_email, customer_phone, start_at, end_at, busy_until, status, price_cents, deposit_cents, source, note,
        created_at, updated_at) VALUES (?, 'BK-TEST1', 's', 'Haircut', 'p', 'Mira', 'c', 'Ana', 'ana@example.com', '', ?, ?, ?,
        'booked', 100000, 30000, 'public', '', ?, ?)""",
        (booking_id, start, start, start, kit.now(), kit.now()),
    )
    db.commit()
    first = payments.create_request(db, "bookings", "booking", booking_id)
    assert first["amount_cents"] == 30000 and first["description"].startswith(
        "Deposit:"
    )
    payment = stub.pay(first["provider_ref"])
    deliver(
        db,
        secrets,
        *stub.webhook(
            "payment_link.paid", link_id=first["provider_ref"], payment=payment
        ),
    )
    after = bookings.get_booking(db, booking_id)
    assert after["paid_cents"] == 30000 and after["payment_kind"] == "deposit"
    rest = payments.create_request(db, "bookings", "booking", booking_id)
    assert rest["amount_cents"] == 70000
    payment = stub.pay(rest["provider_ref"])
    deliver(
        db,
        secrets,
        *stub.webhook(
            "payment_link.paid", link_id=rest["provider_ref"], payment=payment
        ),
    )
    done = bookings.get_booking(db, booking_id)
    assert done["paid_cents"] == 100000 and done["payment_kind"] == "full"
    assert kit.revenue(db)["total_cents"] == 100000
    with pytest.raises(ValueError, match="Nothing is owed"):
        payments.create_request(db, "bookings", "booking", booking_id)
