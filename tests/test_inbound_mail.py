"""Incoming email (aira/inbound_mail.py): the business's mailbox read over IMAP and routed to Service (new cases and
replies), Leads (replies stop sequences), Marketing (unsubscribe emails) and the do-not-contact list (bounces).

The mailbox is a stand-in IMAP server on loopback (``tests/imap_stub.py``); no real mail server is ever reached.
"""

# ruff: noqa: F811
from __future__ import annotations

import imaplib
import sqlite3
import ssl

import pytest
from imap_stub import ImapStub

import auto_apply_assistant as local
import test_careers
from aira import inbound_mail, suite
from aira.leads import mail as transport
from aira.leads import sequences
from aira.leads import store as leads_store
from aira.suite import background, kit, public
from aira.suite.marketing import service as marketing
from aira.suite.marketing import store as marketing_store
from aira.suite.service import desk
from aira.suite.service import store as service_store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

PASSWORD = "imap-Secret-1"


class Secrets:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, connection, name):
        return self.values.get(name)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    suite.load_apps()
    connection = sqlite3.connect(tmp_path / "inbound.sqlite3")
    connection.row_factory = sqlite3.Row
    marketing_store.ensure(connection)
    service_store.ensure(connection)
    leads_store.ensure_schema(connection)
    kit.save_business(connection, {"name": "Glow", "email": "owner@glow.example"})
    kit.save_email_settings(
        connection,
        {"from_email": "hello@glow.example", "smtp_host": "smtp.glow.example"},
    )
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


@pytest.fixture
def imap():
    server = ImapStub(user="hello@glow.example", password=PASSWORD).start()
    yield server
    server.stop()


def mail(
    *,
    sender="ana@example.com",
    subject="Hello",
    body="Hi there",
    message_id="<m1@example.com>",
    headers="",
) -> bytes:
    return (
        f"From: Ana Silva <{sender}>\r\nTo: hello@glow.example\r\nSubject: {subject}\r\n"
        f"Message-ID: {message_id}\r\nDate: Fri, 06 Nov 2026 10:00:00 +0000\r\n{headers}"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
    ).encode()


def bounce(address: str, status: str = "5.1.1", action: str = "failed") -> bytes:
    return (
        "From: Mail Delivery System <MAILER-DAEMON@mx.example>\r\nTo: hello@glow.example\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\nMessage-ID: <dsn-1@mx.example>\r\n"
        'MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain\r\n\r\nYour message could not be delivered.\r\n"
        "--B\r\nContent-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; mx.example\r\n\r\n"
        f"Final-Recipient: rfc822; {address}\r\nAction: {action}\r\nStatus: {status}\r\n"
        "Diagnostic-Code: smtp; 550 5.1.1 User unknown\r\n\r\n"
        "--B--\r\n"
    ).encode()


# ---------------------------------------------------------------------------
# reading a message
# ---------------------------------------------------------------------------


def test_a_reply_is_read_without_the_message_it_quotes():
    raw = mail(
        subject="Re: Booking [#1004]",
        body="Yes, 7 works.\r\n\r\nOn Thu, 5 Nov 2026 at 09:00, Glow <hello@glow.example> wrote:\r\n> Can you come at 7?",
        headers="In-Reply-To: <a@glow.example>\r\nReferences: <root@glow.example> <a@glow.example>\r\n",
    )
    message = inbound_mail.parse(raw)
    assert message.from_email == "ana@example.com" and message.from_name == "Ana Silva"
    assert message.text == "Yes, 7 works." and message.reference == 1004
    assert message.threads == ["<a@glow.example>", "<root@glow.example>"]
    assert not message.automatic and not message.bounced


def test_bounces_and_automatic_replies_are_recognised():
    report = inbound_mail.parse(bounce("gone@acme.example"))
    assert report.is_report and report.bounced == [
        {
            "email": "gone@acme.example",
            "status": "5.1.1",
            "diagnostic": "smtp; 550 5.1.1 User unknown",
        }
    ]
    delayed = inbound_mail.parse(
        bounce("slow@acme.example", status="4.4.1", action="delayed")
    )
    assert delayed.is_report and delayed.bounced == []  # a delay is not a bounce
    away = inbound_mail.parse(mail(headers="Auto-Submitted: auto-replied\r\n"))
    assert away.automatic


# ---------------------------------------------------------------------------
# where a message goes
# ---------------------------------------------------------------------------


