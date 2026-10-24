"""Reply policy and guardrails for LinkedIn conversation replies.

Each conversation thread has a reply mode:

* ``manual`` (default): Aira drafts replies; the user reviews and presses Send.
* ``auto``: during inbox checks Aira may send its drafted reply automatically,
  but only when every deterministic guardrail below passes. Anything that
  fails a guardrail is held for the user's review instead of being sent.

Guardrails never call a model; they are simple, explainable rules so the user
can predict exactly when an automatic reply will and will not go out.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

REPLY_MODES = ("manual", "auto")
DEFAULT_COOLDOWN = timedelta(hours=6)
MIN_REPLY_CHARS = 15
MAX_REPLY_CHARS = 1200
MAX_MANUAL_REPLY_CHARS = 3000

# Topics where an automatic answer could commit the user or disclose sensitive
# information. A match in the inbound conversation tail or in the draft holds
# the reply for review.
SENSITIVE_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "compensation",
        re.compile(
            r"\b(salary|compensation|pay rate|hourly rate|rate expectations?|expected (?:ctc|pay)|ctc|bill rate|total comp|equity|bonus)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "work authorization",
        re.compile(
            r"\b(visa|sponsor(?:ship)?|work authori[sz]ation|authori[sz]ed to work|citizen(?:ship)?|green card|h-?1b|opt|cpt|ead)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "personal identifiers",
        re.compile(
            r"\b(ssn|social security|date of birth|dob|passport|driver'?s licen[cs]e|bank account|routing number)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "offer or contract",
        re.compile(
            r"\b(offer letter|accept(?:ing)? the offer|sign(?:ing)? the (?:offer|contract)|employment contract|non-?compete|start date)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "background check",
        re.compile(
            r"\b(background check|drug (?:test|screen)|references?\b(?= (?:check|contact)))\b",
            re.IGNORECASE,
        ),
    ),
    ("relocation", re.compile(r"\b(relocat(?:e|ion)|move to)\b", re.IGNORECASE)),
)
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]?){9,}")
_MONEY_RE = re.compile(
    r"(?:[$€£₹]\s?\d|\b\d{2,3}\s?k\b|\b\d+(?:\.\d+)?\s?(?:usd|lpa|per hour|/hr|an hour)\b)",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\[[^\]]{2,40}\]|\{[^}]{2,40}\}|<[A-Za-z ]{2,30}>")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def reply_hash(thread_id: str, reply: str) -> str:
    return hashlib.sha256(
        f"{thread_id}\n{normalize(reply)}".encode("utf-8")
    ).hexdigest()


def validate_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in REPLY_MODES:
        raise ValueError("Reply mode must be manual or auto.")
    return mode


def validate_manual_reply(reply: str) -> str:
    reply = (reply or "").strip()
    if not reply:
        raise ValueError("Write or generate a reply before sending.")
    if len(reply) > MAX_MANUAL_REPLY_CHARS:
        raise ValueError(
            f"Replies must be {MAX_MANUAL_REPLY_CHARS:,} characters or fewer."
        )
    if _PLACEHOLDER_RE.search(reply):
        raise ValueError(
            "The reply still contains a placeholder such as [Your Name]. Edit it before sending."
        )
    return reply


def latest_message_is_ours(conversation: str, last_sent_reply: str | None) -> bool:
    """True when our last sent reply is still the newest text in the thread.

    LinkedIn's conversation text lists messages oldest first. If the reply we
    sent appears near the end with only short UI text after it (timestamps,
    "Seen", the composer label), nobody has written since, so there is
    nothing new to answer.
    """
    if not last_sent_reply:
        return False
    text = normalize(conversation)
    probe = normalize(last_sent_reply)[:80]
    if len(probe) < 10:
        return False
    position = text.rfind(probe)
    if position == -1:
        return False
    tail = text[position + len(normalize(last_sent_reply)) :]
    return len(tail) < 160


_YOU_LABEL_RE = re.compile(r"^you(?:\s*[·•|-].*|\s+\d{1,2}:\d{2}.*)?$")


def _usable_full_name(name: str) -> str:
    value = normalize(name)
    if len(value) < 5 or " " not in value or value == "linkedin contact":
        return ""
    return value


def last_speaker(
    conversation: str, participant: str, own_names: Sequence[str]
) -> str | None:
    """Return "them", "us", or None when the latest sender cannot be determined.

    LinkedIn's conversation text starts each message group with the sender's
    display name (or "You"). The last such label marks who wrote last. Only
    full names are matched, because a first name alone also appears inside
    messages ("Hi Jordan"). When the user's own name is unknown and the other
    person appears last, an unlabeled later reply from the user cannot be ruled
    out, so the answer is None.
    """
    them = _usable_full_name(participant)
    owns = [name for name in (_usable_full_name(item) for item in own_names) if name]
    last: str | None = None
    for line in conversation.splitlines():
        value = normalize(line)
        if not value or len(value) > 120:
            continue
        if _YOU_LABEL_RE.match(value) or any(
            value == own or value.startswith(own + " ") for own in owns
        ):
            last = "us"
        elif them and (value == them or value.startswith(them + " ")):
            last = "them"
    if last == "them" and not owns:
        return None
    return last


_ACKNOWLEDGEMENT_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|k|sure|thanks?(?: you)?(?: so much)?|thx|ty|noted|got it|great|cool|perfect|"
    r"will do|sounds good|talk (?:to you )?soon|see you|bye|cheers|welcome|you'?re welcome|no problem|"
    r"np|done|alright|good|nice|appreciate it)[\s!.,]*)+$|^[\W_]+$",
    re.IGNORECASE,
)


def inbound_needs_reply(inbound_text: str | None) -> bool:
    """True when the other person's latest messages call for an answer.

    Anything they wrote counts (a question, a request such as "please check
    and revert", or news) unless every line is a pure acknowledgement or
    sign-off like "Thanks!", "OK", or an emoji.
    """
    lines = [line.strip() for line in (inbound_text or "").splitlines() if line.strip()]
    if not lines:
        return False
    return not all(_ACKNOWLEDGEMENT_RE.match(line) for line in lines)


def inbound_asks_question(conversation: str, participant: str) -> bool:
    """True when the other person's latest message group contains a question."""
    them = _usable_full_name(participant)
    if not them:
        return False
    lines = conversation.splitlines()
    start = None
    for index, line in enumerate(lines):
        value = normalize(line)
        if value == them or value.startswith(them + " "):
            start = index
    if start is None:
        return False
    return "?" in "\n".join(lines[start + 1 :])


@dataclass(frozen=True, slots=True)
class AutoReplyDecision:
    action: str  # "send" | "hold" | "skip"
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.action == "send"


def evaluate_auto_reply(
    *,
    conversation: str,
    reply: str,
    needs_reply: bool,
    speaker: str | None,
    last_sent_reply: str | None = None,
    last_sent_at: datetime | None = None,
    now: datetime | None = None,
    cooldown: timedelta = DEFAULT_COOLDOWN,
    paused: bool = False,
    sent_today: int = 0,
    daily_limit: int = 20,
    inbound_text: str | None = None,
) -> AutoReplyDecision:
    """Decide whether a drafted reply may be sent automatically.

    ``speaker`` comes from :func:`last_speaker`: "us" means nothing new to
    answer, None means Aira cannot tell who wrote last and must not guess.
    """
    now = now or datetime.now(timezone.utc)
    if speaker == "us" or latest_message_is_ours(conversation, last_sent_reply):
        return AutoReplyDecision(
            "skip", ("Your last reply is still the newest message.",)
        )
    if not needs_reply:
        return AutoReplyDecision("skip", ("The latest message does not need a reply.",))
    if speaker is None:
        return AutoReplyDecision(
            "hold",
            (
                "Aira could not tell who wrote the latest message. Add your full name, exactly as LinkedIn shows it, in Settings > Profile.",
            ),
        )
    if paused:
        return AutoReplyDecision("hold", ("Automatic replies are paused in Settings.",))
    if daily_limit <= 0 or sent_today >= daily_limit:
        return AutoReplyDecision(
            "hold", (f"Daily automatic reply limit of {daily_limit} reached.",)
        )
    if last_sent_at is not None and now - last_sent_at < cooldown:
        hours = cooldown.total_seconds() / 3600
        return AutoReplyDecision(
            "hold",
            (
                f"Aira already replied in this conversation within the last {hours:g} hours.",
            ),
        )

    reasons: list[str] = []
    reply = (reply or "").strip()
    if len(reply) < MIN_REPLY_CHARS:
        reasons.append("The drafted reply is too short to send unattended.")
    if len(reply) > MAX_REPLY_CHARS:
        reasons.append("The drafted reply is too long to send unattended.")
    if _PLACEHOLDER_RE.search(reply):
        reasons.append("The draft contains a placeholder such as [Your Name].")
    if _URL_RE.search(reply):
        reasons.append("The draft contains a link.")
    if _EMAIL_RE.search(reply) or _PHONE_RE.search(reply):
        reasons.append("The draft shares contact details.")
    if _MONEY_RE.search(reply):
        reasons.append("The draft mentions a money amount.")
    # Only what the other person wrote since your last message is checked, so
    # your own earlier messages (for example your visa status) never trigger a hold.
    inbound_tail = inbound_text if inbound_text is not None else conversation[-1500:]
    for label, pattern in SENSITIVE_TOPICS:
        if pattern.search(inbound_tail) or pattern.search(reply):
            reasons.append(
                f"The conversation involves {label}, which needs your answer."
            )
    if reasons:
        return AutoReplyDecision("hold", tuple(dict.fromkeys(reasons)))
    return AutoReplyDecision("send", ())


# ---------------------------------------------------------------------------
# LinkedIn thread parsing
# ---------------------------------------------------------------------------
# LinkedIn's messaging page text contains the conversation list, the active
# thread, and composer controls. Inside the thread each message group starts
# with "<Name> sent the following message(s) at <time>" and each message with
# "<Name>   <time>". These English-locale markers turn the page text into a
# clean list of messages; when they are absent, callers fall back to the raw
# text and the line-based helpers above.

_GROUP_RE = re.compile(
    r"^(?P<name>.+?) sent the following messages? at (?P<time>\d{1,2}:\d{2}\s?[AP]M)$",
    re.IGNORECASE,
)
_MESSAGE_HEAD_RE = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?:\([^)]{1,30}\)\s+)?(?P<time>\d{1,2}:\d{2}\s?[AP]M)$",
    re.IGNORECASE,
)
_VIEW_PROFILE_RE = re.compile(r"^View .{1,80}[’']s profile$", re.IGNORECASE)
_DATE_RE = re.compile(
    r"^(TODAY|YESTERDAY|MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY|"
    r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]* \d{1,2}(, \d{4})?)$"
)
_THREAD_END_PREFIXES = (
    "reply to conversation with",
    "maximize compose field",
    "attach an image",
    "attach a file",
    "write a message",
    "open gif keyboard",
)
_SKIP_LINE_RE = re.compile(
    r"^(seen by .*|sent|delivered|status is .*|active now)$", re.IGNORECASE
)
_LABEL_PREFIX_RE = re.compile(r"^(open|view)\s+", re.IGNORECASE)
_LABEL_SUFFIX_RE = re.compile(r"[’']s profile$", re.IGNORECASE)


