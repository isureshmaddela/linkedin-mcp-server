"""The shared outbox and newsletters (platform review P0-12, section 2.3 and section 4's small defects).

* An unsubscribe cancels newsletters already queued, and the outbox checks unsubscribes and the do-not-contact list
  again when it sends (the review reproduced a queued newsletter reaching someone who had unsubscribed).
* Newsletters carry RFC 8058 one-click List-Unsubscribe headers; the one-click POST works without a session.
* Newsletter sign-ups use double opt-in; public forms have a hidden field and a minimum fill time.
* Transactional kinds send automatically by default, time-bound messages expire, passing failures are retried.
* SMTP settings are checked before they are saved, and the mail server's address is resolved once.

Every mail server here is a fake sender or the loopback stand-in (``tests/smtp_stub.py``).
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import smtplib
import socket
import sqlite3
import urllib.error
import urllib.request
from datetime import timedelta
from email import message_from_string

import pytest
from smtp_stub import SmtpStub
from test_suite_marketing import _anonymous

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.leads import enrich
from aira.leads import mail as transport
from aira.suite import kit, public
from aira.suite.marketing import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

SENDER = {"from_email": "news@glow.example", "smtp_host": "smtp.glow.example"}


class Sent:
    """A fake mail server: keeps every message it is given."""

    def __init__(self) -> None:
        self.messages: list = []

    def __call__(self, settings, password, message) -> None:
        self.messages.append(message)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    suite.load_apps()
    connection = sqlite3.connect(tmp_path / "mail.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_email_settings(connection, SENDER)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _subscribed(db, address="ana@example.com", name="Ana"):
    return service.add_subscriber(db, {"email": address, "name": name})


def _newsletter(db, body="Our autumn news."):
    letter = service.create_newsletter(db, {"subject": "Autumn", "body": body})
    service.queue_newsletter(
        db, letter["id"], confirm=True, source={"base_url": "http://127.0.0.1:8765"}
    )
    return [m for m in kit.outbox(db, app="marketing") if m["kind"] == "newsletter"]


# ---------------------------------------------------------------------------
# P0-12: suppression at send time
# ---------------------------------------------------------------------------


def test_an_unsubscribe_cancels_a_newsletter_that_was_already_queued_and_approved(db):
    """The review's reproduction: queue, approve, unsubscribe, send. Before the fix the newsletter went out."""
    subscriber = _subscribed(db)
    [message] = _newsletter(db)
    assert kit.set_outbox_status(db, [message["id"]], "approved") == 1
    service.unsubscribe(db, subscriber["id"])
    sent = Sent()
    result = kit.send_outbox(db, "pw", sender=sent)
    assert sent.messages == [] and result["sent"] == 0
    after = kit.require_row(db, "suite_outbox", message["id"], "message")
    assert after["status"] == "cancelled" and "Unsubscribed" in after["error"]


def test_the_outbox_checks_again_when_it_sends_even_if_nothing_withdrew_the_message(db):
    """Whatever path unsubscribed the person (an import, another tool writing the table), the send-time check holds."""
    _subscribed(db)
    _subscribed(db, "bo@example.com", "Bo")
    messages = _newsletter(db)
    kit.set_outbox_status(db, [m["id"] for m in messages], "approved")
    db.execute(
        "UPDATE mk_subscribers SET status = 'unsubscribed' WHERE email = 'ana@example.com'"
    )
    db.commit()
    sent = Sent()
    result = kit.send_outbox(db, "pw", sender=sent)
    assert [m["To"] for m in sent.messages] == ["bo@example.com"]
    assert result["sent"] == 1 and result["skipped"] == 1
    skipped = next(d for d in result["details"] if d["status"] == "cancelled")
    assert "Unsubscribed" in skipped["error"]


