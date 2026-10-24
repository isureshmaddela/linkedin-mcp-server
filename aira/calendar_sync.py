"""Standards-based calendar synchronisation for the Aira Career Copilot.

No OAuth provider is available in this environment, so synchronisation is
file- and link-based: this module renders RFC 5545 iCalendar content (with
``METHOD:REQUEST`` / ``METHOD:CANCEL``, stable UIDs, SEQUENCE increments and
VALARM reminders) and builds "add to calendar" deep links for Google, Outlook
and Office 365. Everything here is a pure function over :class:`CalendarEvent`
values; time zones come from :mod:`zoneinfo`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "CalendarEvent",
    "EVENT_STATUSES",
    "build_calendar",
    "build_ics",
    "cancel",
    "escape_text",
    "event_fingerprint",
    "fold_line",
    "google_calendar_link",
    "local_time_exists",
    "next_sequence",
    "office365_calendar_link",
    "outlook_calendar_link",
    "parse_ics_datetime",
    "reminder_times",
    "reschedule",
    "to_timezone",
]

EVENT_STATUSES: tuple[str, ...] = ("confirmed", "cancelled", "tentative")
_STATUS_TO_ICS = {
    "confirmed": "CONFIRMED",
    "cancelled": "CANCELLED",
    "tentative": "TENTATIVE",
}
_ICS_LINE_LIMIT = 75
_UTC_FORMAT = "%Y%m%dT%H%M%SZ"
_LOCAL_FORMAT = "%Y%m%dT%H%M%S"
_EMAIL_RE = re.compile(r"^[^\s@\"'<>,;:]+@[^\s@\"'<>,;:]+\.[^\s@\"'<>,;:]+$")
_DATE_RE = re.compile(r"^\d{8}$")
_DATETIME_RE = re.compile(r"^\d{8}T\d{6}(Z?)$")


# --------------------------------------------------------------------------- #
# Time zone helpers
# --------------------------------------------------------------------------- #


def _zone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("A time zone name such as 'America/New_York' is required.")
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError) as exc:
        raise ValueError(f"Unknown time zone: {timezone_name!r}.") from exc


def _is_aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _require_aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"The {label} must be a datetime.")
    if not _is_aware(value):
        raise ValueError(f"The {label} must be time-zone aware.")
    return value


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def to_timezone(value: datetime, timezone: str) -> datetime:
    """Convert a time-zone-aware datetime to the given IANA time zone."""
    _require_aware(value, "value")
    return value.astimezone(_zone(timezone))


def local_time_exists(naive: datetime, timezone: str) -> bool:
    """Return whether a wall-clock time exists in ``timezone``.

    Times skipped by a daylight-saving transition (for example 02:30 on
    2026-03-08 in America/New_York) do not exist and return ``False``.
    """
    if not isinstance(naive, datetime):
        raise ValueError("The local time must be a datetime.")
    if naive.tzinfo is not None:
        naive = naive.replace(tzinfo=None)
    zone = _zone(timezone)
    aware = naive.replace(tzinfo=zone)
    round_trip = _as_utc(aware).astimezone(zone).replace(tzinfo=None)
    return round_trip == naive


def _exists_in_own_zone(value: datetime) -> bool:
    """True unless ``value`` names a wall-clock time its own tzinfo skips."""
    round_trip = _as_utc(value).astimezone(value.tzinfo)
    return round_trip.replace(tzinfo=None) == value.replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Event model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """A single calendar event with everything needed to render it.

    ``start`` and ``end`` must be time-zone aware. ``timezone`` is the IANA
    zone the event is displayed in (DTSTART/DTEND are rendered in that zone).
    """

    uid: str
    title: str
    start: datetime
    end: datetime
    timezone: str
    description: str = ""
    location: str = ""
    url: str = ""
    attendees: tuple[str, ...] = ()
    sequence: int = 0
    status: str = "confirmed"
    reminders_minutes: tuple[int, ...] = (60, 24 * 60)
    organizer_email: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.uid, str) or not self.uid.strip():
            raise ValueError("The event uid must be a non-empty string.")
        if any(ord(char) < 32 for char in self.uid):
            raise ValueError("The event uid may not contain control characters.")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("The event title must be a non-empty string.")
        _zone(self.timezone)
        _require_aware(self.start, "event start")
        _require_aware(self.end, "event end")
        if self.end <= self.start:
            raise ValueError("The event end must be after its start.")
        for label, value in (("start", self.start), ("end", self.end)):
            if not _exists_in_own_zone(value):
                raise ValueError(
                    f"The event {label} names a local time that does not exist "
                    "because of a daylight-saving transition."
                )
        if self.status not in EVENT_STATUSES:
            raise ValueError(
                "The event status must be one of: " + ", ".join(EVENT_STATUSES) + "."
            )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise ValueError("The event sequence must be a whole number.")
        if self.sequence < 0:
            raise ValueError("The event sequence may not be negative.")
        for text_label in ("description", "location", "url", "organizer_email"):
            if not isinstance(getattr(self, text_label), str):
                raise ValueError(f"The event {text_label} must be text.")
        if self.organizer_email and not _EMAIL_RE.match(self.organizer_email):
            raise ValueError("The organizer email address is not valid.")
        if self.url and (any(ord(char) < 32 for char in self.url) or " " in self.url):
            raise ValueError(
                "The event url may not contain spaces or control characters."
            )

        attendees = tuple(self.attendees)
        for attendee in attendees:
            if not isinstance(attendee, str) or not _EMAIL_RE.match(attendee):
                raise ValueError(
                    f"Each attendee must be a valid email address: {attendee!r} is not."
                )
        object.__setattr__(self, "attendees", attendees)

        reminders = tuple(self.reminders_minutes)
        for minutes in reminders:
            if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
                raise ValueError(
                    "Reminder offsets must be whole numbers of minutes, zero or more."
                )
        object.__setattr__(self, "reminders_minutes", reminders)

    @property
    def zone(self) -> ZoneInfo:
        """The event's display time zone."""
        return _zone(self.timezone)

    @property
    def local_start(self) -> datetime:
        """Start converted to the event's display time zone."""
        return self.start.astimezone(self.zone)

    @property
    def local_end(self) -> datetime:
        """End converted to the event's display time zone."""
        return self.end.astimezone(self.zone)


