"""Bookings and calendars: the .ics invite on confirmations, moves and cancellations (outbox attachments), each
staff member's own calendar feed (read safely, recurring events expanded, busy times kept out of availability)
and the customer's "Add to calendar" file.

Calendar feeds are served by a stand-in on this computer (plain http to the loopback address, allowed only by the
test switch); no real calendar service is ever called."""

from __future__ import annotations

import email
import email.policy
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

import test_careers
from aira.suite import background, kit, public, webfetch
from aira.suite.bookings import calendars, ical, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
ALL_WEEK = [{"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)]
CUSTOMER = {"name": "Maya Lopez", "email": "maya@example.com"}


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model():
    from aira import suite

    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "bookings.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(
        connection,
        {
            "name": "Glow Studio",
            "email": "owner@glow.example",
            "address": "12 Main St",
            "timezone": "America/New_York",
        },
    )
    yield connection
    connection.close()


class Feeds:
    """A stand-in calendar server: ``feeds[path] = (status, body, headers)``; ``calls`` lists the paths asked."""

    def __init__(self) -> None:
        self.feeds: dict[str, tuple[int, bytes, dict[str, str]]] = {}
        self.calls: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the http.server name
                outer.calls.append(self.path)
                status, body, headers = outer.feeds.get(self.path, (404, b"missing", {}))
                self.send_response(status)
                for key, value in {"Content-Type": "text/calendar", **headers}.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def put(self, path: str, text: str, status: int = 200, **headers: str) -> str:
        self.feeds[path] = (status, text.replace("\n", "\r\n").encode(), headers)
        return self.base + path

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture
def feeds(monkeypatch):
    monkeypatch.setenv(webfetch.ALLOW_LOCAL_ENV, "1")
    server = Feeds()
    yield server
    server.stop()


def setup(db, minutes=60):
    person = store.create_staff(db, {"name": "Ana", "email": "ana@glow.example", "hours": ALL_WEEK})
    cut = store.create_service(
        db, {"name": "Cut", "minutes": minutes, "price_cents": 4000, "staff_ids": [person["id"]]}
    )
    return person, cut


def future_day(days=10) -> date:
    return datetime.now(NY).date() + timedelta(days=days)


def at(day: date, hhmm: str, tz=NY) -> str:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return kit.iso(datetime.combine(day, datetime.min.time().replace(hour=hours, minute=minutes), tzinfo=tz))


def stamp(day: date, hhmm: str) -> str:
    return f"{day:%Y%m%d}T{hhmm.replace(':', '')}00"


def calendar(*events: str) -> str:
    return "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//EN\n" + "".join(events) + "END:VCALENDAR\n"


def event(uid: str, *lines: str) -> str:
    return "BEGIN:VEVENT\n" + f"UID:{uid}\n" + "".join(f"{line}\n" for line in lines) + "END:VEVENT\n"


def files_of(db, message_id):
    return kit.outbox_files(db, message_id)


def outbox(db, kind):
    return [m for m in kit.outbox(db, app="bookings") if m["kind"] == kind]


# ---------------------------------------------------------------------------
# the invite on every message, and outbox attachments
# ---------------------------------------------------------------------------


def test_confirmation_move_and_cancel_carry_one_event_that_updates(db):
    _, cut = setup(db)
    day = future_day()
    booking = service.book(db, service_id=cut["id"], start=at(day, "10:00"), customer=CUSTOMER, source="public")
    confirmation = outbox(db, "booking_confirmation")[0]
    assert confirmation["attachments"][0]["filename"] == "invite.ics"
    invite = files_of(db, confirmation["id"])[0]
    assert invite["content_type"] == "text/calendar; method=REQUEST; charset=UTF-8"
    text = invite["data"].decode()
    assert "METHOD:REQUEST" in text and f"UID:{booking['id']}@" in text and "SEQUENCE:0" in text
    assert f"DTSTART:{datetime.fromisoformat(at(day, '10:00').replace('Z', '+00:00')):%Y%m%dT%H%M%SZ}" in text
    assert "ORGANIZER;CN=\"Glow Studio\":mailto:owner@glow.example" in text
    assert "ATTENDEE;CN=\"Maya Lopez\"" in text and "mailto:maya@example.com" in text
    assert "LOCATION:12 Main St" in text and "\r\n" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert "The attached invite adds it to your calendar." in confirmation["body"]

    service.reschedule(db, booking["id"], at(day, "13:00"))
    moved = files_of(db, outbox(db, "booking_rescheduled")[0]["id"])[0]["data"].decode()
    assert "METHOD:REQUEST" in moved and f"UID:{booking['id']}@" in moved and "SEQUENCE:1" in moved

    service.cancel(db, booking["id"], reason="Ana is ill")
    gone = outbox(db, "booking_cancelled")[0]
    cancel = files_of(db, gone["id"])[0]
    assert gone["attachments"][0]["filename"] == "cancel.ics"
    assert cancel["content_type"].startswith("text/calendar; method=CANCEL")
    body = cancel["data"].decode()
    assert "METHOD:CANCEL" in body and "STATUS:CANCELLED" in body and "SEQUENCE:2" in body
    assert f"UID:{booking['id']}@" in body


def test_a_customer_who_cancels_is_sent_the_cancellation_for_their_calendar(db):
    _, cut = setup(db)
    booking = service.book(db, service_id=cut["id"], start=at(future_day(), "11:00"), customer=CUSTOMER, source="public")
    service.cancel(db, booking["id"], by="customer", reason="Travel")
    mine = outbox(db, "booking_cancelled")[0]
    assert "as you asked" in mine["body"] and "Travel" not in mine["body"]
    assert b"METHOD:CANCEL" in files_of(db, mine["id"])[0]["data"]


def test_the_invite_goes_out_as_an_attachment_and_is_then_dropped(db):
    _, cut = setup(db)
    service.book(db, service_id=cut["id"], start=at(future_day(), "10:00"), customer=CUSTOMER, source="public")
    kit.save_email_settings(db, {"from_email": "hello@glow.example", "smtp_host": "smtp.glow.example"})
    sent = []
    result = kit.send_outbox(db, "pw", sender=lambda settings, password, mail: sent.append(mail))
    assert result["sent"] >= 1
    mail = next(m for m in sent if m["Subject"].startswith("Booked"))
    parsed = email.message_from_bytes(mail.as_bytes(), policy=email.policy.default)
    parts = [part for part in parsed.walk() if part.get_content_type() == "text/calendar"]
    assert len(parts) == 1 and parts[0].get_filename() == "invite.ics"
    assert parts[0].get_param("method") == "REQUEST"
    assert b"BEGIN:VCALENDAR" in parts[0].get_payload(decode=True)
    assert "Hi Maya" in parsed.get_body(preferencelist=("plain",)).get_content()
    # what went is in the customer's mailbox: the outbox keeps no second copy of the file
    assert db.execute("SELECT COUNT(*) FROM suite_outbox_files").fetchone()[0] == 0


def test_attachments_are_checked(db):
    kit.ensure_outbox(db)
    with pytest.raises(ValueError, match="too large"):
        kit.queue_email(db, "x", "note", to="a@example.com", subject="s", body="b",
                        attachments=[("big.bin", "application/octet-stream", b"x" * (kit.MAX_ATTACHMENT_BYTES + 1))])
    with pytest.raises(ValueError, match="usable type"):
        kit.queue_email(db, "x", "note", to="a@example.com", subject="s", body="b",
                        attachments=[("a.txt", "not a type", b"x")])
    with pytest.raises(ValueError, match="at most"):
        kit.queue_email(db, "x", "note", to="a@example.com", subject="s", body="b",
                        attachments=[(f"{i}.txt", "text/plain", b"x") for i in range(kit.MAX_ATTACHMENTS + 1)])
    made = kit.queue_email(db, "x", "note", to="a@example.com", subject="s", body="b",
                           attachments=[("../../etc/passwd", "text/plain", "hello")])
    assert made["attachments"][0]["filename"] == "_.._etc_passwd"
    # a withdrawn message's file goes the next time the outbox is tidied
    kit.set_outbox_status(db, [made["id"]], "cancelled")
    kit.expire_outbox(db)
    assert files_of(db, made["id"]) == []


# ---------------------------------------------------------------------------
# reading calendars
# ---------------------------------------------------------------------------


def busy(text, start, end):
    return [(a.astimezone(NY), b.astimezone(NY)) for a, b in ical.busy_times(text, window_start=start, window_end=end, fallback=NY)]


def ny(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY)


def test_recurring_events_exceptions_free_time_and_all_day_events():
    feed = calendar(
        # every Monday and Wednesday 10-11, six times; one Wednesday off, one Monday moved to 15:00
        event("weekly@x", "DTSTART;TZID=America/New_York:20261102T100000", "DTEND;TZID=America/New_York:20261102T110000",
              "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=6", "EXDATE;TZID=America/New_York:20261104T100000"),
        event("weekly@x", "RECURRENCE-ID;TZID=America/New_York:20261109T100000",
              "DTSTART;TZID=America/New_York:20261109T150000", "DTEND;TZID=America/New_York:20261109T160000"),
        # shown as free: not busy
        event("free@x", "DTSTART:20261103T140000Z", "DTEND:20261103T150000Z", "TRANSP:TRANSPARENT"),
        # cancelled: not busy
        event("gone@x", "DTSTART:20261103T160000Z", "DTEND:20261103T170000Z", "STATUS:CANCELLED"),
        # all day on the 5th (a day off)
        event("allday@x", "DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261106"),
        # an all-day event shown as free (a birthday) is not busy
        event("birthday@x", "DTSTART;VALUE=DATE:20261106", "TRANSP:TRANSPARENT"),
    )
    found = busy(feed, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 30, tzinfo=UTC))
    assert found == [
        (ny(2026, 11, 2, 10), ny(2026, 11, 2, 11)),
        (ny(2026, 11, 5), ny(2026, 11, 6)),
        (ny(2026, 11, 9, 15), ny(2026, 11, 9, 16)),
        (ny(2026, 11, 11, 10), ny(2026, 11, 11, 11)),
        (ny(2026, 11, 16, 10), ny(2026, 11, 16, 11)),
        (ny(2026, 11, 18, 10), ny(2026, 11, 18, 11)),
    ]