def test_the_do_not_contact_list_stops_bulk_mail_and_a_bounce_stops_every_kind(db):
    _subscribed(db, "ana@acme.example")
    [letter] = _newsletter(db)
    kit.set_outbox_status(db, [letter["id"]], "approved")
    receipt = kit.queue_email(
        db,
        "shop",
        "payment_receipt",
        to="ana@acme.example",
        subject="Paid",
        body="Thanks",
    )
    transport.add_suppression(db, "acme.example", "Asked not to be contacted")
    sent = Sent()
    kit.send_outbox(db, "pw", sender=sent)
    # the newsletter stops; a receipt the customer is waiting for still goes to a cold-email opt-out
    assert [m["Subject"] for m in sent.messages] == ["Paid"]
    assert kit.require_row(db, "suite_outbox", letter["id"])["status"] == "cancelled"
    assert kit.require_row(db, "suite_outbox", receipt["id"])["status"] == "sent"

    ticket = kit.queue_email(
        db,
        "events",
        "ticket",
        to="gone@acme.example",
        subject="Ticket",
        body="Your ticket",
    )
    assert transport.record_bounce(db, "gone@acme.example", "5.1.1 no such user")
    assert kit.require_row(db, "suite_outbox", ticket["id"])["status"] == "cancelled"
    later = kit.queue_email(
        db, "events", "ticket", to="gone@acme.example", subject="Ticket 2", body="Again"
    )
    sent = Sent()
    result = kit.send_outbox(db, "pw", sender=sent)
    assert sent.messages == [] and result["skipped"] == 1
    assert "bounced" in kit.require_row(db, "suite_outbox", later["id"])["error"]


# ---------------------------------------------------------------------------
# P0-12: RFC 8058 one-click unsubscribe
# ---------------------------------------------------------------------------