def clean_person_label(label: str) -> str:
    """Turn accessibility labels like "Open Sagar Reddy Kolli's profile" into a name."""
    value = re.sub(r"\s+", " ", label or "").strip()
    value = _LABEL_PREFIX_RE.sub("", value)
    value = _LABEL_SUFFIX_RE.sub("", value)
    return value.strip()


@dataclass(frozen=True, slots=True)
class ThreadMessage:
    sender: str
    time: str
    date: str
    text: str


def parse_thread(conversation: str) -> list[ThreadMessage]:
    """Extract the active thread's messages from LinkedIn messaging page text."""
    lines = (conversation or "").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if _GROUP_RE.match(line.strip())),
        None,
    )
    if start is None:
        return []
    messages: list[ThreadMessage] = []
    sender = time_label = date_label = ""
    # The date header of the first group sits just above its sender marker.
    for line in reversed(lines[max(0, start - 3) : start]):
        if _DATE_RE.match(line.strip()):
            date_label = line.strip().title()
            break
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if sender and text:
            messages.append(ThreadMessage(sender, time_label, date_label, text))
        body.clear()

    for raw in lines[start:]:
        line = raw.strip()
        lowered = line.casefold()
        if any(lowered.startswith(prefix) for prefix in _THREAD_END_PREFIXES):
            break
        group = _GROUP_RE.match(line)
        if group:
            flush()
            sender, time_label = group["name"].strip(), group["time"]
            continue
        head = _MESSAGE_HEAD_RE.match(line)
        if (
            head
            and sender
            and normalize(head["name"]).startswith(normalize(sender)[:12])
        ):
            flush()
            time_label = head["time"]
            continue
        if _DATE_RE.match(line):
            flush()
            date_label = line.title()
            continue
        if _VIEW_PROFILE_RE.match(line) or _SKIP_LINE_RE.match(line):
            continue
        body.append(line)
    flush()
    return messages