def test_daily_monthly_until_and_clock_changes():
    feed = calendar(
        # every other day at 08:00 New York time, through the November clock change (Outlook's zone name)
        event("daily@x", "DTSTART;TZID=Eastern Standard Time:20261028T080000",
              "DTEND;TZID=Eastern Standard Time:20261028T083000", "RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20261105T235959Z"),
        # the last Friday of every month at 12:00 UTC, for 30 minutes
        event("monthly@x", "DTSTART:20260130T120000Z", "DURATION:PT30M", "RRULE:FREQ=MONTHLY;BYDAY=-1FR"),
        # the 31st of each month: months without one are skipped
        event("day31@x", "DTSTART:20260131T150000Z", "DTEND:20260131T160000Z", "RRULE:FREQ=MONTHLY;BYMONTHDAY=31;COUNT=4"),
    )
    found = busy(feed, datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 11, 10, tzinfo=UTC))
    assert (ny(2026, 10, 28, 8), ny(2026, 10, 28, 8, 30)) in found
    assert (ny(2026, 11, 1, 8), ny(2026, 11, 1, 8, 30)) in found  # after the change: still 08:00 local
    assert (ny(2026, 11, 5, 8), ny(2026, 11, 5, 8, 30)) in found
    assert not any(a.day == 7 and a.hour == 8 for a, _ in found)  # UNTIL ended it
    friday = datetime(2026, 10, 30, 12, tzinfo=UTC).astimezone(NY)
    assert (friday, friday + timedelta(minutes=30)) in found
    # Jan 31, Mar 31, May 31, Jul 31 (COUNT=4): none in this window
    assert not any(a.minute == 0 and a.astimezone(UTC).hour == 15 for a, _ in found)
    later = busy(feed, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))
    assert any(a.astimezone(UTC) == datetime(2026, 7, 31, 15, tzinfo=UTC) for a, _ in later)
    assert not any(a.astimezone(UTC).month == 8 and a.astimezone(UTC).hour == 15 for a, _ in later)