def test_a_new_email_becomes_a_case_and_a_reply_with_its_reference_joins_it(db):
    first = inbound_mail.handle(
        db, mail(subject="Where is my order?", body="Order 55 is late.")
    )
    assert first["handled_by"].startswith("service: new case")
    [case] = desk.list_cases(db, view="all")
    assert case["channel"] == "email" and case["customer_email"] == "ana@example.com"
    acknowledged = next(m for m in kit.outbox(db) if m["kind"] == "case_received")
    assert f"[{case['reference']}]" in acknowledged["subject"]
    assert any(
        m["kind"] == "staff_new_case" for m in kit.outbox(db)
    )  # the team hears about it
    reply = inbound_mail.handle(
        db,
        mail(
            subject=f"Re: We received your request [{case['reference']}]",
            body="Any news?",
            message_id="<m2@example.com>",
        ),
    )
    assert reply["handled_by"] == f"service: reply on {case['reference']}"
    thread = [m["body"] for m in service_store.messages(db, case["id"])]
    assert thread[-1] == "Any news?" and len(desk.list_cases(db, view="all")) == 1
    # the same message read twice is handled once
    again = inbound_mail.handle(
        db,
        mail(
            subject=f"Re: [{case['reference']}]",
            body="Any news?",
            message_id="<m2@example.com>",
        ),
    )
    assert again["handled_by"] == "duplicate"


def test_a_stranger_cannot_write_into_someone_elses_case_by_typing_its_reference(db):
    inbound_mail.handle(db, mail(subject="Refund", body="Please refund"))
    [case] = desk.list_cases(db, view="all")
    outcome = inbound_mail.handle(
        db,
        mail(
            sender="mallory@evil.example",
            subject=f"Re: [{case['reference']}]",
            body="Send it to me",
            message_id="<x@evil>",
        ),
    )
    assert outcome["handled_by"].startswith("service: new case")
    assert all(
        m["body"] != "Send it to me" for m in service_store.messages(db, case["id"])
    )


def test_a_reply_to_an_email_we_sent_threads_by_its_message_id(db):
    case = desk.create_case(
        db,
        {"subject": "Login", "email": "ana@example.com", "message": "Cannot log in"},
        channel="web",
    )
    sent = []
    kit.send_outbox(db, "pw", sender=lambda s, p, m: sent.append(m))
    ours = next(m for m in sent if m["To"] == "ana@example.com")
    outcome = inbound_mail.handle(
        db,
        mail(
            sender="ana.other@example.com",
            subject="Re: your request",
            body="Thanks",
            headers=f"In-Reply-To: {ours['Message-ID']}\r\n",
            message_id="<r@x>",
        ),
    )
    assert outcome["handled_by"] == f"service: reply on {case['reference']}"


def test_a_reply_to_a_closed_case_opens_a_follow_up(db):
    case = desk.create_case(
        db,
        {"subject": "Old", "email": "ana@example.com", "message": "x"},
        channel="web",
    )
    desk.update_case(db, case["id"], {"status": "closed"})
    outcome = inbound_mail.handle(
        db, mail(subject=f"Re: [{case['reference']}]", body="It broke again")
    )
    assert "follow-up to" in outcome["handled_by"]
    assert len(desk.list_cases(db, view="all")) == 2


def test_email_to_case_can_be_turned_off(db):
    service_store.save_settings(db, {"email_to_case": False})
    assert inbound_mail.handle(db, mail())["handled_by"] == "not matched"
    assert desk.list_cases(db, view="all") == []


def test_a_bounce_puts_the_address_on_the_do_not_contact_list_and_withdraws_its_mail(
    db,
):
    queued = kit.queue_email(
        db, "events", "ticket", to="gone@acme.example", subject="T", body="b"
    )
    outcome = inbound_mail.handle(db, bounce("gone@acme.example"))
    assert outcome["handled_by"] == "bounce: gone@acme.example"
    assert transport.do_not_contact(db, "gone@acme.example", bounces_only=True)
    assert kit.require_row(db, "suite_outbox", queued["id"])["status"] == "cancelled"
    assert desk.list_cases(db, view="all") == []  # a bounce never becomes a case


def test_automatic_replies_and_our_own_mail_are_ignored(db):
    assert (
        inbound_mail.handle(db, mail(headers="Auto-Submitted: auto-replied\r\n"))[
            "handled_by"
        ]
        == "ignored: automatic reply"
    )
    assert (
        inbound_mail.handle(
            db, mail(sender="hello@glow.example", message_id="<own@x>")
        )["handled_by"]
        == "ignored: our own address"
    )
    assert desk.list_cases(db, view="all") == []


