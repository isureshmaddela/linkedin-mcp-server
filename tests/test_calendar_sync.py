from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from aira.calendar_sync import (
    CalendarEvent,
    build_calendar,
    build_ics,
    cancel,
    escape_text,
    event_fingerprint,
    fold_line,
    google_calendar_link,
    local_time_exists,
    next_sequence,
    office365_calendar_link,
    outlook_calendar_link,
    parse_ics_datetime,
    reminder_times,
    reschedule,
    to_timezone,
)

LA = ZoneInfo("America/Los_Angeles")
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
STAMP = datetime(2026, 2, 1, 10, 30, tzinfo=UTC)


def make_event(**overrides) -> CalendarEvent:
    fields = {
        "uid": "interview-123@aira",
        "title": "Interview with Acme",
        "start": datetime(2026, 3, 3, 14, 0, tzinfo=LA),
        "end": datetime(2026, 3, 3, 15, 0, tzinfo=LA),
        "timezone": "America/Los_Angeles",
        "description": "Round 2 with the hiring manager.",
        "location": "123 Main St, San Jose",
        "url": "https://example.com/interview",
        "attendees": ("recruiter@example.com",),
        "organizer_email": "me@example.com",
    }
    fields.update(overrides)
    return CalendarEvent(**fields)


def physical_lines(ics: str) -> list[str]:
    assert ics.endswith("\r\n")
    return ics[:-2].split("\r\n")