def test_newsletters_carry_one_click_unsubscribe_headers_and_receipts_do_not(db):
    _subscribed(db)
    previous = suite._public_origin
    suite.set_public_origin(lambda: "https://glow.example")
    try:
        [message] = _newsletter(db)
    finally:
        suite.set_public_origin(previous)
    kit.queue_email(
        db,
        "shop",
        "order_confirmation",
        to="ana@example.com",
        subject="Order",
        body="Thanks",
    )
    kit.set_outbox_status(db, [message["id"]], "approved")
    sent = Sent()
    kit.send_outbox(db, "pw", sender=sent)
    by_subject = {m["Subject"]: m for m in sent.messages}
    newsletter = by_subject["Autumn"]
    token = public.link_for(db, "marketing", "unsubscribe", _subscribed(db)["id"])[
        "token"
    ]
    assert newsletter["List-Unsubscribe"] == (
        f"<https://glow.example/api/public/{token}/unsubscribe>, "
        "<mailto:news@glow.example?subject=unsubscribe>"
    )
    assert newsletter["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert len(token) >= 40  # 32 random bytes, one link per subscriber
    order = by_subject["Order"]
    assert order["List-Unsubscribe"] is None and order["List-Unsubscribe-Post"] is None


def test_without_an_https_address_only_the_mailto_is_offered(db):
    _subscribed(db)
    [message] = _newsletter(db)  # the local build's own http://127.0.0.1 address
    kit.set_outbox_status(db, [message["id"]], "approved")
    sent = Sent()
    kit.send_outbox(db, "pw", sender=sent)
    [mail] = sent.messages
    assert mail["List-Unsubscribe"] == "<mailto:news@glow.example?subject=unsubscribe>"
    assert mail["List-Unsubscribe-Post"] is None


def _form_post(env, path: str, body: bytes, headers: dict | None = None):
    request = urllib.request.Request(env.base + path, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_a_mail_clients_one_click_post_unsubscribes_on_the_local_build(
    env, api, local_index
):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow"})
    api.post("/api/marketing/subscribers", {"email": "ana@example.com"}, expect=201)
    letter = api.post(
        "/api/marketing/newsletters", {"subject": "News", "body": "Hello"}, expect=201
    )["newsletter"]
    api.post(f"/api/marketing/newsletters/{letter['id']}/queue", {"confirm": True})
    [message] = [
        m
        for m in api.get("/api/suite/outbox?app=marketing")["messages"]
        if m["kind"] == "newsletter"
    ]
    path = message["unsubscribe_url"].split(env.base.split("//")[1], 1)[-1]
    assert path.startswith("/api/public/") and path.endswith("/unsubscribe")
    # a link checker opening the address changes nothing
    with urllib.request.urlopen(env.base + path, timeout=30) as response:
        assert json.loads(response.read()) == {"subscribed": True}
    # RFC 8058: a form-encoded POST from the mail provider, no Origin, no session
    status, body = _form_post(env, path, b"List-Unsubscribe=One-Click")
    assert status == 200 and body == {"subscribed": False}
    subscriber = api.get("/api/marketing/subscribers")["subscribers"][0]
    assert subscriber["status"] == "unsubscribed"
    assert (
        api.get("/api/suite/outbox?app=marketing")["messages"][0]["status"]
        == "cancelled"
    )
    # form bodies are read only for public links; anything signed in still needs JSON
    status, _ = _form_post(env, "/api/suite/business", b"name=Evil")
    assert status in (400, 401, 403)


def test_the_hosted_gateway_takes_the_one_click_post_too(cloud):
    from cloud_support import Browser, build_org, sign_in

    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "marketing")
    owner = sign_in(cloud, org.owner)
    owner.call("POST", "/api/marketing/subscribers", {"email": "ana@fans.example"})
    _, made = owner.call(
        "POST", "/api/marketing/newsletters", {"subject": "Hi", "body": "Hello"}
    )
    owner.call(
        "POST",
        f"/api/marketing/newsletters/{made['newsletter']['id']}/queue",
        {"confirm": True},
    )
    _, box = owner.call("GET", "/api/suite/outbox?app=marketing")
    url = next(m for m in box["messages"] if m["kind"] == "newsletter")[
        "unsubscribe_url"
    ]
    # the hosted build's configured address (https in production; the test server's own here)
    assert "/api/public/" in url and url.endswith("/unsubscribe")
    path = "/api/public/" + url.split("/api/public/", 1)[1]
    # the mail provider's server: a form-encoded body, no Origin, no session
    status, body = Browser(cloud).call(
        "POST",
        path,
        b"List-Unsubscribe=One-Click",
        headers={
            "Origin": "",
            "X-Requested-With": "",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert status == 200 and body == {"subscribed": False}
    _, listing = owner.call("GET", "/api/marketing/subscribers")
    assert listing["subscribers"][0]["status"] == "unsubscribed"


# ---------------------------------------------------------------------------
# P0-12: double opt-in and the bot check
# ---------------------------------------------------------------------------


def test_a_form_sign_up_waits_for_confirmation_before_it_gets_newsletters(db):
    kit.save_business(db, {"name": "Glow"})
    form = service.create_form(db, {"title": "News"})
    service.submit(
        db, form["id"], {"name": "Ana", "email": "ana@example.com", "consent": True}
    )
    [pending] = service.list_subscribers(db)
    assert pending["status"] == "pending" and pending["consent_at"] is None
    confirm = next(m for m in kit.outbox(db) if m["kind"] == "newsletter_confirm")
    assert confirm["recipient"] == "ana@example.com" and confirm["status"] == "approved"
    assert "/p/" in confirm["body"] and "ignore this email" in confirm["body"]
    # a second sign-up within the hour sends no second email (a form cannot flood an inbox)
    service.submit(
        db, form["id"], {"name": "Ana", "email": "ana@example.com", "consent": True}
    )
    assert sum(m["kind"] == "newsletter_confirm" for m in kit.outbox(db)) == 1
    with pytest.raises(ValueError, match="nobody is subscribed"):
        _newsletter(db)
    with pytest.raises(ValueError, match="not confirmed"):
        service.update_subscriber(db, pending["id"], {"status": "subscribed"})
    # the confirmation page changes nothing until the person presses Confirm
    link = public.link_for(db, "marketing", "confirm", pending["id"])
    page = public.view(db, link["token"])["page"]
    assert page["subscribed"] is False and "*" in page["email"]
    status, data = public.act(db, link["token"], "confirm", {}, {"client": "x"})
    assert status == 200 and data == {"subscribed": True}
    assert len(_newsletter(db)) == 1


def test_the_bot_check_wants_a_ticket_from_the_page_and_a_few_seconds(db):
    now = kit.utcnow()
    ticket = kit.form_ticket(db, at=now - timedelta(seconds=10))
    assert kit.check_form(db, {"started": ticket}) is True
    assert kit.check_form(db, {"started": ticket, "website": "spam"}) is False
    assert kit.check_form(db, {}) is False  # never loaded the page
    assert kit.check_form(db, {"started": ticket[:-4] + "0000"}) is False  # forged
    with pytest.raises(ValueError, match="quick"):
        kit.check_form(db, {"started": kit.form_ticket(db)})
    with pytest.raises(ValueError, match="Reload"):
        kit.check_form(db, {"started": kit.form_ticket(db, at=now - timedelta(days=2))})


def test_a_public_form_sent_without_the_page_or_too_fast_keeps_nothing(
    env, api, local_index
):
    kit.forget_schema()
    form = api.post("/api/marketing/forms", {"title": "Consult"}, expect=201)["form"]
    token = api.post(f"/api/marketing/forms/{form['id']}/link")["public"]["token"]
    body = {"name": "Bot", "email": "bot@spam.example"}
    status, data = _anonymous(env, "POST", f"/api/public/{token}/submit", body)
    assert status == 201 and data["stored"] is False
    _, page = _anonymous(env, "GET", f"/api/public/{token}")
    status, data = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/submit",
        {**body, "started": page["page"]["started"]},
    )
    assert status == 400 and "quick" in data["error"]
    assert api.get("/api/marketing/submissions")["total"] == 0


def test_the_help_centre_contact_form_has_the_same_check(env, api, local_index):
    kit.forget_schema()
    token = api.post("/api/service/help/link")["link"]["token"]
    message = {"name": "Bot", "email": "bot@spam.example", "message": "Buy now"}
    status, data = _anonymous(env, "POST", f"/api/public/{token}/contact", message)
    assert status == 201 and data["reference"] == ""
    assert api.get("/api/service/cases?view=all")["cases"] == []


# ---------------------------------------------------------------------------
# small defect: a newsletter that saves can always be queued
# ---------------------------------------------------------------------------


def test_a_newsletter_is_checked_with_its_greeting_and_footer_when_it_is_saved(db):
    kit.save_business(db, {"name": "G" * 120})
    limit = service.newsletter_limit(db)
    assert limit < kit.MAX_BODY
    with pytest.raises(ValueError, match="too long"):
        service.create_newsletter(db, {"subject": "Long", "body": "x" * (limit + 1)})
    # the review's case: just under 20,000 characters used to save and then never queue
    with pytest.raises(ValueError, match="too long"):
        service.create_newsletter(db, {"subject": "Long", "body": "x" * 19990})
    service.add_subscriber(db, {"email": "ana@example.com", "name": "A" * 120})
    letter = service.create_newsletter(db, {"subject": "Long", "body": "x" * limit})
    queued = service.queue_newsletter(
        db, letter["id"], confirm=True, source={"base_url": "http://127.0.0.1:8765"}
    )
    assert queued["queued"] == 1
    [message] = [m for m in kit.outbox(db) if m["kind"] == "newsletter"]
    assert len(message["body"]) <= kit.MAX_BODY


# ---------------------------------------------------------------------------
# section 2.3: transactional email by default, expiry, retries
# ---------------------------------------------------------------------------


def test_confirmations_tickets_and_receipts_send_by_default_and_the_business_can_choose(
    db,
):
    def status_of(kind):
        return kit.queue_email(
            db, "x", kind, to="a@example.com", subject="s", body="b"
        )["status"]

    for kind in (
        "order_confirmation",
        "ticket",
        "booking_confirmation",
        "payment_receipt",
    ):
        assert status_of(kind) == "approved", kind
    for kind in ("booking_reminder", "newsletter", "invoice"):
        assert status_of(kind) == "pending", kind
    kinds = {k["id"]: k for k in kit.message_kinds(kit.email_settings(db))}
    assert kinds["order_confirmation"]["label"] == "Shop: order confirmations"
    assert kinds["order_confirmation"]["on"] and not kinds["booking_reminder"]["on"]
    # the business picks: only what it ticked goes out by itself
    kit.save_email_settings(db, {"auto_send": ["booking_reminder"]})
    assert status_of("order_confirmation") == "pending"
    assert status_of("booking_reminder") == "approved"
    assert kit.email_settings(db)["auto_send_chosen"] is True
    # a kind an app adds later (Shop: "order_paid") is kept and listed by its own name
    kit.save_email_settings(db, {"auto_send": ["order_paid"]})
    extra = {k["id"]: k for k in kit.message_kinds(kit.email_settings(db))}[
        "order_paid"
    ]
    assert extra["on"] and extra["group"] == "other"
    assert status_of("missed_dose") == "approved"  # safety notices always go


def test_time_bound_messages_expire_and_are_dropped_unsent(db):
    soon = kit.iso(kit.utcnow() + timedelta(hours=1))
    reminder = kit.queue_email(
        db,
        "bookings",
        "booking_reminder",
        to="a@example.com",
        subject="Tomorrow",
        body="b",
        send_after=soon,
    )
    assert kit.expiry_of(reminder) == kit.iso(kit.parse_time(soon) + timedelta(hours=23))
    # an app that moves the reminder (an event rescheduled) moves its expiry with it
    moved = kit.iso(kit.utcnow() + timedelta(days=5))
    db.execute(
        "UPDATE suite_outbox SET send_after = ? WHERE id = ?", (moved, reminder["id"])
    )
    db.commit()
    assert kit.expire_outbox(db, at=kit.iso(kit.utcnow() + timedelta(days=2))) == 0
    db.execute(
        "UPDATE suite_outbox SET send_after = ? WHERE id = ?", (soon, reminder["id"])
    )
    db.commit()
    custom = kit.queue_email(
        db,
        "x",
        "anything",
        to="b@example.com",
        subject="s",
        body="b",
        expires_at=kit.iso(kit.utcnow() - timedelta(minutes=1)),
    )
    plain = kit.queue_email(
        db, "x", "invoice", to="c@example.com", subject="s", body="b"
    )
    assert kit.expiry_of(plain) is None
    assert kit.expire_outbox(db) == 1
    gone = kit.require_row(db, "suite_outbox", custom["id"])
    assert gone["status"] == "cancelled" and "Expired" in gone["error"]
    # an expired reminder can no longer be approved, and is not sent
    late = kit.iso(kit.utcnow() + timedelta(days=2))
    assert kit.expire_outbox(db, at=late) == 1
    assert kit.set_outbox_status(db, [reminder["id"]], "approved") == 0
    assert kit.require_row(db, "suite_outbox", reminder["id"])["status"] == "cancelled"


def test_a_passing_failure_is_retried_with_backoff_then_marked_failed(db):
    message = kit.queue_email(
        db, "events", "ticket", to="a@example.com", subject="T", body="b"
    )

    def down(settings, password, mail):
        raise transport.SendError(
            "The mail server could not send it (timeout).", temporary=True
        )

    for attempt in range(1, kit.MAX_ATTEMPTS):
        result = kit.send_outbox(db, "pw", sender=down)
        assert result["retrying"] == 1 and result["failed"] == 0
        row = kit.require_row(db, "suite_outbox", message["id"])
        assert row["status"] == "approved" and row["attempts"] == attempt
        wait = kit.parse_time(row["send_after"]) - kit.utcnow()
        assert (
            timedelta(minutes=kit.RETRY_MINUTES[attempt - 1] - 1)
            < wait
            <= timedelta(minutes=kit.RETRY_MINUTES[attempt - 1])
        )
        assert kit.send_outbox(db, "pw", sender=down)["retrying"] == 0  # not due yet
        db.execute(
            "UPDATE suite_outbox SET send_after = ? WHERE id = ?",
            (kit.now(), message["id"]),
        )
        db.commit()
    result = kit.send_outbox(db, "pw", sender=down)
    assert result["failed"] == 1
    row = kit.require_row(db, "suite_outbox", message["id"])
    assert row["status"] == "failed" and row["attempts"] == kit.MAX_ATTEMPTS
    # approving it again starts over
    assert kit.set_outbox_status(db, [message["id"]], "approved") == 1
    sent = Sent()
    assert kit.send_outbox(db, "pw", sender=sent)["sent"] == 1


def test_a_refused_address_fails_at_once_and_goes_on_the_do_not_contact_list(db):
    message = kit.queue_email(
        db, "events", "ticket", to="nobody@example.com", subject="T", body="b"
    )

    def refuse(settings, password, mail):
        raise transport.SendError(
            "The recipient was refused: no such user", bounced=True
        )

    result = kit.send_outbox(db, "pw", sender=refuse)
    assert result["failed"] == 1 and result["retrying"] == 0
    assert kit.require_row(db, "suite_outbox", message["id"])["status"] == "failed"
    assert transport.do_not_contact(db, "nobody@example.com", bounces_only=True)


def test_a_sent_message_keeps_its_message_id_for_threading(db):
    message = kit.queue_email(
        db,
        "service",
        "case_received",
        to="a@example.com",
        subject="We got it",
        body="b",
        ref_type="case",
        ref_id="c1",
    )
    sent = Sent()
    kit.send_outbox(db, "pw", sender=sent)
    message_id = sent.messages[0]["Message-ID"]
    found = kit.sent_message(db, ["<other@x>", message_id])
    assert found["id"] == message["id"] and found["ref_id"] == "c1"


# ---------------------------------------------------------------------------
# small defects: SMTP settings are checked before saving; the mail server is resolved once
# ---------------------------------------------------------------------------


def test_refused_smtp_settings_are_never_stored(db):
    before = kit.email_settings(db)

    def refuse(host, port):
        raise ValueError("The mail server cannot be used here.")

    with pytest.raises(ValueError, match="cannot be used here"):
        kit.save_email_settings(db, {"smtp_host": "10.0.0.5"}, check_host=refuse)
    assert kit.email_settings(db) == before


def test_the_hub_refuses_an_internal_mail_server_before_saving_it(
    env, api, local_index, monkeypatch
):
    kit.forget_schema()
    api.post(
        "/api/suite/email",
        {"from_email": "me@glow.example", "smtp_host": "smtp.gmail.com"},
    )
    monkeypatch.setattr(transport, "_strict", lambda: True)  # what a hosted build does
    status, body = api.call(
        "POST", "/api/suite/email", {"smtp_host": "127.0.0.1", "from_name": "Changed"}
    )
    assert status == 400 and "cannot be used here" in body["error"]
    view = api.get("/api/suite/email")
    assert (
        view["settings"]["smtp_host"] == "smtp.gmail.com"
        and view["settings"]["from_name"] == ""
    )


def test_the_mail_server_is_resolved_once_and_the_connection_goes_to_that_address(
    monkeypatch,
):
    smtp = SmtpStub().start()
    try:
        monkeypatch.setattr(transport, "_strict", lambda: True)
        checked = []

        def check(host, port, allow_local=None):
            checked.append(host)
            return ["127.0.0.1"]  # the one answer that was checked

        real = socket.getaddrinfo

        def no_second_lookup(host, *args, **kwargs):
            if host == "mail.glow.test":
                raise AssertionError("the name was resolved a second time")
            return real(host, *args, **kwargs)

        monkeypatch.setattr(enrich, "_check_addresses", check)
        monkeypatch.setattr(socket, "getaddrinfo", no_second_lookup)
        settings = {
            "from_name": "Glow",
            "from_email": "me@glow.example",
            "smtp_host": "mail.glow.test",
            "smtp_port": smtp.port,
            "smtp_security": "none",
        }
        message = transport.build_message(
            settings, to="a@example.com", subject="Hi", body="Hello"
        )
        transport.smtp_send(settings, "", message)
        assert checked == ["mail.glow.test"] and len(smtp.messages) == 1
        assert message_from_string(smtp.messages[0]["data"])["Subject"] == "Hi"
    finally:
        smtp.stop()


def test_tls_to_a_pinned_address_still_checks_the_servers_name(monkeypatch):
    seen = {}

    class Context:
        def wrap_socket(self, sock, server_hostname=None):
            seen["server_hostname"] = server_hostname
            sock.close()
            raise OSError("stop here")

    class Listener:
        def __init__(self):
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", 0))
            self.sock.listen(1)
            self.port = self.sock.getsockname()[1]

    listener = Listener()
    try:
        with pytest.raises(OSError):
            transport._PinnedSMTPSSL(
                "mail.glow.test",
                listener.port,
                "127.0.0.1",
                timeout=5,
                context=Context(),
            )
        assert seen["server_hostname"] == "mail.glow.test"
    finally:
        listener.sock.close()


def test_temporary_and_permanent_smtp_errors_are_told_apart():
    later = smtplib.SMTPRecipientsRefused({"a@x.example": (450, b"mailbox busy")})
    never = smtplib.SMTPRecipientsRefused({"a@x.example": (550, b"no such user")})

    def raising(error):
        class Server:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ehlo(self):
                pass

            def send_message(self, message):
                raise error

        return Server

    settings = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 25,
        "smtp_security": "none",
        "from_email": "a@b.example",
    }
    message = transport.build_message(
        {**settings, "from_name": "A"}, to="a@x.example", subject="s", body="b"
    )
    for error, temporary, bounced in ((later, True, False), (never, False, True)):
        original = smtplib.SMTP
        smtplib.SMTP = raising(error)  # type: ignore[misc]
        try:
            with pytest.raises(transport.SendError) as caught:
                transport.smtp_send(settings, "", message)
        finally:
            smtplib.SMTP = original  # type: ignore[misc]
        assert caught.value.temporary is temporary and caught.value.bounced is bounced
