"""Deterministic, explainable email intelligence for the Career Copilot.

This module classifies job-search emails into a small set of categories and
extracts only details that are explicitly present in the message (company,
role, recruiter contact, interview date/time, meeting link, deadlines,
application ids). Everything is rule based: weighted keyword/phrase signals,
regular expressions and a fixed time-zone table. There is no machine
learning and nothing here performs network access.

Golden rules enforced by this module:

* Never invent data. Every extracted field comes from a literal match.
* Email content is untrusted data. It is matched, never interpreted, so a
  body that says "ignore previous instructions" only contributes whatever
  literal keyword weight it happens to contain.
* Retention stays minimal. Callers persist only what is returned here plus a
  fingerprint from :func:`message_fingerprint`; attachment bytes are never
  surfaced by :func:`parse_eml`.
"""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import hashlib
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage, Message
from html.parser import HTMLParser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "CATEGORIES",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "EmailClassification",
    "ExtractedDetails",
    "ParsedEmail",
    "SuggestedAction",
    "classify_email",
    "html_to_text",
    "message_fingerprint",
    "parse_eml",
    "suggest_action",
]

CATEGORIES: tuple[str, ...] = (
    "interview_invitation",
    "assessment_request",
    "recruiter_message",
    "application_confirmation",
    "application_update",
    "rejection",
    "offer",
    "follow_up",
    "unrelated",
)
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
RULES_VERSION = "email-rules-v1"

MAX_TEXT_CHARS = 100_000
_FINGERPRINT_BODY_CHARS = 2000
_MAX_ROLE_CHARS = 120
_SUBJECT_WEIGHT = 1.5
_STRONG_SIGNAL = 2.0
_UNRELATED_CONFIDENCE = 0.6

INTERVIEW_TYPES: tuple[str, ...] = (
    "recruiter_screen",
    "technical_interview",
    "manager_interview",
    "panel_interview",
    "final_interview",
)
APPLICATION_STAGES: tuple[str, ...] = (
    "applied",
    "assessment",
    "recruiter_screen",
    "technical_interview",
    "manager_interview",
    "panel_interview",
    "final_interview",
    "offer",
    "rejected",
)
_SENSITIVE_CATEGORIES = frozenset({"offer", "rejection"})


# --------------------------------------------------------------------------- #
# Public data types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExtractedDetails:
    """Details explicitly present in a message. Every field is None when absent."""

    company: str | None = None
    role: str | None = None
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    contact_phone: str | None = None
    date: str | None = None
    time: str | None = None
    timezone: str | None = None
    start_at: str | None = None
    duration_minutes: int | None = None
    meeting_link: str | None = None
    meeting_platform: str | None = None
    assessment_deadline: str | None = None
    application_id: str | None = None
    interview_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EmailClassification:
    """Result of :func:`classify_email`."""

    category: str
    confidence: float
    signals: tuple[str, ...]
    details: ExtractedDetails
    version: str = RULES_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "signals": list(self.signals),
            "details": self.details.to_dict(),
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """Safe, text-only view of an RFC 822 message produced by :func:`parse_eml`."""

    subject: str
    sender_name: str
    sender_email: str
    recipients: tuple[str, ...]
    received_at: str | None
    text: str
    attachments: tuple[dict[str, object], ...]
    message_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "sender_name": self.sender_name,
            "sender_email": self.sender_email,
            "recipients": list(self.recipients),
            "received_at": self.received_at,
            "text": self.text,
            "attachments": [dict(item) for item in self.attachments],
            "message_id": self.message_id,
        }


@dataclass(frozen=True, slots=True)
class SuggestedAction:
    """Action proposed to the user from a classification; never auto-applied."""

    kind: str
    stage: str | None
    requires_confirmation: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Lookup tables
# --------------------------------------------------------------------------- #

# Abbreviation -> IANA zone. Ambiguous abbreviations resolve to the mapping the
# product specification fixes (IST -> India, CST -> US Central, ...).
TIMEZONE_ABBREVIATIONS: dict[str, str] = {
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "PT": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "MT": "America/Denver",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "CT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "ET": "America/New_York",
    "GMT": "UTC",
    "UTC": "UTC",
    "Z": "UTC",
    "BST": "Europe/London",
    "CET": "Europe/Berlin",
    "CEST": "Europe/Berlin",
    "IST": "Asia/Kolkata",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    "AKST": "America/Anchorage",
    "AKDT": "America/Anchorage",
    "HST": "Pacific/Honolulu",
    "JST": "Asia/Tokyo",
    "KST": "Asia/Seoul",
    "SGT": "Asia/Singapore",
    "HKT": "Asia/Hong_Kong",
    "NZST": "Pacific/Auckland",
    "NZDT": "Pacific/Auckland",
    "SAST": "Africa/Johannesburg",
    "EET": "Europe/Athens",
    "EEST": "Europe/Athens",
    "WET": "Europe/Lisbon",
    "WEST": "Europe/Lisbon",
    "MSK": "Europe/Moscow",
    "AWST": "Australia/Perth",
    "ACST": "Australia/Adelaide",
    "ACDT": "Australia/Adelaide",
    "GST": "Asia/Dubai",
    "PKT": "Asia/Karachi",
}

# Full zone names that may appear anywhere in the message (case-insensitive).
_TIMEZONE_NAMES: tuple[tuple[str, str], ...] = (
    (r"\bcentral european(?: summer)? time\b", "Europe/Berlin"),
    (r"\bpacific(?: (?:standard|daylight))? time\b", "America/Los_Angeles"),
    (r"\bmountain(?: (?:standard|daylight))? time\b", "America/Denver"),
    (r"\bcentral(?: (?:standard|daylight))? time\b", "America/Chicago"),
    (r"\beastern(?: (?:standard|daylight))? time\b", "America/New_York"),
    (r"\bindia(?:n)? standard time\b", "Asia/Kolkata"),
    (r"\bbritish summer time\b", "Europe/London"),
    (r"\bgreenwich mean time\b", "UTC"),
    (r"\bcoordinated universal time\b", "UTC"),
    (r"\baustralian eastern(?: (?:standard|daylight))? time\b", "Australia/Sydney"),
    (r"\bjapan standard time\b", "Asia/Tokyo"),
)

# Upper-case tokens that look like zone abbreviations but never are.
_NOT_TIMEZONE_TOKENS = frozenset(
    {
        "AM",
        "PM",
        "ON",
        "AT",
        "TO",
        "ID",
        "HR",
        "US",
        "UK",
        "EU",
        "OR",
        "IN",
        "AND",
        "THE",
        "FOR",
        "VIA",
        "ZOOM",
        "TEAMS",
        "MEET",
        "RSVP",
        "PDF",
        "EOD",
        "COB",
        "ASAP",
        "FYI",
        "CEO",
        "CTO",
        "VP",
        "QA",
        "IT",
        "OK",
    }
)

# Email domains that identify a mailbox provider or an ATS, never the employer.
GENERIC_SENDER_DOMAINS = frozenset(
    {
        "gmail",
        "googlemail",
        "outlook",
        "hotmail",
        "live",
        "msn",
        "yahoo",
        "ymail",
        "icloud",
        "me",
        "mac",
        "aol",
        "proton",
        "protonmail",
        "pm",
        "gmx",
        "mail",
        "email",
        "zoho",
        "fastmail",
        "hey",
        "yandex",
        "qq",
        "greenhouse",
        "greenhouse-mail",
        "lever",
        "hire",
        "ashby",
        "ashbyhq",
        "smartrecruiters",
        "workday",
        "myworkday",
        "myworkdayjobs",
        "linkedin",
        "icims",
        "jobvite",
        "taleo",
        "successfactors",
        "bamboohr",
        "breezy",
        "workable",
        "recruitee",
        "indeed",
        "glassdoor",
        "ziprecruiter",
        "hirevue",
        "hackerrank",
        "codility",
        "codesignal",
        "calendly",
        "goodtime",
        "ultipro",
        "ukg",
        "paylocity",
        "paycom",
        "adp",
        "jazzhr",
        "applytojob",
        "rippling",
        "gem",
        "dover",
        "wellfound",
        "otta",
        "hired",
        "teamtailor",
        "personio",
        "pinpointhq",
        "avature",
        "brassring",
        "kenexa",
        "phenom",
        "eightfold",
        "beamery",
        "recruiterbox",
        "sendgrid",
        "mailgun",
        "amazonses",
        "mandrillapp",
        "sparkpostmail",
        "notifications",
    }
)
_MAILBOX_PROVIDERS = frozenset(
    {
        "gmail",
        "googlemail",
        "outlook",
        "hotmail",
        "live",
        "msn",
        "yahoo",
        "ymail",
        "icloud",
        "me",
        "mac",
        "aol",
        "proton",
        "protonmail",
        "pm",
        "gmx",
        "mail",
        "email",
        "zoho",
        "fastmail",
        "hey",
        "yandex",
        "qq",
    }
)
# ATS / notification domains: a human recruiter never writes from these.
_ATS_DOMAINS = GENERIC_SENDER_DOMAINS - _MAILBOX_PROVIDERS
_NOREPLY_LOCAL_PARTS = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|noreply[\w.-]*|notifications?|"
    r"notify|mailer(?:-daemon)?|alerts?|system|automated|auto-?reply|bounce|"
    r"messages?|updates?)(?:[+.-][\w.+-]*)?$",
    re.IGNORECASE,
)
_PUBLIC_SUFFIX_LABELS = frozenset({"co", "com", "org", "net", "ac", "gov", "edu"})


