"""Stocks: paid ideas carry the analyst's SEBI registration (or "Not SEBI-registered") and what they hold; quotes
are fetched with an honest User-Agent and labelled delayed, for personal use; a guardian can turn Stocks off."""

from __future__ import annotations

import pytest
import test_suite_stocks

from aira.suite import life
from aira.suite.stocks import market, service

_offline = test_suite_stocks._offline  # shared fixtures
db = test_suite_stocks.db
people = test_suite_stocks.people


def _analyst(db, pat, **extra):
    return service.save_analyst(
        db,
        pat,
        {
            "display_name": "Pat Picks",
            "price": "9.99",
            "currency": "USD",
            "payment_link": "https://pay.example/pat",
            **extra,
        },
    )


def _paid(db, pat, **extra):
    return service.create_idea(
        db,
        pat,
        {
            "symbol": "AAPL",
            "side": "buy",
            "target": "110",
            "stop": "95",
            "access": "paid",
            **extra,
        },
    )


def test_paid_ideas_disclose_registration_and_holdings(db, people):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=100)
    found = _analyst(db, pat)
    assert found["sebi_number"] == "" and found["registration"] == "Not SEBI-registered"
    # a paid idea must say what the analyst holds
    with pytest.raises(ValueError, match="hold this stock"):
        _paid(db, pat)
    with pytest.raises(ValueError):
        _paid(db, pat, holding="maybe")
    unregistered = _paid(db, pat, holding="self", holding_note="Bought in 2024")
    assert unregistered["disclosure"] == {
        "sebi_number": "",
        "registration": "Not SEBI-registered",
        "holding": "self",
        "holding_text": "The analyst holds this stock.",
        "note": "Bought in 2024",
    }
    with pytest.raises(ValueError, match="INH"):
        _analyst(db, pat, sebi_number="12345")
    found = _analyst(db, pat, sebi_number="inh 000012345")
    assert found["registration"] == "SEBI research analyst INH000012345"
    registered = _paid(db, pat, holding="none")
    assert (
        registered["disclosure"]["registration"] == "SEBI research analyst INH000012345"
    )
    # an idea keeps what was true when it was published
    assert {
        i["id"]: i["disclosure"]["sebi_number"] for i in service.my_ideas(db, pat)
    } == {
        unregistered["id"]: "",
        registered["id"]: "INH000012345",
    }
    page = service.analyst_page(db, pat["id"])
    assert page["analyst"]["registration"] == "SEBI research analyst INH000012345"
    assert "delayed, for personal use" in page["disclaimer"]
    # a free idea may say nothing about holdings
    free = service.create_idea(
        db, pat, {"symbol": "AAPL", "side": "buy", "target": "110", "stop": "95"}
    )
    assert free["disclosure"]["holding_text"] == "Not disclosed."


def test_quotes_are_labelled_and_fetched_honestly(db, people):
    assert "Mozilla" not in market.USER_AGENT and "Chrome" not in market.USER_AGENT
    assert market.USER_AGENT.startswith("Monosage/")
    market.stub_set("AAPL", price=100, previous_close=100)
    quote = market.quotes(db, ["AAPL"])["AAPL"]
    assert (
        quote["delayed"] is True
        and quote["notice"] == "Prices are delayed, for personal use only."
    )


def test_a_guardian_can_turn_stocks_off(db, people):
    pat, mia = people["pat"], people["mia"]
    life.set_controls(db, pat, mia["id"], {"apps": {"stocks": False}})
    with pytest.raises(life.AppTurnedOff):
        life.require_app(db, life.get(db, mia["id"]), "stocks")
    # real money is never for under-18s, whatever the controls say
    with pytest.raises(PermissionError):
        service.require_adult(life.get(db, mia["id"]))