def next_sequence(previous: int) -> int:
    """Return the next iTIP SEQUENCE value."""
    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        raise ValueError("The previous sequence must be a whole number, zero or more.")
    return previous + 1


def reschedule(
    event: CalendarEvent, *, start: datetime, end: datetime
) -> CalendarEvent:
    """Return a copy moved to ``start``/``end`` with SEQUENCE incremented."""
    return replace(
        event,
        start=start,
        end=end,
        sequence=next_sequence(event.sequence),
        status="confirmed",
    )


def cancel(event: CalendarEvent) -> CalendarEvent:
    """Return a cancelled copy with SEQUENCE incremented.

    Cancelling an event that is already cancelled returns it unchanged so the
    sequence does not drift on repeated calls.
    """
    if event.status == "cancelled":
        return event
    return replace(event, status="cancelled", sequence=next_sequence(event.sequence))


def event_fingerprint(event: CalendarEvent) -> str:
    """Stable SHA-256 over uid, UTC start/end, title and location.

    Two representations of the same instant (for example 14:00 Los Angeles
    and 17:00 New York) produce the same fingerprint, so callers can detect
    duplicate events before creating them again.
    """
    material = "\n".join(
        (
            event.uid.strip(),
            _as_utc(event.start).strftime(_UTC_FORMAT),
            _as_utc(event.end).strftime(_UTC_FORMAT),
            event.title.strip(),
            event.location.strip(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def reminder_times(event: CalendarEvent) -> list[datetime]:
    """UTC datetimes at which each reminder fires (empty for cancelled events)."""
    if event.status == "cancelled":
        return []
    start = _as_utc(event.start)
    return [start - timedelta(minutes=minutes) for minutes in event.reminders_minutes]


# --------------------------------------------------------------------------- #
# iCalendar rendering
# --------------------------------------------------------------------------- #


def escape_text(value: str) -> str:
    """Escape a value for an iCalendar TEXT property (RFC 5545 section 3.3.11)."""
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_line(line: str, limit: int = _ICS_LINE_LIMIT) -> str:
    """Fold a content line at ``limit`` octets (RFC 5545 section 3.1).

    Continuation lines begin with a single space and the split never falls
    inside a multi-byte UTF-8 character.
    """
    if len(line.encode("utf-8")) <= limit:
        return line
    pieces: list[str] = []
    current: list[str] = []
    used = 0
    budget = limit
    for char in line:
        size = len(char.encode("utf-8"))
        if used + size > budget:
            pieces.append("".join(current))
            current = []
            used = 0
            budget = limit - 1
        current.append(char)
        used += size
    if current:
        pieces.append("".join(current))
    return "\r\n ".join(pieces)


def _param_value(value: str) -> str:
    """Quote a parameter value when RFC 5545 requires it."""
    cleaned = value.replace('"', "")
    if any(char in cleaned for char in (";", ":", ",")):
        return f'"{cleaned}"'
    return cleaned


def _dtstamp(now: datetime | None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    elif not isinstance(now, datetime):
        raise ValueError("The DTSTAMP value must be a datetime.")
    elif not _is_aware(now):
        now = now.replace(tzinfo=timezone.utc)
    return _as_utc(now).strftime(_UTC_FORMAT)


def _vevent_lines(event: CalendarEvent, stamp: str) -> list[str]:
    tzid = _param_value(event.timezone)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{escape_text(event.uid)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={tzid}:{event.local_start.strftime(_LOCAL_FORMAT)}",
        f"DTEND;TZID={tzid}:{event.local_end.strftime(_LOCAL_FORMAT)}",
        f"SEQUENCE:{event.sequence}",
        f"STATUS:{_STATUS_TO_ICS[event.status]}",
        f"SUMMARY:{escape_text(event.title)}",
    ]
    if event.description:
        lines.append(f"DESCRIPTION:{escape_text(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{escape_text(event.location)}")
    if event.url:
        lines.append(f"URL:{event.url}")
    if event.organizer_email:
        organizer = event.organizer_email
        lines.append(f"ORGANIZER;CN={_param_value(organizer)}:mailto:{organizer}")
    for attendee in event.attendees:
        lines.append(
            f"ATTENDEE;CN={_param_value(attendee)};ROLE=REQ-PARTICIPANT;"
            f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee}"
        )
    lines.append("TRANSP:OPAQUE")
    if event.status != "cancelled":
        for minutes in event.reminders_minutes:
            trigger = f"-PT{minutes}M" if minutes > 0 else "PT0M"
            lines.extend(
                [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{escape_text(event.title)}",
                    f"TRIGGER:{trigger}",
                    "END:VALARM",
                ]
            )
    lines.append("END:VEVENT")
    return lines


def _effective_method(
    events: Sequence[CalendarEvent], method: str | None
) -> str | None:
    if method is not None:
        if not isinstance(method, str) or not method.strip():
            raise ValueError("The iCalendar METHOD must be a non-empty string.")
        return method.strip().upper()
    if events and all(event.status == "cancelled" for event in events):
        return "CANCEL"
    return None


def _render_calendar(
    events: Sequence[CalendarEvent],
    *,
    prodid: str,
    method: str | None,
    now: datetime | None,
) -> str:
    if not events:
        raise ValueError("At least one event is required to build a calendar.")
    for event in events:
        if not isinstance(event, CalendarEvent):
            raise ValueError("Every item must be a CalendarEvent.")
    if not isinstance(prodid, str) or not prodid.strip():
        raise ValueError("The PRODID must be a non-empty string.")
    stamp = _dtstamp(now)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}", "CALSCALE:GREGORIAN"]
    effective = _effective_method(events, method)
    if effective:
        lines.append(f"METHOD:{effective}")
    for event in events:
        lines.extend(_vevent_lines(event, stamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def build_ics(
    event: CalendarEvent,
    *,
    prodid: str = "-//Aira//Career Copilot//EN",
    method: str | None = None,
    now: datetime | None = None,
) -> str:
    """Render one event as an RFC 5545 iCalendar document.

    Lines use CRLF endings and are folded at 75 octets. DTSTART/DTEND are
    written in the event's time zone with a ``TZID`` parameter; DTSTAMP is
    UTC. When ``method`` is omitted and the event is cancelled, ``METHOD:CANCEL``
    is emitted automatically.
    """
    if not isinstance(event, CalendarEvent):
        raise ValueError("A CalendarEvent is required.")
    return _render_calendar([event], prodid=prodid, method=method, now=now)


def build_calendar(
    events: Sequence[CalendarEvent],
    *,
    prodid: str = "-//Aira//Career Copilot//EN",
    method: str | None = None,
    now: datetime | None = None,
) -> str:
    """Render many events into one VCALENDAR document."""
    return _render_calendar(list(events), prodid=prodid, method=method, now=now)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_ics_datetime(value: str, tzid: str | None = None) -> datetime:
    """Parse an iCalendar DATE or DATE-TIME value into an aware datetime.

    Supports ``20260303T140000Z`` (UTC), ``20260303T140000`` with a ``tzid``
    (local time in that zone) and ``20260303`` (midnight). Local values with no
    ``tzid`` are treated as UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A date or date-time value is required.")
    text = value.strip()
    zone = _zone(tzid) if tzid else timezone.utc

    if _DATE_RE.match(text):
        try:
            day = date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
        except ValueError as exc:
            raise ValueError(f"Invalid iCalendar date: {text!r}.") from exc
        return datetime(day.year, day.month, day.day, tzinfo=zone)

    match = _DATETIME_RE.match(text)
    if not match:
        raise ValueError(
            f"Invalid iCalendar date-time: {text!r}. Expected YYYYMMDD, "
            "YYYYMMDDTHHMMSS or YYYYMMDDTHHMMSSZ."
        )
    try:
        naive = datetime.strptime(text[:15], _LOCAL_FORMAT)
    except ValueError as exc:
        raise ValueError(f"Invalid iCalendar date-time: {text!r}.") from exc
    if match.group(1) == "Z":
        return naive.replace(tzinfo=timezone.utc)
    return naive.replace(tzinfo=zone)


# --------------------------------------------------------------------------- #
# Provider deep links
# --------------------------------------------------------------------------- #


def _query(params: list[tuple[str, str]]) -> str:
    return urlencode(params, quote_via=quote, safe="/")


def google_calendar_link(event: CalendarEvent) -> str:
    """Build a Google Calendar "add event" link (times are sent in UTC)."""
    if not isinstance(event, CalendarEvent):
        raise ValueError("A CalendarEvent is required.")
    dates = (
        f"{_as_utc(event.start).strftime(_UTC_FORMAT)}/"
        f"{_as_utc(event.end).strftime(_UTC_FORMAT)}"
    )
    params = [("action", "TEMPLATE"), ("text", event.title), ("dates", dates)]
    if event.description:
        params.append(("details", event.description))
    if event.location:
        params.append(("location", event.location))
    params.append(("ctz", event.timezone))
    if event.attendees:
        params.append(("add", ",".join(event.attendees)))
    return "https://calendar.google.com/calendar/render?" + _query(params)


def _outlook_link(base: str, event: CalendarEvent) -> str:
    if not isinstance(event, CalendarEvent):
        raise ValueError("A CalendarEvent is required.")
    params = [
        ("rru", "addevent"),
        ("subject", event.title),
        ("startdt", event.local_start.isoformat()),
        ("enddt", event.local_end.isoformat()),
    ]
    if event.description:
        params.append(("body", event.description))
    if event.location:
        params.append(("location", event.location))
    if event.attendees:
        params.append(("to", ",".join(event.attendees)))
    return base + "?" + _query(params)


def outlook_calendar_link(event: CalendarEvent) -> str:
    """Build an Outlook.com "add event" link (local times with UTC offsets)."""
    return _outlook_link("https://outlook.live.com/calendar/0/action/compose", event)


def office365_calendar_link(event: CalendarEvent) -> str:
    """Build an Office 365 / Outlook on the web "add event" link."""
    return _outlook_link("https://outlook.office.com/calendar/0/action/compose", event)