# --------------------------------------------------------------------------- #
# Classification signals
# --------------------------------------------------------------------------- #

# (regex, weight, human label). Weights >= _STRONG_SIGNAL count as "strong".
_Signal = tuple[str, float, str]
_SIGNAL_RULES: dict[str, tuple[_Signal, ...]] = {
    "interview_invitation": (
        (r"\binterview(?:s|ing|ed)?\b", 2.0, "interview"),
        (
            r"\binvit(?:e|ation) (?:you )?to (?:an? )?(?:interview|chat|call)\b",
            2.5,
            "invitation to interview",
        ),
        (
            r"\bschedule (?:a|an|the|your|some) (?:call|conversation|chat|time|interview|meeting)\b",
            2.0,
            "schedule a call",
        ),
        (r"\bphone screen\b", 2.5, "phone screen"),
        (r"\btechnical screen\b", 2.5, "technical screen"),
        (r"\bon-?site\b", 1.5, "onsite"),
        (r"\bmeet with\b", 1.0, "meet with"),
        (r"\bavailability\b", 1.0, "availability"),
        (r"\bcalendar invit(?:e|ation)\b", 1.5, "calendar invite"),
        (r"\bbook (?:a|some) time\b", 1.0, "book a time"),
        (r"\bnext round\b", 1.0, "next round"),
        (
            r"\bconfirm(?:ed|ing)? (?:your|the) (?:interview|call|time slot)\b",
            2.0,
            "confirm the interview",
        ),
    ),
    "assessment_request": (
        (r"\bassessments?\b", 2.5, "assessment"),
        (r"\bcoding (?:challenge|test|exercise)\b", 3.0, "coding challenge"),
        (r"\btake-? ?home\b", 3.0, "take-home"),
        (r"\bhackerrank\b", 3.0, "HackerRank"),
        (r"\bcodility\b", 3.0, "Codility"),
        (r"\bcodesignal\b", 3.0, "CodeSignal"),
        (r"\bonline (?:test|exam)\b", 2.5, "online test"),
        (r"\btechnical (?:exercise|test)\b", 2.5, "technical exercise"),
        (
            r"\bcomplete (?:it |this |the \w+ )?(?:by|within|before)\b",
            1.0,
            "complete by",
        ),
        (r"\bdeadline\b", 1.0, "deadline"),
    ),
    "application_confirmation": (
        (r"\bthanks? (?:you )?for applying\b", 3.0, "thank you for applying"),
        (
            r"\b(?:we|we've|we have|i have|i've) received your application\b",
            3.0,
            "we received your application",
        ),
        (
            r"\bapplication (?:has been|was|is) (?:received|submitted)\b",
            3.0,
            "application has been received",
        ),
        (r"\bapplication submitted\b", 3.0, "application submitted"),
        (r"\bapplication (?:received|confirmation)\b", 2.5, "application received"),
        (r"\bsuccessfully (?:submitted|applied)\b", 2.0, "successfully submitted"),
        (
            r"\bthank you for your (?:interest|application)\b",
            1.5,
            "thank you for your interest",
        ),
    ),
    "application_update": (
        (r"\bupdate on your application\b", 1.5, "update on your application"),
        (r"\bstatus of your application\b", 1.5, "status of your application"),
        (r"\bapplication (?:status|update)\b", 1.5, "application status"),
        (r"\bunder review\b", 1.0, "under review"),
        (r"\bstill reviewing\b", 1.0, "still reviewing"),
        (r"\bnext steps?\b", 0.5, "next steps"),
    ),
    "rejection": (
        (
            r"\b(?:not|won'?t|unable to|will not|decided not to) (?:be )?mov(?:e|ing) forward\b",
            3.0,
            "will not be moving forward",
        ),
        (
            r"\b(?:decided|chosen|elected|opted) to (?:move|go|proceed) forward with (?:other|another)\b",
            3.0,
            "move forward with other candidates",
        ),
        (r"\bother candidates\b", 2.0, "other candidates"),
        (r"\bunfortunately\b", 2.0, "unfortunately"),
        (r"\bnot (?:been )?selected\b", 3.0, "not selected"),
        (r"\bposition has been filled\b", 3.0, "position has been filled"),
        (r"\bpursue other (?:candidates|applicants)\b", 3.0, "pursue other candidates"),
        (
            r"\bno longer (?:being )?(?:considered|under consideration)\b",
            3.0,
            "no longer considered",
        ),
        (r"\bregret to inform\b", 3.0, "regret to inform"),
        (r"\bnot (?:a|the right) (?:match|fit)\b", 2.0, "not the right fit"),
        (
            r"\bkeep your (?:resume|résumé|profile|information|details) on file\b",
            1.5,
            "keep your resume on file",
        ),
        (
            r"\bafter careful (?:consideration|review)\b",
            1.5,
            "after careful consideration",
        ),
        (
            r"\bwish you (?:the best|every success|all the best|luck)\b",
            1.0,
            "wish you the best",
        ),
    ),
    "offer": (
        (r"\bpleased to (?:offer|extend)\b", 3.0, "pleased to offer"),
        (r"\boffer letter\b", 3.0, "offer letter"),
        (
            r"\b(?:would like|'d like|want|excited|happy|thrilled|delighted) to (?:extend|make|offer)(?: you)? (?:an? |the )?(?:offer|position)\b",
            3.0,
            "we would like to extend an offer",
        ),
        (r"\bjob offer\b", 3.0, "job offer"),
        (r"\boffer of employment\b", 3.0, "offer of employment"),
        (r"\bformal offer\b", 3.0, "formal offer"),
        (r"\bcompensation package\b", 2.0, "compensation package"),
        (r"\bstart date\b", 1.5, "start date"),
        (r"\bbase salary\b", 1.5, "base salary"),
        (r"\bsign(?:ing|-on)? bonus\b", 1.5, "signing bonus"),
    ),
    "recruiter_message": (
        (
            r"\bi(?:'m| am) (?:a |the )?(?:technical |senior |lead |principal )?(?:recruiter|sourcer|talent (?:partner|acquisition))",
            3.0,
            "I am a recruiter",
        ),
        (r"\breaching out\b", 2.0, "reaching out"),
        (r"\byour profile\b", 2.0, "your profile"),
        (r"\bopen (?:role|position|req)s?\b", 2.0, "open role"),
        (r"\bare you open to\b", 2.5, "are you open to"),
        (r"\bwould you be (?:open|interested)\b", 2.0, "would you be open"),
        (r"\bcame across your\b", 2.0, "came across your"),
        (r"\bimpressed (?:by|with) your\b", 2.0, "impressed by your background"),
        (r"\bwe(?:'re| are) hiring\b", 2.0, "we are hiring"),
        (r"\bopportunit(?:y|ies)\b", 1.0, "opportunity"),
    ),
    "follow_up": (
        (r"\bfollowing up\b", 3.0, "following up"),
        (r"\bjust wanted to follow up\b", 3.0, "just wanted to follow up"),
        (r"\bcircling back\b", 3.0, "circling back"),
        (r"\bchecking in\b", 2.5, "checking in"),
        (r"\bany updates?\b", 2.5, "any update"),
        (r"\bfollow(?:-| )up\b", 2.0, "follow-up"),
        (r"\bbumping this\b", 2.0, "bumping this"),
        (r"\b(?:gentle|friendly|quick) reminder\b", 2.0, "gentle reminder"),
    ),
}
_COMPILED_SIGNALS: dict[str, tuple[tuple[re.Pattern[str], float, str], ...]] = {
    category: tuple(
        (re.compile(pattern, re.IGNORECASE), weight, label)
        for pattern, weight, label in rules
    )
    for category, rules in _SIGNAL_RULES.items()
}
# Tie-break order: the earlier category wins an exact score tie.
_PRECEDENCE: tuple[str, ...] = (
    "offer",
    "rejection",
    "interview_invitation",
    "assessment_request",
    "application_confirmation",
    "recruiter_message",
    "follow_up",
    "application_update",
)
# Specific outcomes that outrank the generic "application_update" category.
_OUTRANKS_UPDATE = ("rejection", "offer")

