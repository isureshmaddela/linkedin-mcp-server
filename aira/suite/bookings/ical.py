"""iCalendar (RFC 5545) for Bookings: the invite a customer's calendar app adds, and the busy times in a staff
member's own calendar feed.

Writing: :func:`booking_invite` is one VEVENT with a stable UID (the booking's id), a SEQUENCE that goes up with
every change, ``METHOD:REQUEST`` for a confirmation or a move and ``METHOD:CANCEL`` for a cancellation, so the
calendar app updates or removes the same event instead of adding another one.

Reading: :func:`busy_times` turns a feed (Google Calendar's or Outlook's private address in iCal format) into busy
``(start, end)`` UTC ranges inside a window. It understands what those feeds use: times in UTC, with a TZID (an
IANA name or a common Windows one) or floating; all-day events (the whole day is busy); ``TRANSP:TRANSPARENT``
(shown as free, so skipped) and cancelled events; ``RRULE`` with ``FREQ`` DAILY, WEEKLY, MONTHLY or YEARLY,
``INTERVAL``, ``COUNT``, ``UNTIL``, ``BYDAY`` (with an ordinal for monthly rules, like ``1MO`` or ``-1FR``) and
``BYMONTHDAY``; ``RDATE``; ``EXDATE``; and a moved or cancelled occurrence (``RECURRENCE-ID``).
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
PRODID = "-//Monosage//Bookings//EN"
MAX_EVENTS = 20000  # a feed with more VEVENTs than this is read up to here
MAX_PERIODS = 20000  # how far one recurring event is expanded
WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

# Outlook names zones the Windows way. The common ones, as IANA names (others fall back to the business's zone).
WINDOWS_ZONES = {
    "utc": "UTC",
    "coordinated universal time": "UTC",
    "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik",
    "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Budapest",
    "central european standard time": "Europe/Warsaw",
    "romance standard time": "Europe/Paris",
    "e. europe standard time": "Europe/Bucharest",
    "fle standard time": "Europe/Kiev",
    "gtb standard time": "Europe/Athens",
    "russian standard time": "Europe/Moscow",
    "turkey standard time": "Europe/Istanbul",
    "israel standard time": "Asia/Jerusalem",
    "south africa standard time": "Africa/Johannesburg",
    "egypt standard time": "Africa/Cairo",
    "arabian standard time": "Asia/Dubai",
    "arab standard time": "Asia/Riyadh",
    "pakistan standard time": "Asia/Karachi",
    "india standard time": "Asia/Kolkata",
    "sri lanka standard time": "Asia/Colombo",
    "nepal standard time": "Asia/Kathmandu",
    "bangladesh standard time": "Asia/Dhaka",
    "se asia standard time": "Asia/Bangkok",
    "singapore standard time": "Asia/Singapore",
    "china standard time": "Asia/Shanghai",
    "taipei standard time": "Asia/Taipei",
    "tokyo standard time": "Asia/Tokyo",
    "korea standard time": "Asia/Seoul",
    "aus eastern standard time": "Australia/Sydney",
    "e. australia standard time": "Australia/Brisbane",
    "cen. australia standard time": "Australia/Adelaide",
    "w. australia standard time": "Australia/Perth",
    "new zealand standard time": "Pacific/Auckland",
    "hawaiian standard time": "Pacific/Honolulu",
    "alaskan standard time": "America/Anchorage",
    "pacific standard time": "America/Los_Angeles",
    "us mountain standard time": "America/Phoenix",
    "mountain standard time": "America/Denver",
    "central standard time": "America/Chicago",
    "eastern standard time": "America/New_York",
    "atlantic standard time": "America/Halifax",
    "canada central standard time": "America/Regina",
    "sa pacific standard time": "America/Bogota",
    "e. south america standard time": "America/Sao_Paulo",
    "argentina standard time": "America/Argentina/Buenos_Aires",
    "mexico standard time": "America/Mexico_City",
    "central standard time (mexico)": "America/Mexico_City",
}


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def escape(value: Any) -> str:
    """TEXT as RFC 5545 writes it: backslash, semicolon, comma and line breaks escaped."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _param(value: Any) -> str:
    """A parameter value (CN=...): quoted, with the characters a quoted value cannot hold left out."""
    text = re.sub(r'[\x00-\x1f"]', "", str(value or "")).strip()
    return f'"{text}"'