def unfold(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").rstrip("\r\n").split("\r\n")


def test_ics_uses_tzid_local_times_and_utc_dtstamp():
    ics = build_ics(make_event(), now=STAMP)
    lines = unfold(ics)

    assert lines[0] == "BEGIN:VCALENDAR"
    assert lines[-1] == "END:VCALENDAR"
    assert "VERSION:2.0" in lines
    assert "PRODID:-//Aira//Career Copilot//EN" in lines
    assert "DTSTART;TZID=America/Los_Angeles:20260303T140000" in lines
    assert "DTEND;TZID=America/Los_Angeles:20260303T150000" in lines
    assert "DTSTAMP:20260201T103000Z" in lines
    assert "UID:interview-123@aira" in lines
    assert "SEQUENCE:0" in lines
    assert "STATUS:CONFIRMED" in lines
    assert "SUMMARY:Interview with Acme" in lines
    assert "DESCRIPTION:Round 2 with the hiring manager." in lines
    assert "LOCATION:123 Main St\\, San Jose" in lines
    assert "URL:https://example.com/interview" in lines
    assert "ORGANIZER;CN=me@example.com:mailto:me@example.com" in lines
    assert (
        "ATTENDEE;CN=recruiter@example.com;ROLE=REQ-PARTICIPANT;"
        "PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:recruiter@example.com"
    ) in lines
    assert "TRIGGER:-PT60M" in lines
    assert "TRIGGER:-PT1440M" in lines
    assert lines.count("BEGIN:VALARM") == 2
    assert lines.count("ACTION:DISPLAY") == 2
    assert "METHOD:" not in ics
    # Strict CRLF endings everywhere.
    assert "\n" not in ics.replace("\r\n", "")

    default_stamp = [
        line for line in unfold(build_ics(make_event())) if line.startswith("DTSTAMP:")
    ]
    assert len(default_stamp) == 1
    assert default_stamp[0].endswith("Z")
    assert len(default_stamp[0]) == len("DTSTAMP:20260201T103000Z")


def test_ics_dtstart_uses_display_timezone_not_source_timezone():
    event = make_event(
        start=datetime(2026, 3, 3, 22, 0, tzinfo=UTC),
        end=datetime(2026, 3, 3, 23, 0, tzinfo=UTC),
        timezone="America/New_York",
    )
    lines = unfold(build_ics(event, now=STAMP))
    assert "DTSTART;TZID=America/New_York:20260303T170000" in lines
    assert "DTEND;TZID=America/New_York:20260303T180000" in lines


def test_google_link_dates_are_utc():
    link = google_calendar_link(make_event())
    parts = urlsplit(link)
    assert parts.scheme == "https"
    assert parts.netloc == "calendar.google.com"
    assert parts.path == "/calendar/render"
    assert "dates=20260303T220000Z/20260303T230000Z" in link
    query = parse_qs(parts.query)
    assert query["action"] == ["TEMPLATE"]
    assert query["text"] == ["Interview with Acme"]
    assert query["dates"] == ["20260303T220000Z/20260303T230000Z"]
    assert query["details"] == ["Round 2 with the hiring manager."]
    assert query["location"] == ["123 Main St, San Jose"]
    assert query["ctz"] == ["America/Los_Angeles"]
    assert query["add"] == ["recruiter@example.com"]
    assert " " not in link


def test_outlook_and_office365_links_use_local_time_with_offset():
    for builder, host in (
        (outlook_calendar_link, "outlook.live.com"),
        (office365_calendar_link, "outlook.office.com"),
    ):
        link = builder(make_event())
        parts = urlsplit(link)
        assert parts.netloc == host
        assert parts.path == "/calendar/0/action/compose"
        query = parse_qs(parts.query)
        assert query["rru"] == ["addevent"]
        assert query["subject"] == ["Interview with Acme"]
        assert query["startdt"] == ["2026-03-03T14:00:00-08:00"]
        assert query["enddt"] == ["2026-03-03T15:00:00-08:00"]
        assert query["body"] == ["Round 2 with the hiring manager."]
        assert query["location"] == ["123 Main St, San Jose"]
        assert query["to"] == ["recruiter@example.com"]
        assert " " not in link


def test_long_lines_are_folded_at_75_octets():
    description = (
        "Bring two forms of ID and the signed NDA. " * 8
    ) + "Café ünïcode ✓ " * 6
    ics = build_ics(make_event(description=description), now=STAMP)

    for line in physical_lines(ics):
        assert len(line.encode("utf-8")) <= 75, line
    folded = [line for line in physical_lines(ics) if line.startswith(" ")]
    assert folded, "expected at least one continuation line"
    assert all(line.startswith(" ") for line in folded)

    unfolded = unfold(ics)
    assert f"DESCRIPTION:{escape_text(description)}" in unfolded
    assert "✓" in "".join(unfolded)


def test_fold_line_helper_keeps_multibyte_characters_intact():
    line = "X:" + "é" * 100
    folded = fold_line(line)
    for piece in folded.split("\r\n"):
        assert len(piece.encode("utf-8")) <= 75
        piece.encode("utf-8").decode("utf-8")
    assert folded.replace("\r\n ", "") == line
    assert fold_line("short") == "short"


def test_text_escaping():
    assert escape_text("a,b;c\\d\ne\r\nf") == "a\\,b\\;c\\\\d\\ne\\nf"
    event = make_event(
        title="Interview: Acme, Inc; Round 2",
        description="Line one\nLine two",
        location="C:\\conference",
    )
    lines = unfold(build_ics(event, now=STAMP))
    assert "SUMMARY:Interview: Acme\\, Inc\\; Round 2" in lines
    assert "DESCRIPTION:Line one\\nLine two" in lines
    assert "LOCATION:C:\\\\conference" in lines


def test_cancel_emits_cancel_method_and_bumps_sequence():
    event = make_event(sequence=2)
    cancelled = cancel(event)

    assert cancelled.status == "cancelled"
    assert cancelled.sequence == 3
    assert cancelled.uid == event.uid
    assert event.status == "confirmed"
    assert cancel(cancelled) is cancelled

    lines = unfold(build_ics(cancelled, now=STAMP))
    assert "METHOD:CANCEL" in lines
    assert "STATUS:CANCELLED" in lines
    assert "SEQUENCE:3" in lines
    assert "BEGIN:VALARM" not in lines
    assert reminder_times(cancelled) == []

    explicit = unfold(build_ics(event, method="request", now=STAMP))
    assert "METHOD:REQUEST" in explicit
    assert "STATUS:CONFIRMED" in explicit


def test_reschedule_increments_sequence_and_keeps_uid():
    event = make_event(sequence=4, status="tentative")
    moved = reschedule(
        event,
        start=datetime(2026, 3, 4, 9, 0, tzinfo=LA),
        end=datetime(2026, 3, 4, 10, 0, tzinfo=LA),
    )
    assert moved.uid == event.uid
    assert moved.sequence == 5
    assert moved.status == "confirmed"
    assert moved.start == datetime(2026, 3, 4, 9, 0, tzinfo=LA)
    assert event.sequence == 4
    assert "DTSTART;TZID=America/Los_Angeles:20260304T090000" in unfold(
        build_ics(moved, now=STAMP)
    )
    assert next_sequence(0) == 1
    with pytest.raises(ValueError):
        next_sequence(-1)
    with pytest.raises(ValueError):
        reschedule(
            event,
            start=datetime(2026, 3, 4, 10, 0, tzinfo=LA),
            end=datetime(2026, 3, 4, 9, 0, tzinfo=LA),
        )


def test_fingerprint_is_instant_based():
    la_event = make_event()
    ny_event = make_event(
        start=datetime(2026, 3, 3, 17, 0, tzinfo=NY),
        end=datetime(2026, 3, 3, 18, 0, tzinfo=NY),
        timezone="America/New_York",
    )
    utc_event = make_event(
        start=datetime(2026, 3, 3, 22, 0, tzinfo=UTC),
        end=datetime(2026, 3, 3, 23, 0, tzinfo=UTC),
        timezone="UTC",
        sequence=7,
        description="different description does not matter",
    )
    assert event_fingerprint(la_event) == event_fingerprint(ny_event)
    assert event_fingerprint(la_event) == event_fingerprint(utc_event)
    assert len(event_fingerprint(la_event)) == 64

    moved = reschedule(
        la_event,
        start=la_event.start + timedelta(hours=1),
        end=la_event.end + timedelta(hours=1),
    )
    assert event_fingerprint(moved) != event_fingerprint(la_event)
    assert event_fingerprint(make_event(title="Other")) != event_fingerprint(la_event)
    assert event_fingerprint(make_event(uid="other@aira")) != event_fingerprint(
        la_event
    )
    assert event_fingerprint(make_event(location="Elsewhere")) != event_fingerprint(
        la_event
    )


def test_dst_transitions_are_handled_correctly():
    before = make_event(
        start=datetime(2026, 3, 7, 9, 0, tzinfo=NY),
        end=datetime(2026, 3, 7, 10, 0, tzinfo=NY),
        timezone="America/New_York",
    )
    after = make_event(
        start=datetime(2026, 3, 8, 9, 0, tzinfo=NY),
        end=datetime(2026, 3, 8, 10, 0, tzinfo=NY),
        timezone="America/New_York",
    )
    assert before.start.astimezone(UTC) == datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
    assert after.start.astimezone(UTC) == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
    assert "dates=20260307T140000Z/20260307T150000Z" in google_calendar_link(before)
    assert "dates=20260308T130000Z/20260308T140000Z" in google_calendar_link(after)
    assert parse_qs(urlsplit(outlook_calendar_link(before)).query)["startdt"] == [
        "2026-03-07T09:00:00-05:00"
    ]
    assert parse_qs(urlsplit(outlook_calendar_link(after)).query)["startdt"] == [
        "2026-03-08T09:00:00-04:00"
    ]

    assert local_time_exists(datetime(2026, 3, 8, 9, 0), "America/New_York")
    assert not local_time_exists(datetime(2026, 3, 8, 2, 30), "America/New_York")
    assert local_time_exists(datetime(2026, 11, 1, 1, 30), "America/New_York")
    with pytest.raises(ValueError, match="does not exist"):
        make_event(
            start=datetime(2026, 3, 8, 2, 30, tzinfo=NY),
            end=datetime(2026, 3, 8, 3, 30, tzinfo=NY),
            timezone="America/New_York",
        )


def test_invalid_events_raise_value_error():
    with pytest.raises(ValueError, match="time zone"):
        make_event(timezone="Mars/Olympus_Mons")
    with pytest.raises(ValueError, match="after its start"):
        make_event(end=datetime(2026, 3, 3, 14, 0, tzinfo=LA))
    with pytest.raises(ValueError, match="after its start"):
        make_event(end=datetime(2026, 3, 3, 13, 0, tzinfo=LA))
    with pytest.raises(ValueError, match="aware"):
        make_event(start=datetime(2026, 3, 3, 14, 0))
    with pytest.raises(ValueError, match="aware"):
        make_event(end=datetime(2026, 3, 3, 15, 0))
    with pytest.raises(ValueError, match="uid"):
        make_event(uid="")
    with pytest.raises(ValueError, match="title"):
        make_event(title=" ")
    with pytest.raises(ValueError, match="status"):
        make_event(status="maybe")
    with pytest.raises(ValueError, match="sequence"):
        make_event(sequence=-1)
    with pytest.raises(ValueError, match="attendee"):
        make_event(attendees=("not-an-email",))
    with pytest.raises(ValueError, match="organizer"):
        make_event(organizer_email="nope")
    with pytest.raises(ValueError, match="Reminder"):
        make_event(reminders_minutes=(-5,))
    with pytest.raises(ValueError, match="aware"):
        to_timezone(datetime(2026, 3, 3, 14, 0), "UTC")
    with pytest.raises(ValueError, match="time zone"):
        to_timezone(datetime(2026, 3, 3, 14, 0, tzinfo=UTC), "Nowhere/Land")
    with pytest.raises(ValueError, match="At least one event"):
        build_calendar([])


def test_lists_are_coerced_to_tuples():
    event = make_event(
        attendees=["a@example.com", "b@example.com"], reminders_minutes=[15]
    )
    assert event.attendees == ("a@example.com", "b@example.com")
    assert event.reminders_minutes == (15,)
    assert "TRIGGER:-PT15M" in unfold(build_ics(event, now=STAMP))
    zero = make_event(reminders_minutes=(0,))
    assert "TRIGGER:PT0M" in unfold(build_ics(zero, now=STAMP))


def test_parse_ics_datetime_variants():
    utc_value = parse_ics_datetime("20260303T220000Z")
    assert utc_value == datetime(2026, 3, 3, 22, 0, tzinfo=UTC)
    assert utc_value.tzinfo is UTC

    local_value = parse_ics_datetime("20260303T140000", "America/Los_Angeles")
    assert local_value == utc_value
    assert local_value.tzinfo == LA
    assert local_value.hour == 14

    # A Z value stays UTC even when a TZID is supplied.
    assert parse_ics_datetime("20260303T220000Z", "America/Los_Angeles") == utc_value

    midnight = parse_ics_datetime("20260303", "America/Los_Angeles")
    assert midnight == datetime(2026, 3, 3, 0, 0, tzinfo=LA)
    assert parse_ics_datetime("20260303") == datetime(2026, 3, 3, tzinfo=UTC)
    assert parse_ics_datetime(" 20260303T140000 ") == datetime(
        2026, 3, 3, 14, 0, tzinfo=UTC
    )

    for bad in (
        "",
        "2026-03-03",
        "20260303T14",
        "20261303T140000",
        "20260303T250000Z",
        "abc",
    ):
        with pytest.raises(ValueError):
            parse_ics_datetime(bad)
    with pytest.raises(ValueError, match="time zone"):
        parse_ics_datetime("20260303T140000", "Not/AZone")


def test_reminder_times_are_utc():
    times = reminder_times(make_event())
    assert times == [
        datetime(2026, 3, 3, 21, 0, tzinfo=UTC),
        datetime(2026, 3, 2, 22, 0, tzinfo=UTC),
    ]
    assert all(value.tzinfo is UTC for value in times)
    assert reminder_times(make_event(reminders_minutes=())) == []


def test_to_timezone_converts_instants():
    converted = to_timezone(
        datetime(2026, 3, 3, 22, 0, tzinfo=UTC), "America/Los_Angeles"
    )
    assert converted == datetime(2026, 3, 3, 14, 0, tzinfo=LA)
    assert converted.tzinfo == LA
    assert converted.hour == 14


def test_build_calendar_contains_all_events():
    first = make_event()
    second = make_event(
        uid="screening-456@aira",
        title="Recruiter screen",
        start=datetime(2026, 3, 5, 10, 0, tzinfo=NY),
        end=datetime(2026, 3, 5, 10, 30, tzinfo=NY),
        timezone="America/New_York",
    )
    ics = build_calendar([first, second], now=STAMP)
    lines = unfold(ics)
    assert lines.count("BEGIN:VCALENDAR") == 1
    assert lines.count("END:VCALENDAR") == 1
    assert lines.count("BEGIN:VEVENT") == 2
    assert lines.count("END:VEVENT") == 2
    assert "UID:interview-123@aira" in lines
    assert "UID:screening-456@aira" in lines
    assert "DTSTART;TZID=America/New_York:20260305T100000" in lines
    assert "METHOD:" not in ics

    all_cancelled = build_calendar([cancel(first), cancel(second)], now=STAMP)
    assert "METHOD:CANCEL" in unfold(all_cancelled)
    mixed = build_calendar([first, cancel(second)], now=STAMP)
    assert "METHOD:" not in mixed
    requested = build_calendar([first, second], method="REQUEST", now=STAMP)
    assert "METHOD:REQUEST" in unfold(requested)