_INTERVIEW_TYPE_RULES: tuple[tuple[str, str], ...] = (
    (r"\bfinal(?:[- ]round| interview| stage| conversation)?\b", "final_interview"),
    (r"\bpanel\b|\binterview loop\b|\bloop\b", "panel_interview"),
    (
        r"\bhiring manager\b|\bmanager (?:interview|screen|call)\b|\bmeet (?:with )?(?:the|your|our) (?:hiring )?manager\b",
        "manager_interview",
    ),
    (
        r"\btechnical(?!\s+(?:recruit|sourc|talent))\b|\bcoding\b|\bsystem design\b|\bpair programming\b|\blive coding\b|\bwhiteboard\b",
        "technical_interview",
    ),
    (
        r"\brecruiter\b|\bphone screen\b|\bhr\b|\bintro(?:ductory)? call\b|\bscreening call\b|\binitial call\b|\btalent (?:acquisition|partner)\b|\bget to know you\b",
        "recruiter_screen",
    ),
)
_ONSITE_RE = re.compile(r"\bon-?site\b", re.IGNORECASE)
_MULTI_INTERVIEWER_RE = re.compile(
    r"\binterviewers\b|\bseveral\b|\bmultiple\b|\bteam members\b|\brounds\b|\bsessions\b|\bback-to-back\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# Extraction regexes
# --------------------------------------------------------------------------- #

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_MONTH_INDEX = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_WEEKDAY = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"
_MERIDIEM = r"(?:a\.?m\.?|p\.?m\.?)"
_DASH = r"(?:-|–|—|to|until|till|through)"

_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?(?!\d)"
)
_ISO_DATE_RE = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\dT:-])")
_MDY_DATE_RE = re.compile(
    rf"(?:\b{_WEEKDAY},?\s+)?\b({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(\d{{4}})\b)?",
    re.IGNORECASE,
)
_DMY_DATE_RE = re.compile(
    rf"(?:\b{_WEEKDAY},?\s+)?\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTHS})\b\.?(?:,?\s*(\d{{4}})\b)?",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
_TIME_RANGE_RE = re.compile(
    rf"(?<![\d:])(\d{{1,2}})(?::(\d{{2}}))?\s*({_MERIDIEM})?\s*{_DASH}\s*(\d{{1,2}})(?::(\d{{2}}))?\s*({_MERIDIEM})?(?![\d:])",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    rf"(?<![\d:])(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})?(?![\d:])|(?<![\d:])(\d{{1,2}})\s*({_MERIDIEM})(?![\d:])",
    re.IGNORECASE,
)
_TZ_AFTER_TIME_RE = re.compile(r"^\s*\(?([A-Z]{1,5})\)?(?![A-Za-z])")
_TZ_OFFSET_AFTER_TIME_RE = re.compile(
    r"^\s*\(?(?:UTC|GMT)?\s*([+-]\d{1,2})(?::?(\d{2}))?\)?(?![\d:])"
)
_TZ_ANYWHERE_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3,5})(?![A-Za-z])")
_QUOTED_HEADER_LINE_RE = re.compile(r"^\s*>?\s*(?:on .*wrote:|sent:)\s*", re.IGNORECASE)
_DEADLINE_CONTEXT_RE = re.compile(
    r"(?:\b(?:by|before|due(?: on| by| date:?)?|deadline(?: is| of|:)?|no later than|until|prior to|expires?(?: on)?|"
    r"submit(?:ted)? by|complete(?:d)? by|return(?:ed)? by)\s+(?:end of day\s+|eod\s+|close of business\s+|midnight\s+|the\s+|on\s+)?)$",
    re.IGNORECASE,
)
_WITHIN_RE = re.compile(
    r"\bwithin (?:the next )?(\d{1,3}|one|two|three|four|five|six|seven|ten|fourteen)\s*"
    r"(business |working |calendar )?(days?|hours?|weeks?)\b",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "ten": 10,
    "fourteen": 14,
}
_DURATION_RE = re.compile(
    r"\b(\d{1,3}(?:\.\d)?)\s*-?\s*(min(?:ute)?s?|hours?|hrs?)\b|\b(an hour|one hour|half an hour|half hour|half-hour)\b",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
_MEETING_PLATFORMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|//|\.)zoom\.us/(?:j|my|w|s)/", re.IGNORECASE), "zoom"),
    (
        re.compile(r"(?:^|//|\.)teams\.microsoft\.com/l/meetup-join", re.IGNORECASE),
        "teams",
    ),
    (re.compile(r"(?:^|//|\.)meet\.google\.com/", re.IGNORECASE), "google_meet"),
    (re.compile(r"(?:^|//|\.)webex\.com/", re.IGNORECASE), "webex"),
)

_PHONE_RE = re.compile(
    r"(?<![\w/.-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])|"
    r"(?<![\w/.-])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?![\w-])"
)
_PHONE_LINE_EXCLUDE_RE = re.compile(
    r"meeting id|passcode|password|conference id|pin:|reference|application id|req",
    re.IGNORECASE,
)

