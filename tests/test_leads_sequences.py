"""Leads follow-up sequences (aira/leads/sequences.py): steps with waits, written into the Leads outbox when due,
stopped by a reply, an unsubscribe, a bounce or the do-not-contact list."""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import test_careers
from aira.leads import mail, sequences, store
from aira.suite import kit

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
STEPS = [
    {
        "delay_days": 0,
        "subject": "Websites for {company}",
        "body": "Hi {first_name}, a first note.",
    },
    {"delay_days": 3, "body": "Hi {first_name}, following up."},
    {"delay_days": 4, "subject": "Last note", "body": "One last note."},
]


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "leads.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure_schema(connection)
    yield connection
    connection.close()


def lead(db, name="Biz A", email="ana@biza.test", **extra):
    return store.insert_lead(
        db,
        {
            "name": name,
            "first_name": "Ana",
            "company": name,
            "email": email,
            "kind": "company",
            "source": "manual",
            **extra,
        },
        list_id=store.default_list(db)["id"],
    )["lead"]


def messages(db, lead_id):
    return [
        dict(row)
        for row in db.execute(
            "SELECT * FROM lead_messages WHERE lead_id = ? ORDER BY created_at, rowid",
            (lead_id,),
        )
    ]


def send(db, message_id, at):
    db.execute(
        "UPDATE lead_messages SET status = 'sent', sent_at = ? WHERE id = ?",
        (at.isoformat(timespec="seconds"), message_id),
    )
    db.commit()


def test_a_sequence_needs_real_steps():
    with pytest.raises(ValueError, match="at least one step"):
        sequences.clean_steps([])
    with pytest.raises(ValueError, match="subject"):
        sequences.clean_steps([{"delay_days": 0, "body": "x"}])
    with pytest.raises(ValueError, match="at least a day"):
        sequences.clean_steps(
            [{"subject": "a", "body": "x"}, {"delay_days": 0, "body": "y"}]
        )
    with pytest.raises(ValueError, match="at most"):
        sequences.clean_steps(
            [{"subject": "a", "body": "x"}] + [{"delay_days": 1, "body": "y"}] * 10
        )


def test_steps_are_written_when_due_counting_from_when_the_one_before_was_sent(db):
    ana = lead(db)
    sequence = sequences.save_sequence(db, {"name": "Websites", "steps": STEPS})
    assert sequence["review"] is True
    no_email = lead(db, "Biz B", email="")
    outcome = sequences.enroll(
        db, sequence["id"], [ana["id"], no_email["id"], ana["id"]], now=START
    )
    assert (
        outcome["enrolled"] == 1
        and outcome["skipped"][0]["reason"] == "No email address."
    )
    assert (
        sequences.enroll(db, sequence["id"], [ana["id"]], now=START)["skipped"][0][
            "reason"
        ]
        == "Already in this sequence."
    )

    assert sequences.tick(db, now=START)["written"] == 1
    [first] = messages(db, ana["id"])
    assert first["status"] == "draft" and first["subject"] == "Websites for Biz A"
    assert (
        first["body"] == "Hi Ana, a first note."
        and first["campaign"] == f"sequence:{sequence['id']}:1"
    )
    # step 2 waits for step 1 to be sent, then for its three days
    assert sequences.tick(db, now=START + timedelta(days=10))["written"] == 0
    send(db, first["id"], START + timedelta(days=1))
    assert sequences.tick(db, now=START + timedelta(days=3))["written"] == 0
    assert sequences.tick(db, now=START + timedelta(days=4, minutes=1))["written"] == 1
    second = messages(db, ana["id"])[-1]
    assert (
        second["subject"] == "Re: Websites for Biz A"
        and "following up" in second["body"]
    )
    send(db, second["id"], START + timedelta(days=5))
    assert sequences.tick(db, now=START + timedelta(days=9, hours=1))["written"] == 1
    send(db, messages(db, ana["id"])[-1]["id"], START + timedelta(days=9, hours=2))
    assert sequences.tick(db, now=START + timedelta(days=20))["finished"] == 1
    detail = sequences.sequence_detail(db, sequence["id"])
    assert detail["sequence"]["counts"]["finished"] == 1


def test_without_review_each_step_is_approved_as_it_is_written(db):
    ana = lead(db)
    sequence = sequences.save_sequence(
        db, {"name": "Quick", "steps": STEPS[:1], "review": False}
    )
    sequences.enroll(db, sequence["id"], [ana["id"]], now=START)
    sequences.tick(db, now=START)
    [first] = messages(db, ana["id"])
    assert first["status"] == "approved" and first["approved_at"]