def test_folded_lines_floating_times_and_not_a_calendar():
    feed = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:fold@x\r\nSUMMARY:A very long summary that a calendar\r\n  folds over two lines\r\n"
        "DTSTART:20261110T090000\r\nDTEND:20261110T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert busy(feed, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 30, tzinfo=UTC)) == [
        (ny(2026, 11, 10, 9), ny(2026, 11, 10, 10))  # floating: the business's own time
    ]
    with pytest.raises(ValueError, match="did not give a calendar"):
        ical.busy_times("<html>login</html>", window_start=datetime.now(UTC), window_end=datetime.now(UTC), fallback=NY)


def test_a_staff_calendar_takes_its_busy_times_out_of_availability(db, feeds):
    person, cut = setup(db)
    day = future_day(5)
    url = feeds.put(
        "/ana/basic.ics",
        calendar(
            event("dentist@x", f"DTSTART;TZID=America/New_York:{stamp(day, '10:00')}",
                  f"DTEND;TZID=America/New_York:{stamp(day, '11:30')}"),
            event("lunch@x", f"DTSTART;TZID=America/New_York:{stamp(day, '13:00')}",
                  f"DTEND;TZID=America/New_York:{stamp(day, '14:00')}", "TRANSP:TRANSPARENT"),
        ),
    )
    before = [s["local"] for s in service.slots_for_day(db, cut["id"], day.isoformat())]
    assert "10:00" in before
    feed = calendars.set_feed(db, person["id"], url)
    assert feed["ok"] and feed["busy_count"] == 1 and feed["last_synced_at"]
    assert feed["address"].endswith("/…/basic.ics") and "ana" not in feed["address"]
    after = [s["local"] for s in service.slots_for_day(db, cut["id"], day.isoformat())]
    assert "09:00" in after and "10:00" not in after and "11:00" not in after
    assert "09:15" not in after  # a 60-minute cut from 09:15 would run into 10:00
    assert "11:30" in after and "13:00" in after  # free time (TRANSPARENT) stays bookable
    with pytest.raises(ValueError, match="no longer available"):
        service.book(db, service_id=cut["id"], start=at(day, "10:30"), customer=CUSTOMER, source="public")
    # the staff list shows when it was read
    listed = store.get_staff(db, person["id"])["calendar"]
    assert listed["busy_count"] == 1 and listed["last_error"] == ""
    # a calendar that blocks a whole day makes the day closed, not full (there is nothing to wait for)
    feeds.put("/ana/basic.ics", calendar(event("off@x", f"DTSTART;VALUE=DATE:{day:%Y%m%d}")))
    calendars.sync_feed(db, person["id"])
    states = service.day_states(db, cut["id"], day.isoformat(), 1)
    assert states[0]["slots"] == [] and states[0]["state"] == "closed"