_ID_TOKEN = r"(?=[A-Za-z_\-/.#]*\d)([A-Za-z0-9][A-Za-z0-9_\-/.]{1,39})"
_APPLICATION_ID_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\bapplication\s*(?:id|number|no\.?|#|reference)\s*[:#]?\s*{_ID_TOKEN}",
        rf"\breference(?:\s*(?:id|number|no\.?|code))?\s*[:#]\s*{_ID_TOKEN}",
        rf"\bref\.?\s*[:#]\s*{_ID_TOKEN}",
        rf"\breq(?:uisition)?\s*(?:(?:id|number|no\.?)\s*[:#]?|[:#])\s*{_ID_TOKEN}",
        rf"\bjob\s*(?:id|number|no\.?|#|reference|code)\s*[:#]?\s*{_ID_TOKEN}",
        rf"\bposting\s*(?:id|number|#)\s*[:#]?\s*{_ID_TOKEN}",
        rf"\bconfirmation\s*(?:number|no\.?|#|id|code)\s*[:#]?\s*{_ID_TOKEN}",
    )
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_CAP_WORD = r"[A-Z][A-Za-z0-9&'.-]*"
_CAP_RUN = rf"{_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}}"
_CORP_SUFFIX = (
    r"(?:Inc|LLC|Ltd|Limited|Corp|Corporation|Co|GmbH|AG|SA|Technologies|Labs|"
    r"Group|Holdings|Partners|Systems|Software|Solutions|Networks|Bank|Health|Studios)\.?"
)
_COMPANY_CONTEXT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?i:on behalf of)\s+(?:(?i:the)\s+)?({_CAP_RUN})"),
    re.compile(
        rf"\b(?i:at|join|joining|applying to|apply to|application to|application with|"
        rf"interview(?:ing|s)? with|interview at|opportunity with|role with|position with|"
        rf"careers? at|hiring at|offer from|team at|opening at|opportunity at)\s+({_CAP_RUN})"
    ),
    re.compile(
        rf"\b({_CAP_RUN})\s+(?:(?i:recruiting|talent|hiring|people|careers|recruitment|"
        rf"engineering|university|campus|hr)\s+)?(?i:team)\b"
    ),
    re.compile(rf"\b(?i:with)\s+({_CAP_RUN}\s+{_CORP_SUFFIX})"),
)
_COMPANY_STOPWORDS = frozenset(
    {
        "the",
        "this",
        "that",
        "our",
        "your",
        "my",
        "we",
        "i",
        "you",
        "it",
        "they",
        "he",
        "she",
        "a",
        "an",
        "and",
        "or",
        "at",
        "in",
        "on",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "if",
        "so",
        "but",
        "us",
        "me",
        "here",
        "there",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "am",
        "pm",
        "noon",
        "midnight",
        "today",
        "tomorrow",
        "tonight",
        "next",
        "last",
        "week",
        "weekend",
        "morning",
        "afternoon",
        "evening",
        "hr",
        "best",
        "regards",
        "thanks",
        "thank",
        "hi",
        "hello",
        "dear",
        "please",
        "sincerely",
        "cheers",
        "warm",
        "kind",
        "looking",
        "subject",
        "re",
        "fw",
        "fwd",
        "interview",
        "interviews",
        "application",
        "applications",
        "assessment",
        "zoom",
        "teams",
        "webex",
        "skype",
        "meet",
        "google meet",
        "home",
        "office",
        "headquarters",
        "hq",
        "least",
        "most",
        "all",
        "any",
        "some",
        "time",
        "date",
        "day",
        "phone",
        "video",
        "call",
        "calendar",
        "link",
        "id",
        "et",
        "pt",
        "ct",
        "mt",
        "est",
        "pst",
        "cst",
        "mst",
        "edt",
        "pdt",
        "cdt",
        "mdt",
        "utc",
        "gmt",
        "ist",
        "bst",
        "senior",
        "junior",
        "lead",
        "staff",
        "principal",
        "engineer",
        "developer",
        "manager",
        "director",
        "analyst",
        "designer",
        "scientist",
        "hackerrank",
        "codility",
        "codesignal",
        "calendly",
        "workday",
        "greenhouse",
        "lever",
        "ashby",
        "candidates",
        "candidate",
        "position",
        "role",
        "job",
        "jobs",
        "once",
        "when",
        "where",
        "which",
        "while",
        "after",
        "before",
        "during",
    }
)
_COMPANY_FULL_BLOCKLIST = frozenset(
    {
        "google meet",
        "microsoft teams",
        "zoom",
        "teams",
        "webex",
        "skype",
        "google calendar",
        "calendly",
    }
)
_COMPANY_TRAILING_WORDS = frozenset(
    {
        "team",
        "teams",
        "recruiting",
        "talent",
        "hiring",
        "people",
        "careers",
        "career",
        "recruitment",
        "hr",
        "university",
        "campus",
        "hq",
        "headquarters",
        "office",
        "offices",
        "engineering",
        "product",
        "data",
        "technical",
        "notifications",
        "jobs",
        "job",
        "acquisition",
        "via",
        "noreply",
        "no-reply",
        "staffing",
        "ta",
    }
)
_SENDER_NAME_MARKERS = frozenset(
    {
        "careers",
        "recruiting",
        "talent",
        "hiring",
        "team",
        "hr",
        "jobs",
        "notifications",
        "recruitment",
        "people",
        "acquisition",
        "noreply",
        "no-reply",
        "via",
        "staffing",
        "university",
        "campus",
        "ta",
    }
)

_ROLE_KEYWORDS_RE = re.compile(
    r"\b(?:engineer|engineering|developer|programmer|manager|analyst|designer|scientist|"
    r"architect|consultant|specialist|director|lead|intern|internship|administrator|admin|"
    r"coordinator|associate|representative|technician|writer|researcher|accountant|nurse|"
    r"officer|head|vp|president|principal|staff|product|marketing|sales|recruiter|devops|"
    r"sre|qa|tester|support|success|operations|data|software|backend|back-end|frontend|"
    r"front-end|full-?stack|ml|ai|security|cloud|platform|mobile|ios|android|web|teacher|"
    r"instructor|assistant|executive|counsel|attorney|paralegal|clerk|buyer|planner|"
    r"strategist|editor|producer|artist|animator|pm|program|project|partner|advisor|"
    r"pharmacist|physician|therapist|technologist|electrician|mechanic|driver|chef|cook|"
    r"barista|cashier|agent|broker|underwriter|actuary|auditor|controller|treasurer|"
    r"trainer|coach|librarian|scrum|owner|evangelist|advocate|fellow|apprentice|"
    r"generalist|salesforce|java|python)\b",
    re.IGNORECASE,
)
_ROLE_TOKEN = r"(?:[A-Z][\w+#./()-]*|iOS|macOS|iPaaS|[\w+#./()-]*\d[\w+#./()-]*|of|and|&|-|–|/|[IVX]+)"
_ROLE_TOKEN_RUN = rf"{_ROLE_TOKEN}(?:\s+{_ROLE_TOKEN}){{0,7}}"
_ROLE_TERMINATOR = r"(?=\s+(?:position|role|opening|at|with|\(|-|–|—|\|)|[.,;:!\n]|$)"
_ROLE_FILLER = r"(?:(?!\b(?:for|to|regarding|about)\s)[^\n]){2,120}?"
# (regex, require_role_keyword, subject_only)
_ROLE_RES: tuple[tuple[re.Pattern[str], bool, bool], ...] = (
    (
        re.compile(
            rf"\b(?:for|to|regarding|about|re)\s+(?:the|our|a|an|this|your)\s+({_ROLE_FILLER})\s+"
            r"(?:position|role|opening|opportunity|job|vacancy)\b",
            re.IGNORECASE,
        ),
        False,
        False,
    ),
    (
        re.compile(
            rf"({_ROLE_TOKEN_RUN})\s+(?i:position|role|opening|opportunity|vacancy)\s+(?i:at|with|in|on)\b"
        ),
        False,
        False,
    ),
    (
        re.compile(
            r"^\s*(?:position|role|job title|title|job|opening)\s*:\s*(.{2,120}?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        False,
        False,
    ),
    (
        re.compile(
            r"\b(?:application|applied|applying|apply|interview|offer|opening|opportunity|candidacy)\s+"
            rf"(?:for|to|as)\s+(?:the\s+|our\s+|a\s+|an\s+)?(.{{2,120}}?){_ROLE_TERMINATOR}",
            re.IGNORECASE,
        ),
        True,
        False,
    ),
    (
        re.compile(
            r"\b(?:position|role|title|job|opening|opportunity)\s+(?:of|for|as)\s+(?:a\s+|an\s+|the\s+)?"
            rf"(.{{2,120}}?){_ROLE_TERMINATOR}",
            re.IGNORECASE,
        ),
        True,
        False,
    ),
    (
        re.compile(
            rf"^\s*(?:(?i:re|fw|fwd)\s*:\s*)?({_ROLE_TOKEN_RUN})\s+(?i:opportunity|opening|position|role|vacancy)\b"
        ),
        True,
        True,
    ),
    (re.compile(r"^\s*(?:re|fw|fwd)\s*:\s*(.{2,120}?)\s*$", re.IGNORECASE), True, True),
    (
        re.compile(
            r"^\s*(?:re\s*:\s*|fw\s*:\s*|fwd\s*:\s*)?(?:[^:\n]{0,40}?(?:interview|invitation|application|"
            r"offer|assessment|update|opportunity|position|role|opening|received|confirmation)[^:\n]{0,40})"
            r":\s*(.{2,120}?)\s*$",
            re.IGNORECASE,
        ),
        True,
        True,
    ),
)
_ROLE_LEADING_RE = re.compile(
    r"^(?:(?:the|our|a|an|this|your|role of|position of|job of)\s+)+", re.IGNORECASE
)
# "interest in the Senior Engineer" -> keep only what follows the last determiner phrase.
_ROLE_INNER_DETERMINER_RE = re.compile(
    r"^.*\b(?:in|for|to|of|on|about|regarding|as)\s+(?:the|our|a|an|this|your)\s+",
    re.IGNORECASE,
)
_ROLE_TRAILING_CUT_RE = re.compile(r"\s+(?:at|with)\s+[A-Z].*$|\s+[-–—|]\s+.*$")
_ROLE_PLACEHOLDERS = frozenset(
    {
        "open",
        "same",
        "right",
        "following",
        "next",
        "first",
        "second",
        "new",
        "other",
        "above",
        "this",
        "that",
        "said",
        "current",
        "relevant",
        "available",
        "full-time",
        "part-time",
        "remote",
        "contract",
        "mentioned",
        "advertised",
        "posted",
        "listed",
    }
)
_ROLE_REJECT_RE = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|today|next week|this week)\b|\bwith (?:the|our|your|me|us)\b|\byour\b|\bwe\b|\bplease\b|"
    r"\bhttps?://",
    re.IGNORECASE,
)

