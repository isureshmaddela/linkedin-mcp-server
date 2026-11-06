"""The ledger and the shared money rules (aira/suite/kit.py): totals per currency, months in the business's time
zone, refunds as negative lines in their own month, GST and tax-inclusive prices, the public address."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aira import suite
from aira.suite import kit


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "ledger.sqlite3")
    connection.row_factory = sqlite3.Row
    kit.ensure_core(connection)
    yield connection
    connection.close()


def pay(db, source_id, cents, code, at, app="shop"):
    kit.record_revenue(
        db,
        app,
        source_type="order",
        source_id=source_id,
        amount_cents=cents,
        currency_code=code,
        label=f"Order {source_id}",
        at=at,
    )


def test_totals_never_add_currencies_together(db):
    kit.save_business(db, {"name": "Chai Co", "currency": "INR"})
    now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    pay(db, "a", 50000, "INR", "2026-11-02T10:00:00Z")
    pay(db, "b", 2000, "USD", "2026-11-03T10:00:00Z")
    pay(db, "c", 1500, "EUR", "2026-10-03T10:00:00Z", app="events")
    totals = kit.revenue(db, moment=now)
    assert (
        totals["currency"] == "INR"
        and totals["this_month_cents"] == 50000
        and totals["total_cents"] == 50000
    )
    assert totals["currencies"] == ["INR", "EUR", "USD"]
    assert totals["by_currency"]["USD"]["this_month_cents"] == 2000
    assert totals["by_currency"]["EUR"]["last_month_cents"] == 1500 and totals[
        "by_currency"
    ]["EUR"]["by_app"] == {"events": 1500}
    assert totals["payments"] == 1
    assert (
        kit.revenue(db, app="events", moment=now)["by_currency"]["EUR"]["total_cents"]
        == 1500
    )
    assert kit.revenue(db, app="events", moment=now)["total_cents"] == 0


def test_months_are_the_business_s_months(db):
    kit.save_business(
        db, {"name": "Chai Co", "currency": "INR", "timezone": "Asia/Kolkata"}
    )
    # 31 October 20:00 UTC is already 1 November in India
    pay(db, "late", 10000, "INR", "2026-10-31T20:00:00Z")
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    totals = kit.revenue(db, moment=now)
    assert totals["this_month_cents"] == 10000 and totals["last_month_cents"] == 0
    kit.save_business(db, {"timezone": "UTC"})
    totals = kit.revenue(db, moment=now)
    assert totals["this_month_cents"] == 0 and totals["last_month_cents"] == 10000
    kit.save_business(db, {"timezone": "UTC-05:00"})
    assert (
        kit.revenue(db, moment=datetime(2026, 11, 1, 2, tzinfo=timezone.utc))[
            "by_month"
        ][-1]["month"]
        == "2026-10"
    )


def test_zones_work_without_a_time_zone_database():
    assert kit.zone_info("Asia/Kolkata").utcoffset(datetime(2026, 1, 1)) == timedelta(
        hours=5, minutes=30
    )
    assert kit.zone_info("UTC+05:45").utcoffset(None) == timedelta(hours=5, minutes=45)
    assert kit.zone_info("-03:00").utcoffset(None) == timedelta(hours=-3)
    assert kit.zone_info("Nowhere/Land").utcoffset(None) == timedelta(0)


def test_a_refund_is_a_negative_line_in_the_refund_month(db):
    kit.save_business(db, {"name": "Chai Co", "currency": "INR"})
    pay(db, "a", 50000, "INR", "2026-10-10T10:00:00Z")
    kit.void_revenue(
        db,
        "shop",
        "order",
        "a",
        refund_cents=20000,
        refund_id="r1",
        at="2026-11-02T10:00:00Z",
    )
    kit.void_revenue(
        db,
        "shop",
        "order",
        "a",
        refund_cents=20000,
        refund_id="r1",
        at="2026-11-02T10:00:00Z",
    )  # once
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    totals = kit.revenue(db, moment=now)
    assert totals["last_month_cents"] == 50000  # October keeps its sale
    assert totals["this_month_cents"] == -20000 and totals["refunded_cents"] == 20000
    assert totals["total_cents"] == 30000 and totals["payments"] == 1
    assert kit.refunded_cents(db, "shop", "order", "a") == 20000
    refund = next(line for line in kit.revenue_lines(db) if line["kind"] == "refund")
    assert (
        refund["label"] == "Refund: Order a"
        and refund["currency"] == "INR"
        and refund["at"].startswith("2026-11")
    )
    # a refund of nothing recorded does nothing; marking unpaid still voids the payment line only
    kit.void_revenue(db, "shop", "order", "missing", refund_cents=100)
    kit.void_revenue(db, "shop", "order", "a")
    assert kit.revenue(db, moment=now)["total_cents"] == -20000
    with pytest.raises(ValueError):
        kit.record_refund(
            db, "shop", source_type="order", source_id="a", amount_cents=0
        )


def test_an_old_ledger_gains_its_new_columns(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "old.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute(
        kit.CORE_SCHEMA[4]
    )  # suite_revenue as it was made before refunds
    connection.execute(
        "INSERT INTO suite_revenue (id, app, source_type, source_id, amount_cents, currency, at) VALUES ('1', 'shop', 'order', 'x', 900, 'USD', '2026-01-01T00:00:00Z')"
    )
    connection.commit()
    assert kit.revenue(connection)["total_cents"] == 900
    assert kit.revenue_lines(connection)[0]["kind"] == "payment"
    connection.close()


# ---------------------------------------------------------------------------
# taxes
# ---------------------------------------------------------------------------

GST_PROFILE = {
    "tax_mode": "gst",
    "currency": "INR",
    "tax_id": "29AAGCB7383J1Z4",
    "state_code": "29",
    "tax_rate_bp": 1200,
}


def test_tax_breakdowns_flat_inclusive_and_gst():
    flat = kit.tax_breakdown({"tax_rate_bp": 750, "tax_label": "VAT"}, 10000)
    assert flat["added_cents"] == 750 and flat["lines"] == [
        {"label": "VAT", "rate_bp": 750, "cents": 750}
    ]
    inclusive = kit.tax_breakdown(
        {"tax_rate_bp": 1800, "prices_include_tax": True}, 11800
    )
    assert (
        inclusive["tax_cents"] == 1800
        and inclusive["added_cents"] == 0
        and inclusive["net_cents"] == 10000
    )
    intra = kit.tax_breakdown(GST_PROFILE, 10001)
    assert intra["supply"] == "intra" and [
        line["cents"] for line in intra["lines"]
    ] == [600, 600]
    odd = kit.tax_breakdown({**GST_PROFILE, "tax_rate_bp": 500}, 10010)
    assert [line["cents"] for line in odd["lines"]] == [251, 250] and odd[
        "tax_cents"
    ] == 501
    inter = kit.tax_breakdown(GST_PROFILE, 10000, customer_state="33")
    assert inter["supply"] == "inter" and inter["lines"] == [
        {"label": "IGST", "rate_bp": 1200, "cents": 1200}
    ]
    assert kit.tax_breakdown(GST_PROFILE, 10000, exempt=True)["tax_cents"] == 0
    # a GST profile in another currency, or with a broken GSTIN, is a flat rate
    assert kit.tax_breakdown({**GST_PROFILE, "currency": "USD"}, 10000)["gst"] is False
    assert (
        kit.tax_breakdown({**GST_PROFILE, "tax_id": "29AAGCB7383J1Z5"}, 10000)["gst"]
        is False
    )


def test_gstin_state_and_hsn_rules():
    assert kit.valid_gstin("27AAPFU0939F1ZV") and kit.valid_gstin(" 29aagcb7383j1z4 ")
    assert (
        not kit.valid_gstin("27AAPFU0939F1ZW")
        and not kit.valid_gstin("99AAPFU0939F1ZV")
        and not kit.valid_gstin("")
    )
    assert (
        kit.state_code("Tamil Nadu") == "33"
        and kit.state_code("7") == "07"
        and kit.state_code("") == ""
    )
    with pytest.raises(ValueError):
        kit.state_code("Narnia")
    assert kit.hsn_code("9983") == "9983" and kit.hsn_code("") == ""
    with pytest.raises(ValueError):
        kit.hsn_code("12")


def test_the_business_profile_keeps_gst_settings(db):
    saved = kit.save_business(
        db,
        {
            "currency": "INR",
            "tax_mode": "gst",
            "tax_id": "27aapfu0939f1zv",
            "tax_rate_bp": 1800,
        },
    )
    assert saved["tax_id"] == "27AAPFU0939F1ZV" and saved["state_code"] == "27"
    with pytest.raises(ValueError, match="state"):
        kit.save_business(db, {"state_code": "98"})
    assert (
        kit.save_business(db, {"tax_mode": "flat", "currency": "USD"})["tax_mode"]
        == "flat"
    )


# ---------------------------------------------------------------------------
# the public address
# ---------------------------------------------------------------------------


def test_public_address_rules_and_links(db, monkeypatch):
    registered = []
    monkeypatch.setattr(kit, "_public_host_hook", registered.append)
    monkeypatch.setattr(kit, "_address_source", None)
    monkeypatch.setattr(suite, "_public_origin", None)
    for bad in (
        "http://a.example",
        "https://a.example/x",
        "https://10.0.0.1",
        "https://localhost",
        "https://intranet",
        "https://user@a.example",
    ):
        with pytest.raises(ValueError):
            kit.save_public_address(db, bad)
    assert (
        kit.save_public_address(db, "HTTPS://Shop.Example.com/")
        == "https://shop.example.com"
    )
    assert (
        kit.save_public_address(db, "https://shop.example.com:8443")
        == "https://shop.example.com:8443"
    )
    assert registered[-1] == "https://shop.example.com:8443"
    assert (
        kit.public_url("/p/x", {"base_url": "http://127.0.0.1:9"})
        == "http://127.0.0.1:9/p/x"
    )
    monkeypatch.setattr(kit, "_address_source", lambda: "https://shop.example.com/")
    assert (
        kit.public_url("/p/x", {"base_url": "http://127.0.0.1:9"})
        == "https://shop.example.com/p/x"
    )
    monkeypatch.setattr(suite, "_public_origin", lambda: "https://cloud.example.com")
    assert kit.public_url("/p/x") == "https://cloud.example.com/p/x"  # hosted wins
    assert kit.is_loopback_url("http://127.0.0.1:8765/p/x") and kit.is_loopback_url(
        "http://localhost/x"
    )
    assert not kit.is_loopback_url("https://shop.example.com/p/x")
    kit.save_public_address(db, "")
    assert registered[-1] == "" and kit.public_address(db) == ""