def _own_name_list(own_names: Sequence[str]) -> list[str]:
    return [name for name in (_usable_full_name(item) for item in own_names) if name]


def _is_own(sender: str, owns: Sequence[str]) -> bool:
    value = normalize(sender)
    return any(value == name or value.startswith(name + " ") for name in owns)


def thread_participant(
    messages: Sequence[ThreadMessage], own_names: Sequence[str], fallback: str = ""
) -> str:
    """The other person in the thread."""
    label = clean_person_label(fallback)
    for message in messages:
        if label and normalize(message.sender) == normalize(label):
            return message.sender
    owns = _own_name_list(own_names)
    if owns:
        for message in reversed(messages):
            if not _is_own(message.sender, owns):
                return message.sender
    return label


def _sent_by_us(sender: str, owns: Sequence[str], participant: str) -> bool:
    if owns:
        return _is_own(sender, owns)
    them = normalize(clean_person_label(participant))
    return bool(them) and normalize(sender) != them


def thread_speaker(
    messages: Sequence[ThreadMessage], participant: str, own_names: Sequence[str]
) -> str | None:
    """Who wrote the last message: "us", "them", or None when it cannot be told."""
    if not messages:
        return None
    owns = _own_name_list(own_names)
    if not owns and not clean_person_label(participant):
        return None
    return "us" if _sent_by_us(messages[-1].sender, owns, participant) else "them"