_SIGNOFF_RE = re.compile(
    r"^\s*(?:best|best regards|kind regards|warm regards|regards|thanks|thank you|many thanks|"
    r"sincerely|cheers|all the best|warmly|talk soon|looking forward)[,!.]*\s*$",
    re.IGNORECASE,
)
_NAME_LINE_RE = re.compile(r"^\s*([A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){1,3})\s*$")


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def _normalize_text(text: str) -> str:
    """Normalize line endings, odd spaces and curly quotes without changing meaning."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    for odd_space in (" ", " ", " ", " "):
        text = text.replace(odd_space, " ")
    text = text.replace("​", "")
    text = text.replace("’", "'").replace("‘", "'")
    return text.replace("“", '"').replace("”", '"')


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _line_of(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start : end if end != -1 else len(text)]


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(
        start < other_end and end > other_start for other_start, other_end in spans
    )


# --------------------------------------------------------------------------- #
# Date / time extraction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _DateHit:
    start: int
    end: int
    year: int | None
    month: int
    day: int
    is_deadline: bool


@dataclass(frozen=True, slots=True)
class _TimeHit:
    start: int
    end: int
    hour: int
    minute: int
    end_hour: int | None
    end_minute: int | None
    tz_token: str | None


def _to_24h(hour: int, minute: int, meridiem: str | None) -> tuple[int, int] | None:
    if minute > 59 or hour < 0:
        return None
    if meridiem is None:
        return (hour, minute) if hour <= 23 else None
    if not 1 <= hour <= 12:
        return None
    is_pm = meridiem.lower().startswith("p")
    if is_pm and hour != 12:
        hour += 12
    elif not is_pm and hour == 12:
        hour = 0
    return hour, minute


def _is_deadline_context(text: str, start: int) -> bool:
    return _DEADLINE_CONTEXT_RE.search(text[max(0, start - 48) : start]) is not None


def _find_dates(text: str, masked: list[tuple[int, int]]) -> list[_DateHit]:
    hits: list[_DateHit] = []
    spans = list(masked)

    def add(start: int, end: int, year: int | None, month: int, day: int) -> None:
        if _overlaps(start, end, spans):
            return
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return
        if _QUOTED_HEADER_LINE_RE.match(_line_of(text, start)):
            return
        spans.append((start, end))
        hits.append(
            _DateHit(start, end, year, month, day, _is_deadline_context(text, start))
        )

    for match in _ISO_DATE_RE.finditer(text):
        add(
            match.start(),
            match.end(),
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    for match in _MDY_DATE_RE.finditer(text):
        month = _MONTH_INDEX[match.group(1)[:3].lower()]
        year = int(match.group(3)) if match.group(3) else None
        add(match.start(), match.end(), year, month, int(match.group(2)))
    for match in _DMY_DATE_RE.finditer(text):
        month = _MONTH_INDEX[match.group(2)[:3].lower()]
        year = int(match.group(3)) if match.group(3) else None
        add(match.start(), match.end(), year, month, int(match.group(1)))
    for match in _NUMERIC_DATE_RE.finditer(text):
        first, second, year = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if year < 100:
            year += 2000
        month, day = (first, second) if first <= 12 else (second, first)
        add(match.start(), match.end(), year, month, day)
    hits.sort(key=lambda hit: hit.start)
    return hits


def _resolve_date(hit: _DateHit, received_at: datetime | None) -> date | None:
    reference = received_at.date() if received_at is not None else datetime.now().date()
    year = hit.year if hit.year is not None else reference.year
    try:
        candidate = date(year, hit.month, hit.day)
    except ValueError:
        return None
    if hit.year is None and candidate < reference - timedelta(days=7):
        try:
            candidate = date(year + 1, hit.month, hit.day)
        except ValueError:
            return None
    return candidate


def _tz_token_after(text: str, position: int) -> str | None:
    window = text[position : position + 16]
    offset = _TZ_OFFSET_AFTER_TIME_RE.match(window)
    if offset:
        hours = int(offset.group(1))
        minutes = int(offset.group(2) or 0)
        if abs(hours) <= 14 and minutes <= 59:
            sign = "-" if hours < 0 else "+"
            return f"{sign}{abs(hours):02d}:{minutes:02d}"
    abbrev = _TZ_AFTER_TIME_RE.match(window)
    if abbrev:
        token = abbrev.group(1)
        if len(token) >= 2 and token not in _NOT_TIMEZONE_TOKENS:
            return token
    return None


def _find_times(text: str, masked: list[tuple[int, int]]) -> list[_TimeHit]:
    hits: list[_TimeHit] = []
    spans = list(masked)
    for match in _TIME_RANGE_RE.finditer(text):
        if _overlaps(match.start(), match.end(), spans):
            continue
        start_colon, end_colon = match.group(2) is not None, match.group(5) is not None
        meridiem_start, meridiem_end = match.group(3), match.group(6)
        if (
            meridiem_start is None
            and meridiem_end is None
            and not (start_colon and end_colon)
        ):
            continue
        raw_start, raw_end = int(match.group(1)), int(match.group(4))
        if meridiem_start is None and meridiem_end is not None:
            same_half = raw_start <= raw_end or raw_start == 12
            if same_half:
                meridiem_start = meridiem_end
            else:
                meridiem_start = "am" if meridiem_end.lower().startswith("p") else "pm"
        elif meridiem_end is None and meridiem_start is not None:
            meridiem_end = meridiem_start
        start_time = _to_24h(raw_start, int(match.group(2) or 0), meridiem_start)
        end_time = _to_24h(raw_end, int(match.group(5) or 0), meridiem_end)
        if start_time is None or end_time is None:
            continue
        spans.append((match.start(), match.end()))
        hits.append(
            _TimeHit(
                match.start(),
                match.end(),
                start_time[0],
                start_time[1],
                end_time[0],
                end_time[1],
                _tz_token_after(text, match.end()),
            )
        )
    for match in _TIME_RE.finditer(text):
        if _overlaps(match.start(), match.end(), spans):
            continue
        if match.group(1) is not None:
            parsed = _to_24h(int(match.group(1)), int(match.group(2)), match.group(3))
        else:
            parsed = _to_24h(int(match.group(4)), 0, match.group(5))
        if parsed is None:
            continue
        spans.append((match.start(), match.end()))
        hits.append(
            _TimeHit(
                match.start(),
                match.end(),
                parsed[0],
                parsed[1],
                None,
                None,
                _tz_token_after(text, match.end()),
            )
        )
    hits.sort(key=lambda hit: hit.start)
    return hits


def _resolve_timezone(
    token: str | None,
) -> tuple[str | None, timezone | ZoneInfo | None]:
    """Return (label, tzinfo). The label is an IANA name or the literal token."""
    if not token:
        return None, None
    if token[0] in "+-":
        hours, minutes = int(token[1:3]), int(token[4:6])
        delta = timedelta(hours=hours, minutes=minutes)
        if token[0] == "-":
            delta = -delta
        if delta == timedelta(0):
            return "UTC", timezone.utc
        return token, timezone(delta)
    name = TIMEZONE_ABBREVIATIONS.get(token.upper())
    if name is None and ("/" in token or token == "UTC"):
        name = token
    if name is None:
        return token, None
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return token, None


def _timezone_anywhere(text: str) -> str | None:
    for pattern, name in _TIMEZONE_NAMES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    for match in _TZ_ANYWHERE_RE.finditer(text):
        token = match.group(1)
        if token in TIMEZONE_ABBREVIATIONS and token not in _NOT_TIMEZONE_TOKENS:
            return token
    return None


def _format_start(
    day: date, hour: int, minute: int, tzinfo: timezone | ZoneInfo
) -> str:
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=tzinfo
    ).isoformat()


def _explicit_duration(text: str) -> int | None:
    for match in _DURATION_RE.finditer(text):
        if match.group(3):
            phrase = match.group(3).lower()
            return 60 if "an hour" in phrase or "one hour" in phrase else 30
        amount = float(match.group(1))
        unit = match.group(2).lower()
        minutes = (
            int(round(amount * 60)) if unit.startswith(("h",)) else int(round(amount))
        )
        if 1 <= minutes <= 720:
            return minutes
    return None


def _business_days_after(start: date, days: int) -> date:
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _deadline_from_within(text: str, received_at: datetime | None) -> str | None:
    match = _WITHIN_RE.search(text)
    if match is None or received_at is None:
        return None
    raw = match.group(1).lower()
    amount = int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
    qualifier = (match.group(2) or "").strip().lower()
    unit = match.group(3).lower()
    if unit.startswith("hour"):
        return (received_at + timedelta(hours=amount)).isoformat(timespec="minutes")
    if unit.startswith("week"):
        amount *= 7
    if qualifier in {"business", "working"}:
        return _business_days_after(received_at.date(), amount).isoformat()
    return (received_at.date() + timedelta(days=amount)).isoformat()


@dataclass(frozen=True, slots=True)
class _Schedule:
    date: str | None = None
    time: str | None = None
    timezone: str | None = None
    start_at: str | None = None
    duration_minutes: int | None = None
    assessment_deadline: str | None = None


def _time_following(text: str, position: int, times: list[_TimeHit]) -> _TimeHit | None:
    for hit in times:
        if position <= hit.start <= position + 30:
            return hit
    return None


def _extract_schedule(text: str, received_at: datetime | None) -> _Schedule:
    iso_hits: list[tuple[int, int, re.Match[str]]] = [
        (match.start(), match.end(), match) for match in _ISO_DATETIME_RE.finditer(text)
    ]
    masked = [(start, end) for start, end, _ in iso_hits]
    dates = _find_dates(text, masked)
    times = _find_times(text, masked)
    deadline_dates = [hit for hit in dates if hit.is_deadline]
    deadline_time_spans: list[tuple[int, int]] = []

    deadline: str | None = None
    for hit in deadline_dates:
        resolved = _resolve_date(hit, received_at)
        if resolved is None:
            continue
        following = _time_following(text, hit.end, times)
        if following is not None:
            deadline_time_spans.append((following.start, following.end))
            label, tzinfo = _resolve_timezone(following.tz_token)
            if tzinfo is None:
                anywhere = _timezone_anywhere(text)
                label, tzinfo = (
                    _resolve_timezone(anywhere) if anywhere else (label, None)
                )
            if tzinfo is not None:
                deadline = _format_start(
                    resolved, following.hour, following.minute, tzinfo
                )
            else:
                deadline = f"{resolved.isoformat()}T{following.hour:02d}:{following.minute:02d}"
        else:
            deadline = resolved.isoformat()
        break
    if deadline is None:
        deadline = _deadline_from_within(text, received_at)

    iso_candidates = [
        (start, end, match)
        for start, end, match in iso_hits
        if not _is_deadline_context(text, start)
    ]
    if iso_candidates:
        start_pos, end_pos, match = iso_candidates[0]
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hour, minute = int(match.group(4)), int(match.group(5))
        try:
            resolved = date(year, month, day)
        except ValueError:
            resolved = None
        if resolved is not None and hour <= 23 and minute <= 59:
            offset_token = match.group(7)
            if offset_token:
                offset_token = "+00:00" if offset_token == "Z" else offset_token
                if ":" not in offset_token:
                    offset_token = f"{offset_token[:3]}:{offset_token[3:]}"
            tz_token = (
                offset_token
                or _tz_token_after(text, end_pos)
                or _timezone_anywhere(text)
            )
            label, tzinfo = _resolve_timezone(tz_token)
            start_at = (
                _format_start(resolved, hour, minute, tzinfo)
                if tzinfo is not None
                else None
            )
            return _Schedule(
                date=resolved.isoformat(),
                time=f"{hour:02d}:{minute:02d}",
                timezone=label,
                start_at=start_at,
                duration_minutes=_explicit_duration(text),
                assessment_deadline=deadline,
            )

    event_dates = [hit for hit in dates if not hit.is_deadline]
    event_times = [
        hit for hit in times if not _overlaps(hit.start, hit.end, deadline_time_spans)
    ]

    chosen_time: _TimeHit | None = None
    chosen_date: _DateHit | None = None
    if event_times and event_dates:
        best: tuple[int, int] | None = None
        for time_index, time_hit in enumerate(event_times):
            for date_hit in event_dates:
                distance = min(
                    abs(time_hit.start - date_hit.end),
                    abs(date_hit.start - time_hit.end),
                )
                if best is None or distance < best[0]:
                    best = (distance, time_index)
                    chosen_time, chosen_date = time_hit, date_hit
    elif event_times:
        chosen_time = event_times[0]
    elif event_dates:
        chosen_date = event_dates[0]

    resolved_date = (
        _resolve_date(chosen_date, received_at) if chosen_date is not None else None
    )
    time_text = (
        f"{chosen_time.hour:02d}:{chosen_time.minute:02d}"
        if chosen_time is not None
        else None
    )
    tz_token = chosen_time.tz_token if chosen_time is not None else None
    if tz_token is None and chosen_time is not None:
        tz_token = _timezone_anywhere(text)
    label, tzinfo = _resolve_timezone(tz_token)

    duration: int | None = None
    if (
        chosen_time is not None
        and chosen_time.end_hour is not None
        and chosen_time.end_minute is not None
    ):
        delta = (chosen_time.end_hour * 60 + chosen_time.end_minute) - (
            chosen_time.hour * 60 + chosen_time.minute
        )
        if delta <= 0:
            delta += 24 * 60
        duration = delta
    if duration is None:
        duration = _explicit_duration(text)

    start_at = None
    if resolved_date is not None and chosen_time is not None and tzinfo is not None:
        start_at = _format_start(
            resolved_date, chosen_time.hour, chosen_time.minute, tzinfo
        )
    return _Schedule(
        date=resolved_date.isoformat() if resolved_date is not None else None,
        time=time_text,
        timezone=label,
        start_at=start_at,
        duration_minutes=duration,
        assessment_deadline=deadline,
    )


# --------------------------------------------------------------------------- #
# Links, phone numbers, ids
# --------------------------------------------------------------------------- #


def _find_urls(text: str) -> list[tuple[int, int, str]]:
    urls: list[tuple[int, int, str]] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?'\">")
        urls.append((match.start(), match.start() + len(url), url))
    return urls


def _meeting_link(urls: list[tuple[int, int, str]]) -> tuple[str | None, str | None]:
    for _, _, url in urls:
        for pattern, platform in _MEETING_PLATFORMS:
            if pattern.search(url):
                return url, platform
    return None, None


def _contact_phone(text: str, url_spans: list[tuple[int, int]]) -> str | None:
    for match in _PHONE_RE.finditer(text):
        if _overlaps(match.start(), match.end(), url_spans):
            continue
        if _PHONE_LINE_EXCLUDE_RE.search(_line_of(text, match.start())):
            continue
        digits = re.sub(r"\D", "", match.group(0))
        if 10 <= len(digits) <= 15:
            return _collapse_whitespace(match.group(0))
    return None


def _application_id(text: str) -> str | None:
    for pattern in _APPLICATION_ID_RES:
        match = pattern.search(text)
        if match:
            token = match.group(1).rstrip(".,;:)/-")
            if len(token) >= 2:
                return token
    return None


# --------------------------------------------------------------------------- #
# Company, role, recruiter
# --------------------------------------------------------------------------- #


def _clean_company(candidate: str) -> str | None:
    tokens = _collapse_whitespace(candidate).split(" ")
    while tokens and tokens[0].lower().strip(".,") in _COMPANY_STOPWORDS:
        tokens.pop(0)
    kept: list[str] = []
    for token in tokens:
        if token.lower().strip(".,") in _COMPANY_STOPWORDS:
            break
        kept.append(token)
    while kept and kept[-1].lower().strip(".,") in _COMPANY_TRAILING_WORDS:
        kept.pop()
    if not kept:
        return None
    name = " ".join(kept)
    name = re.sub(r"(?:'s|')$", "", name).strip(" .,;:!?\"'")
    if len(name) < 2 or len(name) > 80 or not name[0].isalpha():
        return None
    if name.lower() in _COMPANY_FULL_BLOCKLIST:
        return None
    return name


def _company_from_domain(address: str) -> str | None:
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].lower().strip(" >")
    labels = [label for label in domain.split(".") if label]
    if len(labels) < 2:
        return None
    registrable = labels[-2]
    if registrable in _PUBLIC_SUFFIX_LABELS and len(labels) >= 3:
        registrable = labels[-3]
    if registrable in GENERIC_SENDER_DOMAINS or any(
        label in GENERIC_SENDER_DOMAINS for label in labels[:-1]
    ):
        return None
    if len(registrable) < 2 or registrable.isdigit():
        return None
    return " ".join(
        part.capitalize() for part in re.split(r"[-_]+", registrable) if part
    )


def _company_from_sender_name(sender_name: str) -> str | None:
    tokens = _collapse_whitespace(
        sender_name.replace("|", " ").replace("-", " - ")
    ).split(" ")
    tokens = [token for token in tokens if token not in {"-", "|"}]
    stripped = False
    while tokens and tokens[-1].lower().strip(".,()") in _SENDER_NAME_MARKERS:
        tokens.pop()
        stripped = True
    while tokens and tokens[0].lower().strip(".,()") in _SENDER_NAME_MARKERS:
        tokens.pop(0)
        stripped = True
    if not stripped or not tokens:
        return None
    return _clean_company(" ".join(tokens))


def _extract_company(
    subject: str,
    body: str,
    sender: str,
    sender_name: str,
    known_companies: Sequence[str],
) -> str | None:
    haystack = "\n".join((subject, body, sender_name, sender))
    candidates: list[str] = [
        name.strip() for name in known_companies if name and name.strip()
    ]
    candidates.sort(key=lambda name: len(name), reverse=True)
    for known in candidates:
        pattern = rf"(?<![\w]){re.escape(known)}(?![\w])"
        if re.search(pattern, haystack, re.IGNORECASE):
            return known
    text = "\n".join((subject, body))
    for pattern in _COMPANY_CONTEXT_RES:
        for match in pattern.finditer(text):
            cleaned = _clean_company(match.group(1))
            if cleaned:
                return cleaned
    from_sender_name = _company_from_sender_name(sender_name) if sender_name else None
    if from_sender_name:
        return from_sender_name
    return _company_from_domain(sender)


def _clean_role(candidate: str, require_keyword: bool) -> str | None:
    role = _collapse_whitespace(candidate).strip("\"'")
    role = _ROLE_LEADING_RE.sub("", role)
    role = _ROLE_INNER_DETERMINER_RE.sub("", role)
    role = _ROLE_TRAILING_CUT_RE.sub("", role)
    role = role.strip(" .,;:!?-–—|\"'")
    if len(role) < 2:
        return None
    if role.lower() in _ROLE_PLACEHOLDERS or _ROLE_REJECT_RE.search(role):
        return None
    if require_keyword and not _ROLE_KEYWORDS_RE.search(role):
        return None
    return role[:_MAX_ROLE_CHARS].rstrip(" .,;:!?-")


def _extract_role(subject: str, body: str) -> str | None:
    for pattern, require_keyword, subject_only in _ROLE_RES:
        sources = (subject,) if subject_only else (subject, body)
        for source in sources:
            for match in pattern.finditer(source):
                cleaned = _clean_role(match.group(1), require_keyword)
                if cleaned:
                    return cleaned
    return None


def _is_automated_sender(sender: str) -> bool:
    if "@" not in sender:
        return True
    local, domain = sender.rsplit("@", 1)
    if _NOREPLY_LOCAL_PARTS.match(local.strip()):
        return True
    labels = domain.lower().split(".")
    return any(label in _ATS_DOMAINS for label in labels[:-1])


def _looks_like_person(name: str) -> bool:
    tokens = [
        token.lower().strip(".,()")
        for token in _collapse_whitespace(name).split(" ")
        if token
    ]
    if not 1 <= len(tokens) <= 4:
        return False
    return not any(
        token in _SENDER_NAME_MARKERS or token in _COMPANY_TRAILING_WORDS
        for token in tokens
    )


def _signature_name(body: str) -> str | None:
    lines = body.split("\n")
    for index, line in enumerate(lines):
        if _SIGNOFF_RE.match(line):
            for candidate in lines[index + 1 : index + 3]:
                match = _NAME_LINE_RE.match(candidate)
                if match and _looks_like_person(match.group(1)):
                    return match.group(1)
            break
    return None


def _extract_recruiter(
    sender: str, sender_name: str, body: str
) -> tuple[str | None, str | None]:
    sender = sender.strip().lower()
    if sender and not _is_automated_sender(sender):
        name = _collapse_whitespace(sender_name)
        if not _looks_like_person(name):
            name = ""
        return (name or _signature_name(body) or None), sender
    return _signature_name(body), None


def _interview_type(text: str) -> str | None:
    onsite_panel = bool(_ONSITE_RE.search(text) and _MULTI_INTERVIEWER_RE.search(text))
    for pattern, interview_type in _INTERVIEW_TYPE_RULES:
        if interview_type == "panel_interview" and onsite_panel:
            return interview_type
        if re.search(pattern, text, re.IGNORECASE):
            return interview_type
    return None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


class _CategoryScore:
    __slots__ = ("score", "signals", "strong", "weak")

    def __init__(self) -> None:
        self.score = 0.0
        self.strong = 0
        self.weak = 0
        self.signals: list[str] = []

    def add(self, base_weight: float, effective_weight: float, signal: str) -> None:
        self.score += effective_weight
        if base_weight >= _STRONG_SIGNAL:
            self.strong += 1
        else:
            self.weak += 1
        self.signals.append(signal)


def _score_categories(subject: str, body: str) -> dict[str, _CategoryScore]:
    scores = {category: _CategoryScore() for category in _SIGNAL_RULES}
    for category, rules in _COMPILED_SIGNALS.items():
        entry = scores[category]
        for pattern, weight, label in rules:
            if pattern.search(subject):
                entry.add(
                    weight, weight * _SUBJECT_WEIGHT, f'subject contains "{label}"'
                )
            if pattern.search(body):
                entry.add(weight, weight, f'body contains "{label}"')
    return scores


def _select_category(
    scores: dict[str, _CategoryScore],
) -> tuple[str, float, tuple[str, ...]]:
    ranked = sorted(
        scores.items(), key=lambda item: (-item[1].score, _PRECEDENCE.index(item[0]))
    )
    top_category, top = ranked[0]
    if top.score <= 0:
        return "unrelated", _UNRELATED_CONFIDENCE, ("no job-search signals matched",)

    extra_signals: list[str] = []
    if top_category == "application_update":
        specific = [name for name in _OUTRANKS_UPDATE if scores[name].strong > 0]
        if specific:
            winner = min(
                specific,
                key=lambda name: (-scores[name].score, _PRECEDENCE.index(name)),
            )
            extra_signals.append(
                f'specific outcome "{winner}" outranks a generic application update'
            )
            top_category, top = winner, scores[winner]

    excluded = {top_category}
    if top_category in _OUTRANKS_UPDATE:
        excluded.add("application_update")
    runner_up = max(
        (entry.score for name, entry in scores.items() if name not in excluded),
        default=0.0,
    )

    confidence = 0.5 + 0.1 * top.strong + 0.05 * top.weak
    if runner_up > 0:
        ratio = runner_up / top.score
        if ratio >= 0.75:
            confidence -= 0.15
        elif ratio >= 0.5:
            confidence -= 0.05
    confidence = round(min(0.98, max(0.3, confidence)), 2)
    return top_category, confidence, tuple(top.signals + extra_signals)


def classify_email(
    subject: str,
    body: str,
    *,
    sender: str = "",
    sender_name: str = "",
    received_at: datetime | None = None,
    known_companies: Sequence[str] = (),
) -> EmailClassification:
    """Classify one email and extract only the details it explicitly contains.

    ``subject`` and ``body`` are plain text (use :func:`html_to_text` for HTML).
    ``sender`` is the sender address, ``sender_name`` the display name. The
    message content is treated strictly as data: it is matched against fixed
    patterns and never interpreted as instructions.
    """
    subject_text = _collapse_whitespace(_normalize_text(subject or ""))
    body_text = _normalize_text(body or "")[:MAX_TEXT_CHARS]
    full_text = f"{subject_text}\n{body_text}"
    sender_text = (sender or "").strip()
    sender_name_text = _collapse_whitespace(sender_name or "")

    scores = _score_categories(subject_text, body_text)

    urls = _find_urls(full_text)
    url_spans = [(start, end) for start, end, _ in urls]
    meeting_link, meeting_platform = _meeting_link(urls)
    schedule = _extract_schedule(full_text, received_at)

    if meeting_link and (schedule.date or schedule.time):
        scores["interview_invitation"].add(
            _STRONG_SIGNAL,
            _STRONG_SIGNAL,
            f"body contains a {meeting_platform} meeting link with a date/time",
        )
    if schedule.assessment_deadline:
        scores["assessment_request"].add(1.0, 1.0, "body contains a deadline")

    category, confidence, signals = _select_category(scores)

    recruiter_name, recruiter_email = _extract_recruiter(
        sender_text, sender_name_text, body_text
    )
    details = ExtractedDetails(
        company=_extract_company(
            subject_text, body_text, sender_text, sender_name_text, known_companies
        ),
        role=_extract_role(subject_text, body_text),
        recruiter_name=recruiter_name,
        recruiter_email=recruiter_email,
        contact_phone=_contact_phone(full_text, url_spans),
        date=schedule.date,
        time=schedule.time,
        timezone=schedule.timezone,
        start_at=schedule.start_at,
        duration_minutes=schedule.duration_minutes,
        meeting_link=meeting_link,
        meeting_platform=meeting_platform,
        assessment_deadline=schedule.assessment_deadline
        if category == "assessment_request"
        else None,
        application_id=_application_id(full_text),
        interview_type=_interview_type(full_text)
        if category == "interview_invitation"
        else None,
    )
    return EmailClassification(
        category=category, confidence=confidence, signals=signals, details=details
    )


# --------------------------------------------------------------------------- #
# HTML and EML handling
# --------------------------------------------------------------------------- #

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "ul",
        "ol",
        "blockquote",
        "section",
        "article",
        "header",
        "footer",
        "pre",
        "address",
        "dd",
        "dt",
        "dl",
        "form",
        "fieldset",
        "main",
        "nav",
        "aside",
        "figure",
        "figcaption",
    }
)
_LINE_BREAK_TAGS = frozenset({"br", "hr"})
_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "head",
        "noscript",
        "template",
        "iframe",
        "object",
        "embed",
        "svg",
        "title",
    }
)


class _TextExtractor(HTMLParser):
    """Collects visible text; never evaluates scripts, styles or resources."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._href: str | None = None
        self._anchor_start = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            self._href = (
                href if href.lower().startswith(("http://", "https://")) else None
            )
            self._anchor_start = len(self.parts)
        if tag in _LINE_BREAK_TAGS or tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS or self._skip_depth:
            return
        if tag in _LINE_BREAK_TAGS or tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a" and self._href:
            anchor_text = "".join(self.parts[self._anchor_start :])
            if self._href not in anchor_text:
                self.parts.append(f" <{self._href}>")
            self._href = None
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Convert HTML to plain text safely.

    Drops ``<script>``/``<style>``/``<head>`` content, decodes entities, keeps
    line breaks for block-level tags and appends ``<href>`` after link text so
    meeting links survive. Nothing is fetched, rendered or executed.
    """
    if not html:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(_normalize_text(html))
        extractor.close()
    except Exception:  # noqa: BLE001 - malformed markup must never raise
        pass
    text = "".join(extractor.parts)
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _header_text(message: Message, name: str) -> str:
    try:
        value = message.get(name)
    except Exception:  # noqa: BLE001 - defective headers degrade to empty strings
        return ""
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if "=?" in text:
        try:
            text = str(email.header.make_header(email.header.decode_header(text)))
        except Exception:  # noqa: BLE001
            pass
    return _collapse_whitespace(_normalize_text(text))


def _part_text(part: Message) -> str:
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
            if isinstance(content, str):
                return content
        except Exception:  # noqa: BLE001 - fall back to a tolerant decode
            pass
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _part_size(part: Message) -> int:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        return 0
    return len(payload) if isinstance(payload, (bytes, bytearray)) else 0


def parse_eml(data: bytes) -> ParsedEmail:
    """Parse raw RFC 822 bytes into a text-only :class:`ParsedEmail`.

    text/plain is preferred, text/html is converted with :func:`html_to_text`.
    Attachments are listed by filename, content type and size only; their
    bytes are never returned. Text is bounded to ``MAX_TEXT_CHARS``.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("parse_eml expects bytes")
    message = email.message_from_bytes(bytes(data), policy=email.policy.default)

    subject = _header_text(message, "subject")
    sender_name, sender_email = email.utils.parseaddr(_header_text(message, "from"))
    recipients: list[str] = []
    for header in ("to", "cc"):
        for _, address in email.utils.getaddresses([_header_text(message, header)]):
            if address and address not in recipients:
                recipients.append(address)

    received_at: str | None = None
    date_header = _header_text(message, "date")
    if date_header:
        try:
            received_at = email.utils.parsedate_to_datetime(date_header).isoformat()
        except (TypeError, ValueError, IndexError):
            received_at = None

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, object]] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if content_type in ("text/plain", "text/html") and disposition != "attachment":
            text = _part_text(part)
            (plain_parts if content_type == "text/plain" else html_parts).append(text)
            continue
        attachments.append(
            {
                "filename": _collapse_whitespace(filename or ""),
                "content_type": content_type,
                "size": _part_size(part),
            }
        )

    if plain_parts:
        text = "\n\n".join(plain_parts)
    elif html_parts:
        text = html_to_text("\n".join(html_parts))
    else:
        text = ""
    text = _normalize_text(text).strip()[:MAX_TEXT_CHARS]

    return ParsedEmail(
        subject=subject,
        sender_name=_collapse_whitespace(sender_name),
        sender_email=sender_email.strip().lower(),
        recipients=tuple(recipients),
        received_at=received_at,
        text=text,
        attachments=tuple(attachments),
        message_id=_header_text(message, "message-id"),
    )