def test_a_reply_stops_the_sequence_and_withdraws_what_was_not_sent(db):
    ana = lead(db)
    sequence = sequences.save_sequence(db, {"name": "Websites", "steps": STEPS})
    sequences.enroll(db, sequence["id"], [ana["id"]], now=START)
    sequences.tick(db, now=START)
    first = messages(db, ana["id"])[0]
    send(db, first["id"], START)
    db.execute(
        "UPDATE lead_messages SET message_id = '<one@me>' WHERE id = ?", (first["id"],)
    )
    sequences.tick(db, now=START + timedelta(days=4))
    assert messages(db, ana["id"])[-1]["status"] == "draft"
    assert sequences.record_reply(
        db, "someone.else@biza.test", ["<one@me>"]
    ).startswith("reply from")
    assert store.get_lead(db, ana["id"])["status"] == "replied"
    assert messages(db, ana["id"])[-1]["status"] == "rejected"
    [enrollment] = sequences.sequence_detail(db, sequence["id"])["enrollments"]
    assert enrollment["status"] == "stopped" and enrollment["stop_reason"] == "replied"
    assert sequences.record_reply(db, "stranger@nowhere.test") == ""


def test_a_bounce_or_the_do_not_contact_list_stops_it(db):
    ana, bo = lead(db), lead(db, "Biz B", email="bo@bizb.test")
    sequence = sequences.save_sequence(db, {"name": "Websites", "steps": STEPS})
    sequences.enroll(db, sequence["id"], [ana["id"], bo["id"]], now=START)
    assert mail.record_bounce(db, "ana@biza.test", "5.1.1 user unknown")
    mail.add_suppression(db, "bizb.test", "Asked us to stop")
    result = sequences.tick(db, now=START)
    assert result == {"written": 0, "stopped": 2, "finished": 0}
    later = lead(db, "Biz C", email="cy@bizb.test")
    skipped = sequences.enroll(db, sequence["id"], [later["id"]], now=START)["skipped"]
    assert "do-not-contact" in skipped[0]["reason"]


def test_an_unsubscribe_stops_every_sequence_to_that_address(db):
    ana = lead(db)
    for name in ("One", "Two"):
        made = sequences.save_sequence(db, {"name": name, "steps": STEPS})
        sequences.enroll(db, made["id"], [ana["id"]], now=START)
    assert sequences.stop_for_address(db, "ANA@biza.test", "unsubscribed") == 2


def test_a_database_without_leads_is_left_alone(tmp_path):
    connection = sqlite3.connect(tmp_path / "empty.sqlite3")
    connection.row_factory = sqlite3.Row
    assert sequences.tick(connection) == {"written": 0, "stopped": 0, "finished": 0}
    assert sequences.record_reply(connection, "a@b.example") == ""
    assert sequences.stop_for_address(connection, "a@b.example", "bounced") == 0
    assert connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def test_the_sequence_routes_on_the_local_build(env, api):
    kit.forget_schema()
    imported = api.post(
        "/api/leads/items/import-csv",
        {"csv": "Name,Company,Email,Website\nBiz A,Biz A,a@biza.test,biza.test\n"},
        expect=201,
    )
    assert imported
    lead_id = api.get("/api/leads/items")["leads"][0]["id"]
    made = api.post(
        "/api/leads/sequences", {"name": "Websites", "steps": STEPS}, expect=201
    )["sequence"]
    assert (
        api.call("POST", "/api/leads/sequences", {"name": "Empty", "steps": []})[0]
        == 400
    )
    assert (
        api.post(f"/api/leads/sequences/{made['id']}/enroll", {"lead_ids": [lead_id]})[
            "enrolled"
        ]
        == 1
    )
    listed = api.get("/api/leads/sequences")["sequences"]
    assert listed[0]["counts"]["active"] == 1
    paused = api.call(
        "PATCH", f"/api/leads/sequences/{made['id']}", {"status": "paused"}
    )[1]
    assert paused["sequence"]["status"] == "paused"
    detail = api.get(f"/api/leads/sequences/{made['id']}")
    assert detail["enrollments"][0]["lead_id"] == lead_id
    assert (
        api.post(f"/api/leads/sequences/{made['id']}/stop", {"lead_ids": [lead_id]})[
            "stopped"
        ]
        == 1
    )
    assert api.call("DELETE", f"/api/leads/sequences/{made['id']}")[0] == 200
    assert api.get("/api/leads/sequences")["sequences"] == []
