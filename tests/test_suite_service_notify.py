"""Service tells the team (desk.notify_staff): new cases, new live chats and cases past their due time, by email
through the outbox to the assignee or the team, with a place for other channels (Web Push) to plug in."""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aira import suite
from aira.suite import kit, public
from aira.suite.service import desk, store


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    suite.load_apps()
    connection = sqlite3.connect(tmp_path / "notify.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(connection, {"name": "Glow", "email": "owner@glow.example"})
    yield connection
    connection.close()


def notices(db, kind):
    return [m for m in kit.outbox(db) if m["kind"] == f"staff_{kind}"]


def test_a_new_case_from_a_customer_tells_the_team_and_goes_out_without_approval(db):
    case = desk.create_case(
        db,
        {
            "subject": "Broken tap",
            "email": "ana@example.com",
            "name": "Ana",
            "message": "It drips.",
        },
        channel="web",
    )
    [notice] = notices(db, "new_case")
    assert (
        notice["recipient"] == "owner@glow.example" and notice["status"] == "approved"
    )
    assert (
        f"[{case['reference']}]" in notice["subject"] and "It drips." in notice["body"]
    )
    assert f"/#/service?open={case['id']}" in notice["body"]
    # a case a team member typed in themselves is no news to the team
    desk.create_case(db, {"subject": "Called in"}, channel="manual")
    assert len(notices(db, "new_case")) == 1


def test_the_assignee_hears_first_then_the_team_address(db):
    store.save_settings(db, {"notify_email": "support@glow.example"})
    case = desk.create_case(
        db, {"subject": "A", "email": "a@example.com"}, channel="web"
    )
    assert notices(db, "new_case")[0]["recipient"] == "support@glow.example"
    assert (
        desk.staff_address(db, {**case, "assignee": "kim@glow.example"})
        == "kim@glow.example"
    )
    assert desk.staff_address(db, {**case, "assignee": "Kim"}) == "support@glow.example"


def test_a_new_live_chat_tells_the_team(db):
    desk.start_chat(
        db, {"name": "Bo", "email": "bo@example.com", "message": "Are you open today?"}
    )
    [notice] = notices(db, "new_chat")
    assert "Bo" in notice["subject"] and "Are you open today?" in notice["body"]
    assert kit.expiry_of(notice)  # a chat notice is stale after a few hours


def test_an_overdue_case_tells_its_assignee_once(db):
    start = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    case = desk.create_case(
        db,
        {
            "subject": "Urgent",
            "email": "a@example.com",
            "priority": "urgent",
            "assignee": "kim@glow.example",
        },
        channel="web",
        now=start,
    )
    desk.tick(db, now=start + timedelta(minutes=30))
    assert notices(db, "sla_breach") == []
    desk.tick(db, now=start + timedelta(hours=2))
    [notice] = notices(db, "sla_breach")
    assert notice["recipient"] == "kim@glow.example" and "first reply" in notice["body"]
    assert case["reference"] in notice["subject"]
    desk.tick(db, now=start + timedelta(hours=3))
    assert len(notices(db, "sla_breach")) == 1  # once, when it turns overdue


def test_each_notice_can_be_turned_off(db):
    store.save_settings(db, {"notify_new_case": False, "notify_new_chat": False})
    desk.create_case(db, {"subject": "Quiet", "email": "a@example.com"}, channel="web")
    desk.start_chat(db, {"name": "Bo", "message": "Hi"})
    assert notices(db, "new_case") == [] and notices(db, "new_chat") == []
    assert store.settings(db)["notify_sla_breach"] is True
    with pytest.raises(ValueError, match="Unknown notice"):
        desk.notify_staff(db, "party", {})


def test_other_channels_get_every_notice(db, monkeypatch):
    seen = []
    monkeypatch.setitem(
        desk.STAFF_CHANNELS,
        "push",
        lambda connection, kind, case, notice: seen.append((kind, notice["to"])),
    )

    def broken(connection, kind, case, notice):
        raise RuntimeError("push service down")

    monkeypatch.setitem(desk.STAFF_CHANNELS, "broken", broken)
    desk.create_case(db, {"subject": "Hi", "email": "a@example.com"}, channel="web")
    assert seen == [("new_case", "owner@glow.example")]
    assert (
        len(notices(db, "new_case")) == 1
    )  # a failing channel does not stop the email