# --------------------------------------------------------------------------- #
# Fingerprint and actions
# --------------------------------------------------------------------------- #


def message_fingerprint(subject: str, sender: str, body: str) -> str:
    """Stable sha256 over normalized subject, sender and the first 2000 body chars."""

    def normalize(value: str) -> str:
        return _collapse_whitespace(_normalize_text(value or "")).casefold()

    payload = "\n".join(
        (
            normalize(subject),
            normalize(sender),
            normalize(body)[:_FINGERPRINT_BODY_CHARS],
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def suggest_action(
    classification: EmailClassification,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> SuggestedAction:
    """Map a classification to one proposed action the user confirms."""
    category = classification.category
    details = classification.details
    low_confidence = classification.confidence < threshold
    sensitive = category in _SENSITIVE_CATEGORIES
    requires_confirmation = low_confidence or sensitive

    if category == "interview_invitation":
        stage = details.interview_type or "recruiter_screen"
        kind, reason = (
            "create_interview",
            f"interview invitation detected; stage {stage}",
        )
    elif category == "assessment_request":
        kind, stage, reason = (
            "record_assessment",
            "assessment",
            "assessment request detected",
        )
    elif category == "application_confirmation":
        kind, stage, reason = (
            "set_stage",
            "applied",
            "application confirmation detected",
        )
    elif category == "rejection":
        kind, stage, reason = (
            "set_stage",
            "rejected",
            "rejection detected; always confirm before closing",
        )
    elif category == "offer":
        kind, stage, reason = (
            "set_stage",
            "offer",
            "offer detected; always confirm before recording",
        )
    elif category == "recruiter_message":
        kind, stage, reason = "add_recruiter", None, "recruiter outreach detected"
    else:
        kind, stage, reason = "none", None, f"no action for category {category}"

    if low_confidence:
        reason = f"{reason} (confidence {classification.confidence:.2f} below threshold {threshold:.2f})"
    return SuggestedAction(
        kind=kind,
        stage=stage,
        requires_confirmation=requires_confirmation,
        reason=reason,
    )