def latest_inbound_text(
    messages: Sequence[ThreadMessage], own_names: Sequence[str], participant: str
) -> str:
    """Text the other person sent after your most recent message."""
    owns = _own_name_list(own_names)
    collected: list[str] = []
    for message in reversed(messages):
        if _sent_by_us(message.sender, owns, participant):
            break
        collected.append(message.text)
    return "\n".join(reversed(collected))


def format_transcript(messages: Sequence[ThreadMessage], limit: int = 25) -> str:
    blocks = []
    for message in messages[-limit:]:
        when = " ".join(part for part in (message.date, message.time) if part)
        blocks.append(f"{message.sender} ({when}):\n{message.text}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Which conversations get automatic replies
# ---------------------------------------------------------------------------
AUTO_REPLY_SCOPES = ("selected", "recruiters", "all")

_STRONG_RECRUITER_TERMS = (
    ("recruiter", re.compile(r"\brecruit(?:er|ing|ment)\b", re.IGNORECASE)),
    (
        "talent acquisition",
        re.compile(r"\btalent (?:acquisition|partner)\b", re.IGNORECASE),
    ),
    ("hiring", re.compile(r"\bhiring\b", re.IGNORECASE)),
    ("staffing", re.compile(r"\bstaffing\b", re.IGNORECASE)),
    ("requirement", re.compile(r"\brequirements?\b", re.IGNORECASE)),
    ("resume", re.compile(r"\b(?:resume|cv)\b", re.IGNORECASE)),
    ("W2 or C2C", re.compile(r"\b(?:w-?2|c2c|corp to corp|1099)\b", re.IGNORECASE)),
    (
        "marketing your profile",
        re.compile(r"\bmarket(?:ing)? (?:your )?profile\b", re.IGNORECASE),
    ),
)
_WEAK_RECRUITER_TERMS = (
    ("job", re.compile(r"\bjobs?\b", re.IGNORECASE)),
    ("role", re.compile(r"\broles?\b", re.IGNORECASE)),
    ("position", re.compile(r"\bpositions?\b", re.IGNORECASE)),
    ("opportunity", re.compile(r"\bopportunit(?:y|ies)\b", re.IGNORECASE)),
    ("interview", re.compile(r"\binterview", re.IGNORECASE)),
    ("client", re.compile(r"\bclients?\b", re.IGNORECASE)),
    ("contract", re.compile(r"\bcontract\b", re.IGNORECASE)),
    (
        "work authorization",
        re.compile(r"\b(?:visa|work authori[sz]ation|h-?1b|ead)\b", re.IGNORECASE),
    ),
)


def recruiter_signal(text: str) -> tuple[bool, str]:
    """Decide whether the other person's messages read like recruiter outreach.

    One strong term (recruiter, hiring, staffing, resume, W2/C2C...) or two
    different weaker terms (job, role, position, opportunity...) are enough.
    Returns the decision and a short reason naming the terms found.
    """
    strong = [
        label
        for label, pattern in _STRONG_RECRUITER_TERMS
        if pattern.search(text or "")
    ]
    weak = [
        label for label, pattern in _WEAK_RECRUITER_TERMS if pattern.search(text or "")
    ]
    found = strong + weak
    if strong or len(weak) >= 2:
        return True, "Looks like recruiter outreach: mentions " + ", ".join(
            found[:5]
        ) + "."
    if weak:
        return False, "Not clearly recruiter outreach: only mentions " + ", ".join(
            weak
        ) + "."
    return False, "No recruiting terms in their messages."


def effective_mode(
    *, explicit_mode: str | None, scope: str, is_recruiter: bool, recruiter_reason: str
) -> tuple[str, str]:
    """The reply mode that applies to a conversation, and why."""
    if explicit_mode in REPLY_MODES:
        label = "Auto-reply" if explicit_mode == "auto" else "Manual send"
        return explicit_mode, f"{label}: you set this for this conversation."
    if scope == "all":
        return "auto", "Auto-reply: you chose automatic replies for every conversation."
    if scope == "recruiters":
        if is_recruiter:
            return (
                "auto",
                recruiter_reason + " Auto-reply covers recruiter conversations.",
            )
        return "manual", recruiter_reason + " Drafts only."
    return (
        "manual",
        "Manual send: automatic replies run only in conversations you switch on.",
    )
