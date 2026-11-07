"""Automatic moderation for words people write (Social, Dating, Videos comments, AI answers for young people).

This is a small, local first line of defence, not a complete one. It knows:

* **Categorised term lists** (:data:`TERMS`): sexual content, self-harm, violence, hate and grooming phrases, plus
  the swear words that were the old word filter. A term is matched as whole words, ignoring case, simple letter
  swaps (``s3x``, ``k1ll``) and stretched letters (``fuuuck``).
* **Contact details and moves off the platform** (:func:`contact_moves`): phone numbers, email addresses, links,
  "add me on ...", another app's name, asking to meet, asking where someone lives, asking for photos, asking to
  keep a secret from parents.
* **Self-harm intent** (:func:`self_harm_intent`): someone saying they want to hurt or kill themselves. Callers
  answer with :data:`CRISIS_REPLY`, which has helplines.

:func:`check` decides what happens to one piece of writing: ``allow``, ``mask`` (swear words starred out, as
before) or ``hold`` (kept back for a moderator, never silently delivered). Writing that involves a child or a
teen is held for any sexual, self-harm, violence, hate or grooming term and for any contact detail or move off the
platform. Grown-ups' writing is only masked.

**Images are not scanned here.** Spotting nudity or known child sexual abuse material (CSAM) needs an outside
service (for example Microsoft PhotoDNA, Thorn Safer or Google's Content Safety API) and, for CSAM, a reporting
duty to the authorities. :class:`ImageScanner` is the interface such a service plugs into
(:func:`set_image_scanner`); until one is set, :func:`scan_image` answers ``unscanned`` and uploads go ahead, so the
platform must not be opened to the public with young people's photos before a scanner is in place.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------------------
# term lists (lower case; a phrase is several words)
# ---------------------------------------------------------------------------

PROFANITY = ("fuck", "fucking", "shit", "bitch", "bastard", "cunt", "motherfucker")

TERMS: dict[str, tuple[str, ...]] = {
    "sexual": (
        "sex",
        "sexy",
        "nude",
        "nudes",
        "naked",
        "porn",
        "porno",
        "horny",
        "boobs",
        "dick",
        "pussy",
        "blowjob",
        "handjob",
        "orgasm",
        "masturbate",
        "send nudes",
        "take your clothes off",
        "sexting",
        "onlyfans",
    ),
    "self_harm": (
        "kill myself",
        "killing myself",
        "suicide",
        "suicidal",
        "self harm",
        "self-harm",
        "cut myself",
        "cutting myself",
        "hurt myself",
        "end my life",
        "want to die",
        "wanna die",
        "better off dead",
        "no reason to live",
        "overdose",
    ),
    "violence": (
        "kill you",
        "i will kill",
        "shoot you",
        "stab you",
        "beat you up",
        "hurt you",
        "bomb",
        "behead",
        "murder",
        "gun to school",
    ),
    "hate": (
        "nigger",
        "nigga",
        "faggot",
        "fag",
        "retard",
        "tranny",
        "chink",
        "paki",
        "kike",
        "spic",
        "gas the",
        "go back to your country",
        "subhuman",
    ),
    "grooming": (
        "our secret",
        "our little secret",
        "don't tell your parents",
        "dont tell your parents",
        "don't tell your mom",
        "dont tell your mom",
        "don't tell your mum",
        "don't tell your dad",
        "dont tell your dad",
        "don't tell anyone",
        "dont tell anyone",
        "are you alone",
        "are your parents home",
        "you are mature for your age",
        "you're mature for your age",
        "delete this chat",
        "delete our messages",
        "i can buy you",
        "send me a pic",
        "send me a photo",
        "send a pic",
        "send a photo",
        "show me your body",
    ),
    "profanity": PROFANITY,
}
HOLD_FOR_YOUNG = ("sexual", "self_harm", "violence", "hate", "grooming")
CATEGORY_TEXT = {
    "sexual": "sexual content",
    "self_harm": "self-harm",
    "violence": "violence",
    "hate": "hate",
    "grooming": "grooming",
    "profanity": "swearing",
    "contact": "contact details or a move to another app",
}

# ---------------------------------------------------------------------------
# contact details and moves off the platform
# ---------------------------------------------------------------------------

_PHONE = re.compile(
    r"(?<![\w])(?:\+?\d[\s().-]*){9,}\d(?![\w])"
)  # ten digits or more: never a date
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LINK = re.compile(
    r"\b(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|in|me|io|gg|ly|app)(?:/\S*)?\b",
    re.I,
)
_APPS = (
    "whatsapp",
    "snapchat",
    "snap",
    "telegram",
    "instagram",
    "insta",
    "discord",
    "kik",
    "signal",
    "wechat",
    "tiktok",
    "facebook",
    "messenger",
    "skype",
    "zoom",
    "omegle",
    "wickr",
    "line app",
)
_MOVES = (
    r"\badd me\b",
    r"\bdm me\b",
    r"\bmessage me on\b",
    r"\btext me\b",
    r"\bcall me\b",
    r"\bmy number\b",
    r"\byour number\b",
    r"\bphone number\b",
    r"\bmeet (?:up|me|you|in person|irl)\b",
    r"\blet'?s meet\b",
    r"\bcan we meet\b",
    r"\bcome over\b",
    r"\bwhere do you live\b",
    r"\bwhat'?s your address\b",
    r"\byour address\b",
    r"\bwhich school\b",
    r"\bwhat school\b",
    r"\bpick you up\b",
)
_MOVE_RE = [re.compile(pattern, re.I) for pattern in _MOVES]
_APP_RE = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in _APPS) + r")\b", re.I)

_LEET = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
    }
)
_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {}


def _normal(text: str) -> str:
    """Lower case, accents off, simple letter swaps undone, letters stretched three times or more cut to one."""
    plain = (
        unicodedata.normalize("NFKD", str(text or ""))
        .encode("ascii", "ignore")
        .decode()
    )
    plain = plain.lower().replace("’", "'")
    swapped = re.sub(
        r"(?<=[a-z])[013457@$!]+(?=[a-z])|[013457@$!]+(?=[a-z]{2})",
        lambda m: m.group(0).translate(_LEET),
        plain,
    )
    return re.sub(r"([a-z])\1{2,}", r"\1", swapped)


def _compiled(
    category: str, extra: Iterable[str] = ()
) -> list[tuple[str, re.Pattern[str]]]:
    terms = (*TERMS.get(category, ()), *extra)
    key = category if not extra else ""
    if key and key in _PATTERNS:
        return _PATTERNS[key]
    out = [
        (
            term,
            re.compile(
                r"(?<![a-z])"
                + r"\s+".join(re.escape(w) for w in term.split())
                + r"(?![a-z])"
            ),
        )
        for term in terms
        if term
    ]
    if key:
        _PATTERNS[key] = out
    return out


@dataclass
class Finding:
    category: str
    term: str


@dataclass
class Verdict:
    """What :func:`check` decided: ``allow``, ``mask`` (``text`` has swear words starred out) or ``hold``."""

    action: str
    text: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        return sorted({f.category for f in self.findings})

    @property
    def reason(self) -> str:
        names = [CATEGORY_TEXT.get(c, c) for c in self.categories if c != "profanity"]
        return "Held for review: " + (", ".join(names) or "flagged words") + "."


def scan(text: str, *, extra_words: Iterable[str] = ()) -> list[Finding]:
    """Every listed term in ``text`` (``extra_words``: a moderator's own words, counted as swearing)."""
    normal = _normal(text)
    found: list[Finding] = []
    for category in TERMS:
        for term, pattern in _compiled(category):
            if pattern.search(normal):
                found.append(Finding(category, term))
    for term, pattern in _compiled("_extra", [w.lower() for w in extra_words if w]):
        if pattern.search(normal):
            found.append(Finding("profanity", term))
    return found


def contact_moves(text: str) -> list[Finding]:
    """Phone numbers, email addresses, links, other apps and asks to meet or move elsewhere."""
    raw = str(text or "")
    normal = _normal(raw)
    found: list[Finding] = []
    if _PHONE.search(raw):
        found.append(Finding("contact", "a phone number"))
    if _EMAIL.search(raw):
        found.append(Finding("contact", "an email address"))
    if _LINK.search(raw):
        found.append(Finding("contact", "a link"))
    hit = _APP_RE.search(normal)
    if hit:
        found.append(Finding("contact", hit.group(0)))
    for pattern in _MOVE_RE:
        hit = pattern.search(normal)
        if hit:
            found.append(Finding("contact", hit.group(0)))
    return found


def self_harm_intent(text: str) -> bool:
    return any(f.category == "self_harm" for f in scan(text))


def mask(text: str, *, extra_words: Iterable[str] = ()) -> str:
    """The text with swear words (and a moderator's own words) starred out, everything else as written."""
    out = str(text or "")
    terms: set[str] = {*PROFANITY, *[str(w).lower() for w in extra_words if w]}
    for term in sorted(terms, key=lambda t: len(t), reverse=True):
        out = _loose(term).sub(lambda hit: "*" * len(hit.group(0)), out)
    return out


_SWAPS = {"o": "o0", "i": "i1!", "e": "e3", "a": "a4@", "s": "s5$", "t": "t7"}


def _loose(term: str) -> re.Pattern[str]:
    """A word as people dodge a filter: any case, letters stretched (fuuuck), simple swaps (sh!t, $hit)."""
    parts = []
    for letter in term:
        if letter.isspace():
            parts.append(r"\s+")
        elif letter.isalpha():
            parts.append(f"[{re.escape(_SWAPS.get(letter, letter))}]+")
        else:
            parts.append(re.escape(letter))
    return re.compile(r"(?i)(?<![\w$@!])" + "".join(parts) + r"(?![\w])")


def check(
    text: str, *, young: bool = False, extra_words: Iterable[str] = ()
) -> Verdict:
    """Decide about one piece of writing. ``young``: a child or a teen wrote it or will read it."""
    findings = scan(text, extra_words=extra_words)
    if young:
        findings += contact_moves(text)
        if any(f.category in (*HOLD_FOR_YOUNG, "contact") for f in findings):
            return Verdict("hold", str(text or ""), findings)
    masked = mask(text, extra_words=extra_words)
    return Verdict("mask" if masked != text else "allow", masked, findings)


def safe_for_young(text: str) -> bool:
    """No sexual, self-harm, violence, hate or grooming term (for writing shown to a child or a teen)."""
    return not any(f.category in HOLD_FOR_YOUNG for f in scan(text))


# ---------------------------------------------------------------------------
# what to say to someone in crisis
# ---------------------------------------------------------------------------

CRISIS_REPLY = (
    "I'm really sorry you're feeling this way. You don't have to go through it alone, and talking to someone can "
    "help right now.\n\n"
    "- In India, call Tele-MANAS free, any time: 14416 or 1-800-891-4416.\n"
    "- Anywhere else, contact your local emergency services, or a local crisis line.\n"
    "- If you might act on these thoughts, or you are in danger now, call emergency services or go to the nearest "
    "hospital.\n\n"
    "If you can, tell someone you trust, like a family member, a friend or a teacher, how you feel."
)
CRISIS_REPLY_YOUNG = (
    "I'm really sorry you're feeling this way. Please tell a grown-up you trust right now, like a parent, a "
    "teacher or another family member. You don't have to deal with this alone.\n\n"
    "- In India, call Tele-MANAS free, any time: 14416 or 1-800-891-4416.\n"
    "- Anywhere else, contact your local emergency services.\n"
    "- If you are in danger now, call emergency services."
)


def crisis_reply(young: bool = False) -> str:
    return CRISIS_REPLY_YOUNG if young else CRISIS_REPLY


# ---------------------------------------------------------------------------
# images: an outside service plugs in here
# ---------------------------------------------------------------------------


@dataclass
class ImageVerdict:
    """``ok``, ``block`` (never store or show it) or ``unscanned`` (no scanner is set up)."""

    status: str
    reason: str = ""


class ImageScanner(Protocol):
    def scan(self, data: bytes, mime: str, *, young: bool) -> ImageVerdict:
        """Look at one uploaded picture: nudity for young people's uploads, known CSAM hashes for every upload."""
        ...


class _NoScanner:
    def scan(self, data: bytes, mime: str, *, young: bool) -> ImageVerdict:
        return ImageVerdict("unscanned", "No image scanning service is set up.")


_scanner: ImageScanner = _NoScanner()


def set_image_scanner(scanner: ImageScanner | None) -> None:
    """Install the outside image scanning service (None: back to none)."""
    global _scanner
    _scanner = scanner or _NoScanner()


def scan_image(data: bytes, mime: str, *, young: bool = False) -> ImageVerdict:
    try:
        return _scanner.scan(data, mime, young=young)
    except Exception:  # noqa: BLE001 - a scanner that fails must not let a picture through as "ok"
        return ImageVerdict("unscanned", "The image scanning service did not answer.")