def test_a_calendar_that_cannot_be_read_keeps_the_last_busy_times_and_says_why(db, feeds):
    person, cut = setup(db)
    day = future_day(3)
    url = feeds.put("/cal.ics", calendar(event("a@x", f"DTSTART;TZID=America/New_York:{stamp(day, '09:00')}",
                                               f"DTEND;TZID=America/New_York:{stamp(day, '10:00')}")))
    calendars.set_feed(db, person["id"], url)
    feeds.feeds["/cal.ics"] = (404, b"gone", {})
    result = calendars.sync_feed(db, person["id"])
    assert not result["ok"] and "404" in result["error"] and "Copy it again" in result["error"]
    feed = store.calendar_feed(db, person["id"])
    assert feed["last_error"] and feed["busy_count"] == 1 and not feed["ok"]
    assert "09:00" not in [s["local"] for s in service.slots_for_day(db, cut["id"], day.isoformat())]
    feeds.put("/cal.ics", "<html>Sign in</html>")
    assert "did not give a calendar" in calendars.sync_feed(db, person["id"])["error"]
    calendars.remove_feed(db, person["id"])
    assert store.calendar_feed(db, person["id"]) is None
    assert "09:00" in [s["local"] for s in service.slots_for_day(db, cut["id"], day.isoformat())]


def test_the_background_pass_reads_calendars_every_half_hour(db, feeds):
    person, _ = setup(db)
    url = feeds.put("/x.ics", calendar())
    calendars.set_feed(db, person["id"], url)
    assert len(feeds.calls) == 1
    assert background.TASKS["bookings"](db) == {"synced": 0, "failed": 0}  # read just now
    later = kit.utcnow() + timedelta(minutes=calendars.SYNC_MINUTES + 1)
    assert calendars.sync_due(db, now=later) == {"synced": 1, "failed": 0}
    assert len(feeds.calls) == 2
    store.update_staff(db, person["id"], {"active": False})
    assert calendars.sync_due(db, now=later + timedelta(hours=1)) == {"synced": 0, "failed": 0}


# ---------------------------------------------------------------------------
# the safe reader
# ---------------------------------------------------------------------------


def test_the_reader_refuses_what_it_must(monkeypatch, feeds):
    monkeypatch.delenv(webfetch.ALLOW_LOCAL_ENV)
    for bad in ("http://calendar.example.com/a.ics", "ftp://x/a.ics", "https://user:pw@example.com/a.ics", "file:///etc/passwd"):
        with pytest.raises(webfetch.FetchError, match="https://"):
            webfetch.fetch(bad)
    with pytest.raises(webfetch.FetchError, match="not a public address"):
        webfetch.fetch("https://127.0.0.1/a.ics")
    with pytest.raises(webfetch.FetchError, match="not a public address"):
        webfetch.fetch("https://10.1.2.3/a.ics")
    assert webfetch.normalise("webcal://p01-calendars.icloud.com/x") == "https://p01-calendars.icloud.com/x"
    with pytest.raises(ValueError, match="https://"):
        calendars.check_address("http://example.com/a.ics")