def test_an_unsubscribe_email_unsubscribes_and_stops_writing(db):
    subscriber = marketing.add_subscriber(db, {"email": "ana@example.com"})
    outcome = inbound_mail.handle(db, mail(subject="unsubscribe", body=""))
    assert outcome["handled_by"].startswith("unsubscribe")
    assert (
        marketing_store.get(db, "mk_subscribers", subscriber["id"], "s")["status"]
        == "unsubscribed"
    )
    assert transport.suppressed_reason(db, {"email": "ana@example.com"})
    assert desk.list_cases(db, view="all") == []


def test_a_lead_who_replies_is_marked_replied_and_their_sequence_stops(db):
    lead_id = leads_store.insert_lead(
        db,
        {
            "name": "Biz A",
            "email": "a@biza.test",
            "kind": "company",
            "source": "manual",
        },
        list_id=leads_store.default_list(db)["id"],
    )["lead"]["id"]
    sequence = sequences.save_sequence(
        db,
        {
            "name": "Follow up",
            "review": False,
            "steps": [
                {
                    "delay_days": 0,
                    "subject": "Hello {first_name}",
                    "body": "A first note.",
                },
                {"delay_days": 3, "body": "A second note."},
            ],
        },
    )
    sequences.enroll(db, sequence["id"], [lead_id])
    sequences.tick(db)
    first = db.execute(
        "SELECT * FROM lead_messages WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    db.execute(
        "UPDATE lead_messages SET status = 'sent', sent_at = ?, message_id = '<lead-1@glow.example>' WHERE id = ?",
        (leads_store.now(), first["id"]),
    )
    db.commit()
    outcome = inbound_mail.handle(
        db,
        mail(
            sender="someone@biza.test",
            subject="Re: Hello",
            body="Interested!",
            headers="In-Reply-To: <lead-1@glow.example>\r\n",
        ),
    )
    assert outcome["handled_by"].startswith("leads: reply from")
    assert leads_store.get_lead(db, lead_id)["status"] == "replied"
    enrollment = db.execute("SELECT * FROM lead_enrollments").fetchone()
    assert enrollment["status"] == "stopped" and enrollment["stop_reason"] == "replied"
    assert desk.list_cases(db, view="all") == []


def test_careers_mailbox_sync_has_a_place_to_plug_in(db, monkeypatch):
    seen = []
    monkeypatch.setitem(
        inbound_mail.CONSUMERS,
        "careers",
        lambda connection, message: (
            seen.append(message.subject) or "careers: recruiter reply"
        ),
    )
    outcome = inbound_mail.handle(db, mail(subject="Your application at Acme"))
    assert outcome["handled_by"] == "careers: recruiter reply" and seen == [
        "Your application at Acme"
    ]
    assert desk.list_cases(db, view="all") == []


# ---------------------------------------------------------------------------
# the mailbox
# ---------------------------------------------------------------------------


def _mailbox(db, imap, **extra):
    inbound_mail.save_settings(
        db,
        {
            "enabled": True,
            "imap_host": "127.0.0.1",
            "imap_port": imap.port,
            "imap_security": "none",
            "imap_username": "hello@glow.example",
            **extra,
        },
    )


def test_polling_starts_from_the_newest_message_and_never_changes_the_mailbox(db, imap):
    imap.add(mail(subject="Old mail", message_id="<old@x>"))
    _mailbox(db, imap)
    secrets = Secrets({inbound_mail.PASSWORD_SECRET: PASSWORD})
    first = inbound_mail.poll(db, secrets)
    assert first["ran"] and first.get("started_at_uid") == 1 and "error" not in first
    assert desk.list_cases(db, view="all") == []  # old mail is not imported
    imap.add(mail(subject="New question", message_id="<new@x>"))
    second = inbound_mail.poll(db, secrets)
    assert second["read"] == 1 and second["handled"] == {"service": 1}
    assert [c["subject"] for c in desk.list_cases(db, view="all")] == ["New question"]
    assert inbound_mail.poll(db, secrets)["read"] == 0  # nothing new
    # read-only: the folder is EXAMINEd and messages are PEEKed, so nothing becomes "read" or moves
    assert "EXAMINE" in imap.commands and "SELECT" not in imap.commands
    assert imap.peeked and all(imap.peeked)
    view = inbound_mail.view(db, secrets)
    assert (
        view["has_password"]
        and view["last_error"] == ""
        and view["recent"][0]["subject"] == "New question"
    )