def fold(line: str) -> str:
    """One content line folded at 75 octets (a continuation starts with a space), never inside a character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces: list[str] = []
    current = b""
    limit = 75
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > limit:
            pieces.append(current.decode("utf-8"))
            current = b""
            limit = 74  # the leading space takes one octet
        current += encoded
    pieces.append(current.decode("utf-8"))
    return "\r\n ".join(pieces)


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def booking_invite(
    booking: Mapping[str, Any],
    *,
    method: str = "REQUEST",
    business: Mapping[str, Any] | None = None,
    organizer_email: str = "",
    manage_url: str = "",
    sequence: int = 0,
    uid_domain: str = "bookings.monosage",
    now: datetime | None = None,
) -> bytes:
    """The ``.ics`` for one booking: ``REQUEST`` (booked or moved, sent by email), ``CANCEL`` (cancelled) or
    ``PUBLISH`` (the file a customer downloads with "Add to calendar": no attendees, nothing to answer)."""
    wanted = str(method).upper()
    publish = wanted == "PUBLISH"
    method = wanted if wanted in ("CANCEL", "PUBLISH") else "REQUEST"
    profile = dict(business or {})
    start = datetime.fromisoformat(str(booking["start_at"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(booking["end_at"]).replace("Z", "+00:00"))
    name = str(profile.get("name") or "")
    summary = f"{booking['service_name']}" + (f" at {name}" if name else "")
    details = [
        f"{booking['service_name']} with {booking['staff_name']}.",
        f"Reference: {booking['reference']}",
    ]
    if manage_url and method != "CANCEL":
        details.append(f"To cancel or change it: {manage_url}")
    status = "CANCELLED" if method == "CANCEL" else "CONFIRMED"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{booking['id']}@{uid_domain}",
        f"SEQUENCE:{max(0, int(sequence))}",
        f"DTSTAMP:{stamp(now or datetime.now(UTC))}",
        f"DTSTART:{stamp(start)}",
        f"DTEND:{stamp(end)}",
        f"SUMMARY:{escape(summary)}",
        f"DESCRIPTION:{escape(chr(10).join(details))}",
        f"STATUS:{status}",
        "TRANSP:OPAQUE",
    ]
    if profile.get("address"):
        lines.append(f"LOCATION:{escape(profile['address'])}")
    if manage_url and method != "CANCEL":
        lines.append(f"URL:{manage_url}")
    if organizer_email:
        lines.append(
            f"ORGANIZER;CN={_param(name or organizer_email)}:mailto:{organizer_email}"
        )
    if booking.get("customer_email") and not publish:
        lines.append(
            f"ATTENDEE;CN={_param(booking.get('customer_name'))};ROLE=REQ-PARTICIPANT;"
            f"PARTSTAT={'DECLINED' if method == 'CANCEL' else 'ACCEPTED'};RSVP=FALSE:mailto:{booking['customer_email']}"
        )
    if method != "CANCEL" and start > (now or datetime.now(UTC)) + timedelta(hours=2):
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{escape(summary)}",
            "TRIGGER:-PT1H",
            "END:VALARM",
        ]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8")


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


@dataclass
class Prop:
    name: str
    params: dict[str, str]
    value: str


@dataclass
class Event:
    uid: str = ""
    props: dict[str, list[Prop]] = field(default_factory=dict)

    def first(self, name: str) -> Prop | None:
        found = self.props.get(name)
        return found[0] if found else None

    def all(self, name: str) -> list[Prop]:
        return self.props.get(name, [])


def unfold(text: str) -> list[str]:
    """The content lines of a calendar: line breaks normalised, folded lines joined."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def parse_line(line: str) -> Prop | None:
    """``NAME;PARAM=VALUE;...:value`` (a colon inside a quoted parameter does not end the name)."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            head, value = line[:index], line[index + 1 :]
            break
    else:
        return None
    parts: list[str] = []
    current = ""
    quoted = False
    for char in head:
        if char == '"':
            quoted = not quoted
            continue
        if char == ";" and not quoted:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _eq, val = part.partition("=")
        params[key.strip().upper()] = val.strip()
    return Prop(parts[0].strip().upper(), params, value)


def events(text: str) -> list[Event]:
    """The VEVENTs of a calendar (their alarms and other parts left out)."""
    found: list[Event] = []
    current: Event | None = None
    depth = 0  # components nested inside the event (VALARM)
    for line in unfold(text):
        prop = parse_line(line)
        if prop is None:
            continue
        if prop.name == "BEGIN":
            kind = prop.value.strip().upper()
            if current is not None:
                depth += 1
            elif kind == "VEVENT":
                current = Event()
                depth = 0
            continue
        if prop.name == "END":
            kind = prop.value.strip().upper()
            if current is not None and depth:
                depth -= 1
            elif current is not None and kind == "VEVENT":
                found.append(current)
                current = None
                if len(found) >= MAX_EVENTS:
                    break
            continue
        if current is not None and not depth:
            current.props.setdefault(prop.name, []).append(prop)
            if prop.name == "UID":
                current.uid = prop.value.strip()
    return found


def zone_for(tzid: str, fallback: ZoneInfo) -> ZoneInfo:
    """A TZID as a zone: an IANA name, a common Windows name, a ``/vendor/.../Area/City`` path, else ``fallback``."""
    name = tzid.strip().strip('"')
    candidates = [name]
    if "/" in name:
        parts = [p for p in name.split("/") if p]
        candidates += ["/".join(parts[-2:]), "/".join(parts[-3:])]
    mapped = WINDOWS_ZONES.get(name.lower())
    if mapped:
        candidates.insert(0, mapped)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return fallback


_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$")


@dataclass
class When:
    """A DTSTART-like value: a date (all day) or a wall-clock time in a zone (``utc`` when it ended in Z)."""

    wall: datetime  # naive: the local date and time
    zone: ZoneInfo | timezone
    all_day: bool

    def instant(self) -> datetime:
        return _localise(self.wall, self.zone)


def _localise(wall: datetime, zone: ZoneInfo | timezone) -> datetime:
    """A wall-clock time in ``zone`` as a UTC instant (a time skipped by a clock change moves forward)."""
    aware = wall.replace(tzinfo=zone)
    if isinstance(zone, ZoneInfo):
        # a time that does not exist (a spring-forward gap) round-trips to another wall time: move it on an hour
        back = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if back != wall:
            aware = (wall + timedelta(hours=1)).replace(tzinfo=zone)
    return aware.astimezone(UTC)


def parse_when(
    value: str, params: Mapping[str, str], fallback: ZoneInfo
) -> When | None:
    match = _STAMP.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, utc = match.groups()
    try:
        if hour is None or params.get("VALUE", "").upper() == "DATE":
            return When(datetime(int(year), int(month), int(day)), fallback, True)
        wall = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0)
        )
    except ValueError:
        return None
    if utc:
        return When(wall, UTC, False)
    if params.get("TZID"):
        return When(wall, zone_for(params["TZID"], fallback), False)
    return When(wall, fallback, False)  # floating: the business's own time


def parse_duration(value: str) -> timedelta | None:
    match = re.match(
        r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$",
        value.strip().upper(),
    )
    if not match or value.strip().upper() in ("P", "PT"):
        return None
    sign, weeks, days, hours, minutes, seconds = match.groups()
    span = timedelta(
        weeks=int(weeks or 0),
        days=int(days or 0),
        hours=int(hours or 0),
        minutes=int(minutes or 0),
        seconds=int(seconds or 0),
    )
    return -span if sign == "-" else span


def parse_rule(value: str) -> dict[str, str]:
    rule: dict[str, str] = {}
    for part in value.split(";"):
        key, _eq, val = part.partition("=")
        if key.strip():
            rule[key.strip().upper()] = val.strip().upper()
    return rule


_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")


def _bydays(rule: Mapping[str, str]) -> list[tuple[int, int]]:
    """``BYDAY`` as ``[(ordinal or 0, weekday 0-6)]``."""
    out = []
    for item in rule.get("BYDAY", "").split(","):
        match = _BYDAY.match(item.strip())
        if match:
            out.append((int(match.group(1) or 0), WEEKDAY_CODES.index(match.group(2))))
    return out


def _month_days(year: int, month: int, rule: Mapping[str, str], base_day: int) -> list[int]:
    """The days of one month a MONTHLY (or YEARLY) rule picks, in order."""
    last = calendar.monthrange(year, month)[1]
    picked: set[int] = set()
    if rule.get("BYMONTHDAY"):
        for item in rule["BYMONTHDAY"].split(","):
            try:
                number = int(item)
            except ValueError:
                continue
            day = number if number > 0 else last + 1 + number
            if 1 <= day <= last:
                picked.add(day)
    elif rule.get("BYDAY"):
        for ordinal, weekday in _bydays(rule):
            days = [d for d in range(1, last + 1) if date(year, month, d).weekday() == weekday]
            if ordinal == 0:
                picked.update(days)
            elif 0 < ordinal <= len(days):
                picked.add(days[ordinal - 1])
            elif 0 < -ordinal <= len(days):
                picked.add(days[ordinal])
    elif base_day <= last:
        picked.add(base_day)  # a month without that day (the 31st) is skipped
    return sorted(picked)


def _add_months(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def expand(
    start: When,
    rule: Mapping[str, str],
    *,
    window_end: datetime,
    window_start: datetime,
) -> Iterator[datetime]:
    """The wall-clock starts of a recurring event (the first included), in order, until the rule ends or the
    window does. COUNT counts from the first occurrence, so the expansion starts there when it is set."""
    freq = rule.get("FREQ", "")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        yield start.wall
        return
    try:
        interval = max(1, int(rule.get("INTERVAL") or 1))
    except ValueError:
        interval = 1
    count = None
    if rule.get("COUNT"):
        try:
            count = max(0, int(rule["COUNT"]))
        except ValueError:
            count = None
    until_wall: datetime | None = None
    until_instant: datetime | None = None
    if rule.get("UNTIL"):
        local = start.zone if isinstance(start.zone, ZoneInfo) else ZoneInfo("UTC")
        until = parse_when(rule["UNTIL"], {}, local)
        if until is not None:
            if until.all_day:
                until_wall = until.wall.replace(hour=23, minute=59, second=59)
            elif until.zone is UTC and not start.all_day:
                until_instant = until.wall.replace(tzinfo=UTC)
            else:
                until_wall = until.wall
    base = start.wall
    clock = base.time()
    weekdays = sorted({weekday for _ordinal, weekday in _bydays(rule)}) or [base.weekday()]
    seen = 0
    # Without COUNT the periods before the window cannot matter: start a little before it.
    first_period = 0
    if count is None:
        lead = (window_start.replace(tzinfo=None) - base).days - 31
        if lead > 0:
            if freq == "DAILY":
                first_period = lead // interval
            elif freq == "WEEKLY":
                first_period = lead // (7 * interval)
            elif freq == "MONTHLY":
                first_period = (lead // 31) // interval
            else:
                first_period = (lead // 366) // interval
    end_wall = window_end.astimezone(UTC).replace(tzinfo=None) + timedelta(days=2)
    wkst = WEEKDAY_CODES.index(rule["WKST"]) if rule.get("WKST") in WEEKDAY_CODES else 0
    for period in range(first_period, first_period + MAX_PERIODS):
        if freq == "DAILY":
            day = base.date() + timedelta(days=period * interval)
            candidates = [day] if not rule.get("BYDAY") or day.weekday() in weekdays else []
        elif freq == "WEEKLY":
            week_start = base.date() - timedelta(days=(base.weekday() - wkst) % 7)
            week_start += timedelta(weeks=period * interval)
            candidates = sorted(
                week_start + timedelta(days=(weekday - wkst) % 7) for weekday in weekdays
            )
        elif freq == "MONTHLY":
            year, month = _add_months(base.year, base.month, period * interval)
            candidates = [date(year, month, d) for d in _month_days(year, month, rule, base.day)]
        else:
            year = base.year + period * interval
            month_rule = rule.get("BYMONTH", "")
            months = [int(m) for m in month_rule.split(",") if m.isdigit() and 1 <= int(m) <= 12] or [base.month]
            candidates = []
            for month in months:
                if rule.get("BYDAY") or rule.get("BYMONTHDAY"):
                    candidates += [date(year, month, d) for d in _month_days(year, month, rule, base.day)]
                elif base.day <= calendar.monthrange(year, month)[1]:
                    candidates.append(date(year, month, base.day))
            candidates.sort()
        if candidates and datetime.combine(candidates[0], clock) > end_wall:
            return
        for day in candidates:
            moment = datetime.combine(day, clock)
            if moment < base:
                continue
            if until_wall is not None and moment > until_wall:
                return
            if until_instant is not None and _localise(moment, start.zone) > until_instant:
                return
            if moment > end_wall:
                return
            seen += 1
            if count is not None and seen > count:
                return
            yield moment


def _value(event: Event, name: str) -> str:
    prop = event.first(name)
    return prop.value.strip().upper() if prop is not None else ""


def _exclusions(event: Event, fallback: ZoneInfo) -> tuple[set[datetime], set[date]]:
    instants: set[datetime] = set()
    days: set[date] = set()
    for prop in event.all("EXDATE"):
        for value in prop.value.split(","):
            when = parse_when(value, prop.params, fallback)
            if when is None:
                continue
            if when.all_day:
                days.add(when.wall.date())
            else:
                instants.add(when.instant())
    return instants, days


def _busy_of(
    event: Event,
    fallback: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
    moved: Mapping[str, set[datetime]],
    moved_days: Mapping[str, set[date]],
) -> Iterator[tuple[datetime, datetime]]:
    if _value(event, "STATUS") == "CANCELLED" or _value(event, "TRANSP") == "TRANSPARENT":
        return
    start_prop = event.first("DTSTART")
    if start_prop is None:
        return
    start = parse_when(start_prop.value, start_prop.params, fallback)
    if start is None:
        return
    end_prop = event.first("DTEND")
    end = parse_when(end_prop.value, end_prop.params, fallback) if end_prop else None
    duration_prop = event.first("DURATION")
    if start.all_day:
        days = 1
        if end is not None and end.all_day:
            days = max(1, (end.wall.date() - start.wall.date()).days)
        elif duration_prop is not None:
            span = parse_duration(duration_prop.value)
            days = max(1, span.days) if span else 1
        length: timedelta = timedelta(days=days)
    elif end is not None:
        length = end.instant() - start.instant()
    elif duration_prop is not None:
        length = parse_duration(duration_prop.value) or timedelta(0)
    else:
        length = timedelta(0)
    if length <= timedelta(0):
        return  # a moment, not a span of time
    excluded, excluded_days = _exclusions(event, fallback)
    override = event.first("RECURRENCE-ID") is not None
    if not override:  # the occurrences moved elsewhere (or cancelled) by their own VEVENT
        excluded |= moved.get(event.uid, set())
        excluded_days |= moved_days.get(event.uid, set())
    rule_prop = event.first("RRULE")
    walls: list[tuple[datetime, ZoneInfo | timezone]] = []
    if rule_prop is not None and not override:
        walls = [
            (wall, start.zone)
            for wall in expand(
                start,
                parse_rule(rule_prop.value),
                window_end=window_end,
                window_start=window_start,
            )
        ]
    else:
        walls = [(start.wall, start.zone)]
    for prop in event.all("RDATE"):
        if prop.params.get("VALUE", "").upper() == "PERIOD":
            continue
        for value in prop.value.split(","):
            extra = parse_when(value, prop.params, fallback)
            if extra is not None:
                walls.append((extra.wall, extra.zone))
    for wall, zone in walls:
        if start.all_day:
            if wall.date() in excluded_days:
                continue
            begin = _localise(datetime.combine(wall.date(), time(0, 0)), fallback)
            finish = _localise(
                datetime.combine(wall.date() + length, time(0, 0)), fallback
            )
        else:
            begin = _localise(wall, zone)
            if begin in excluded or wall.date() in excluded_days:
                continue
            finish = begin + length
        if begin < window_end and finish > window_start:
            yield begin, finish


def busy_times(
    text: str,
    *,
    window_start: datetime,
    window_end: datetime,
    fallback: ZoneInfo,
    limit: int = 20000,
) -> list[tuple[datetime, datetime]]:
    """The busy ranges (UTC, merged where they overlap) of a calendar feed inside the window."""
    found = events(text)
    if not found and "BEGIN:VCALENDAR" not in text.upper():
        raise ValueError("That address did not give a calendar (an .ics file).")
    # A moved or cancelled occurrence (RECURRENCE-ID) replaces that occurrence of its series.
    moved: dict[str, set[datetime]] = {}
    moved_days: dict[str, set[date]] = {}
    for event in found:
        rid = event.first("RECURRENCE-ID")
        if rid is None:
            continue
        when = parse_when(rid.value, rid.params, fallback)
        if when is None:
            continue
        if when.all_day:
            moved_days.setdefault(event.uid, set()).add(when.wall.date())
        else:
            moved.setdefault(event.uid, set()).add(when.instant())
    ranges: list[tuple[datetime, datetime]] = []
    for event in found:
        for busy in _busy_of(event, fallback, window_start, window_end, moved, moved_days):
            ranges.append(busy)
            if len(ranges) >= limit * 2:
                break
    ranges.sort()
    merged: list[tuple[datetime, datetime]] = []
    for begin, finish in ranges:
        if merged and begin <= merged[-1][1]:
            if finish > merged[-1][1]:
                merged[-1] = (merged[-1][0], finish)
        else:
            merged.append((begin, finish))
    return merged[:limit]