def test_the_reader_checks_every_redirect_and_caps_the_size(monkeypatch, feeds):
    feeds.put("/to-private", "", status=302, Location="http://10.0.0.7/cal.ics")
    with pytest.raises(webfetch.FetchError, match="not a public address"):
        webfetch.fetch(feeds.base + "/to-private")  # a private host, even with the test switch
    feeds.put("/to-private-https", "", status=302, Location="https://192.168.1.1/cal.ics")
    with pytest.raises(webfetch.FetchError, match="not a public address"):
        webfetch.fetch(feeds.base + "/to-private-https")
    feeds.put("/real.ics", calendar())
    feeds.put("/hop", "", status=301, Location="/real.ics")
    assert b"BEGIN:VCALENDAR" in webfetch.fetch(feeds.base + "/hop")
    feeds.put("/loop", "", status=302, Location="/loop")
    with pytest.raises(webfetch.FetchError, match="too many"):
        webfetch.fetch(feeds.base + "/loop")
    feeds.feeds["/huge.ics"] = (200, b"x" * 5000, {})
    with pytest.raises(webfetch.FetchError, match="too large"):
        webfetch.fetch(feeds.base + "/huge.ics", max_bytes=1000)


# ---------------------------------------------------------------------------
# the single-user server: staff calendar routes and "Add to calendar"
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    import auto_apply_assistant as local

    public.set_index(local._public_link_index)


def anonymous_raw(env, path):
    request = urllib.request.Request(env.base + path, method="GET")
    request.add_header("X-Requested-With", "aira")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_routes_connect_a_calendar_and_customers_add_their_booking(env, api, local_index, feeds):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow Studio", "email": "owner@glow.example", "timezone": "UTC"})
    person = api.post("/api/bookings/staff", {"name": "Ana", "hours": ALL_WEEK}, expect=201)["person"]
    cut = api.post("/api/bookings/services", {"name": "Cut", "minutes": 60, "staff_ids": [person["id"]]}, expect=201)["service"]
    day = datetime.now(UTC).date() + timedelta(days=6)
    url = feeds.put("/ana.ics", calendar(event("x@x", f"DTSTART:{stamp(day, '09:00')}Z", f"DTEND:{stamp(day, '10:00')}Z")))
    connected = api.post(f"/api/bookings/staff/{person['id']}/calendar", {"url": url})["calendar"]
    assert connected["ok"] and connected["busy_count"] == 1 and "127.0.0.1/…/ana.ics" == connected["address"]
    staff = api.get("/api/bookings/staff")["staff"][0]
    assert staff["calendar"]["last_synced_at"] and url not in str(staff)
    slots = api.get(f"/api/bookings/slots?service_id={cut['id']}&day={day.isoformat()}&days=1")
    assert "09:00" not in [s["local"] for s in slots["days"][0]["slots"]]
    synced = api.post(f"/api/bookings/staff/{person['id']}/calendar/sync", {})
    assert synced["result"]["ok"]
    status, refused = api.call("POST", f"/api/bookings/staff/{person['id']}/calendar", {"url": "ftp://x"})
    assert status == 400 and "https://" in refused["error"]

    made = api.post(
        "/api/bookings/bookings",
        {"service_id": cut["id"], "start": f"{day.isoformat()}T11:00:00Z", "name": "Maya", "email": "maya@example.com"},
        expect=201,
    )["booking"]
    token = api.post(f"/api/bookings/bookings/{made['id']}/link", {})["link"]["token"]
    status, headers, body = anonymous_raw(env, f"/api/public/{token}/calendar.ics")
    assert status == 200 and headers["Content-Type"].startswith("text/calendar")
    assert f"{made['reference']}.ics" in headers.get("Content-Disposition", "")
    text = body.decode()
    assert "METHOD:PUBLISH" in text and f"UID:{made['id']}@" in text and "ATTENDEE" not in text
    assert f"/p/{token}" in text  # the way back to manage it
    page_token = api.post("/api/bookings/page-link", {})["link"]["token"]
    assert anonymous_raw(env, f"/api/public/{page_token}/calendar.ics")[0] == 404
    api.call("DELETE", f"/api/bookings/staff/{person['id']}/calendar")
    assert api.get("/api/bookings/staff")["staff"][0]["calendar"] is None
