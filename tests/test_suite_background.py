"""Work done without anyone looking (aira/suite/background.py): app tasks and messages that are due, both builds."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from cloud_support import build_org

from aira.leads import mail as transport
from aira.suite import background, kit


class Secrets:
    def __init__(self, password: str = "pw") -> None:
        self.password = password

    def get(self, connection, name):
        return self.password if name == kit.SMTP_PASSWORD_SECRET else None


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "bg.sqlite3")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def test_an_account_that_never_used_the_apps_is_left_alone(db):
    assert background.run(db, Secrets()) == {"ran": "nothing"}
    assert not background.uses_suite(db)


def test_tasks_run_and_due_mail_goes_out(db, monkeypatch):
    ran = []
    monkeypatch.setitem(
        background.TASKS, "planner", lambda connection: ran.append("planner") or 3
    )

    def broken(connection):
        raise RuntimeError("boom")

    monkeypatch.setitem(background.TASKS, "learn", broken)
    kit.save_email_settings(
        db, {"from_email": "me@home.example", "smtp_host": "smtp.example.com"}
    )
    later = kit.iso(kit.utcnow() + timedelta(hours=5))
    kit.queue_email(
        db,
        "bookings",
        "booking_reminder",
        to="a@example.com",
        subject="Tomorrow",
        body="See you",
    )
    kit.queue_email(
        db,
        "care",
        "missed_dose",
        to="carer@example.com",
        subject="Missed",
        body="Check in",
    )
    kit.queue_email(
        db,
        "bookings",
        "booking_reminder",
        to="b@example.com",
        subject="Later",
        body="Later",
        send_after=later,
    )
    # the reminder waits for approval; the caregiver's safety notice does not
    statuses = {m["recipient"]: m["status"] for m in kit.outbox(db)}
    assert statuses == {
        "a@example.com": "pending",
        "carer@example.com": "approved",
        "b@example.com": "pending",
    }
    for message in kit.outbox(db):
        if message["recipient"] == "b@example.com":
            kit.set_outbox_status(db, [message["id"]], "approved")
    sent = []
    monkeypatch.setattr(
        transport,
        "smtp_send",
        lambda settings, password, message: sent.append(message["To"]),
    )
    result = background.run(db, Secrets())
    assert (
        ran == ["planner"]
        and result["tasks"]["planner"] == 3
        and result["tasks"]["learn"] == "failed"
    )
    assert result["mail"] == {"sent": 1, "failed": 0} and sent == [
        "carer@example.com"
    ]  # the later one waits
    # without email set up nothing is attempted
    kit.save_email_settings(db, {"smtp_host": ""})
    assert "mail" not in background.run(db, Secrets())


def test_the_hosted_scheduler_queues_one_tick_per_organization_and_the_worker_runs_it(
    cloud, monkeypatch
):
    from aira_cloud.worker import HANDLERS

    org = build_org(cloud)
    counts = cloud.scheduler.enqueue_due()
    assert (
        counts["suite.tick"] == 0
    )  # no industry or life app, no organization database yet
    cloud.billing.subscribe_app(org.owner, "planner")
    with cloud.workspaces.bind_org_api(org.org_id):
        import auto_apply_assistant as local

        with local._connect() as connection:
            kit.ensure_core(connection)
    assert cloud.scheduler.enqueue_due()["suite.tick"] == 1
    assert (
        cloud.scheduler.enqueue_due()["suite.tick"] == 0
    )  # one per organization per five minutes
    ran = []
    monkeypatch.setitem(
        background.TASKS, "planner", lambda connection: ran.append("planner") or "ok"
    )
    monkeypatch.setitem(
        background.TASKS, "studio", lambda connection: ran.append("studio") or "ok"
    )
    result = HANDLERS["suite.tick"](cloud, {"org_id": org.org_id, "job_id": "j1"})
    assert ran == ["planner"] and result["tasks"] == {
        "planner": "ok"
    }  # Studio is not subscribed