def test_a_wrong_password_is_recorded_and_nothing_breaks(db, imap):
    _mailbox(db, imap)
    result = inbound_mail.poll(db, Secrets({inbound_mail.PASSWORD_SECRET: "wrong"}))
    assert "refused the username or password" in result["error"]
    assert "refused" in inbound_mail.view(db, Secrets())["last_error"]


def test_the_background_pass_reads_the_mailbox(db, imap):
    imap.add(mail(subject="Old", message_id="<o@x>"))
    _mailbox(db, imap)
    secrets = Secrets({inbound_mail.PASSWORD_SECRET: PASSWORD})
    background.run(db, secrets)
    imap.add(mail(subject="Help please", message_id="<h@x>"))
    result = background.run(db, secrets)
    assert result["inbound"]["read"] == 1
    assert [c["subject"] for c in desk.list_cases(db, view="all")] == ["Help please"]


def test_plain_imap_is_only_for_this_computer_and_ssl_checks_the_certificate(
    db, monkeypatch
):
    with pytest.raises(ValueError, match="SSL or STARTTLS"):
        inbound_mail.save_settings(
            db,
            {
                "enabled": True,
                "imap_host": "imap.example.com",
                "imap_security": "none",
                "imap_username": "me",
            },
        )
    seen = {}

    class FakeSSL:
        def __init__(self, host, port, *, ssl_context=None, timeout=None):
            seen.update(host=host, port=port, context=ssl_context, timeout=timeout)

        def login(self, user, password):
            seen["login"] = (user, password)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeSSL)
    inbound_mail.open_mailbox(
        {
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_security": "ssl",
            "imap_username": "me",
        },
        "pw",
    )
    context = seen["context"]
    assert seen["host"] == "imap.example.com" and seen["login"] == ("me", "pw")
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_a_hosted_build_refuses_an_internal_mailbox_and_connects_to_the_checked_address(
    db, monkeypatch
):
    from aira.leads import enrich

    monkeypatch.setattr(transport, "_strict", lambda: True)
    with pytest.raises(ValueError, match="cannot be used here"):
        inbound_mail.save_settings(
            db, {"enabled": True, "imap_host": "10.1.2.3", "imap_username": "me"}
        )
    assert inbound_mail.settings(db)["imap_host"] == ""  # nothing was stored
    monkeypatch.setattr(
        enrich, "_check_addresses", lambda host, port, allow_local=None: ["203.0.113.9"]
    )
    made = {}

    class Pinned(inbound_mail._PinnedIMAPSSL):
        def __init__(self, host, port, address, *, timeout, ssl_context):
            made.update(host=host, address=address)

        def login(self, user, password):
            pass

    monkeypatch.setattr(inbound_mail, "_PinnedIMAPSSL", Pinned)
    inbound_mail.open_mailbox(
        {
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_security": "ssl",
            "imap_username": "me",
        },
        "pw",
    )
    assert made == {"host": "imap.example.com", "address": "203.0.113.9"}


def test_the_hub_keeps_the_mailbox_password_secret_and_checks_on_demand(
    env, api, local_index, imap
):
    kit.forget_schema()
    imap.add(mail(subject="Old", message_id="<o2@x>"))
    view = api.post(
        "/api/suite/inbound",
        {
            "enabled": True,
            "imap_host": "127.0.0.1",
            "imap_port": imap.port,
            "imap_security": "none",
            "imap_username": "hello@glow.example",
            "imap_password": PASSWORD,
        },
    )
    assert view["has_password"] is True and PASSWORD not in str(view)
    assert PASSWORD not in str(api.get("/api/suite/inbound"))
    checked = api.post("/api/suite/inbound/check", {})
    assert checked["result"]["ran"] and "error" not in checked["result"]
    imap.add(mail(subject="A new request", message_id="<n2@x>"))
    checked = api.post("/api/suite/inbound/check", {})
    assert checked["result"]["read"] == 1
    assert (
        api.get("/api/service/cases?view=all")["cases"][0]["subject"] == "A new request"
    )
    status, body = api.call(
        "POST",
        "/api/suite/inbound",
        {"imap_security": "none", "imap_host": "imap.example.com"},
    )
    assert status == 400 and "SSL or STARTTLS" in body["error"]
