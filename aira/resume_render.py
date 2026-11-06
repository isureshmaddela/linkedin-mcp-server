"""Formatted resumes and cover letters for employers.

The tailored resume Monosage writes (and the person edits) is text, often with Markdown-like marks. This module
turns it into a document an employer reads as a designed resume:

* :func:`parse_resume` finds the name and contact header, section headings (Markdown ``#``/``##``, ALL CAPS lines,
  setext underlines, bold-only lines and the common English heading words), role and company lines (bold, with
  dates set on the right), bullets and paragraphs, and inline ``**bold**`` and ``*italic*``.
* :func:`layout_resume` and :func:`layout_cover_letter` place every line on US Letter pages: margins, spacing,
  hanging bullets, a rule under each heading, and page breaks that keep a heading or a role line with what
  follows it (no heading is left alone at the foot of a page).
* :func:`render_pdf` writes those pages as a PDF with the standard Helvetica faces (regular, bold, oblique and
  bold oblique) in WinAnsi encoding, so ATS parsers read the text. :func:`preview_pages` returns the same
  positions as JSON, so the resume editor draws the exact pages before anything is uploaded.
* :func:`render_docx` puts the tailored content into a copy of the person's own Word resume, keeping its
  paragraph and character styles, when that resume is a simple one-column document (no tables, text boxes,
  content controls, pictures or columns). :func:`docx_template_status` says whether a file qualifies.

Characters the PDF fonts cannot show are transliterated (accents dropped, dashes, arrows and symbols to ASCII);
anything left is shown as "?" and named in ``warnings``. The Word path keeps every character.

Only the standard library is used.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from aira.pdf_text import _ASCII_WIDTHS, _HIGH_WIDTHS

__all__ = [
    "Block",
    "DocxTemplateError",
    "Layout",
    "ResumeDoc",
    "Run",
    "Section",
    "cover_letter_pdf",
    "docx_template_status",
    "heading_kind",
    "layout_cover_letter",
    "layout_resume",
    "parse_resume",
    "preview_pages",
    "render_docx",
    "render_pdf",
    "resume_pdf",
    "to_winansi",
]

# --------------------------------------------------------------------------- #
# Page geometry and type sizes (points)
# --------------------------------------------------------------------------- #
PAGE_WIDTH = 612.0  # US Letter
PAGE_HEIGHT = 792.0
MARGIN_X = 54.0  # 0.75 inch
MARGIN_TOP = 50.0
MARGIN_BOTTOM = 50.0
USABLE = PAGE_WIDTH - 2 * MARGIN_X
BODY_SIZE = 10.0
BULLET_INDENT = 18.0
MAX_PAGES = (
    12  # a resume or a letter never needs more; longer text is cut with a warning
)

REGULAR, BOLD, ITALIC, BOLD_ITALIC = "F1", "F2", "F3", "F4"
FONT_NAMES = {
    REGULAR: "Helvetica",
    BOLD: "Helvetica-Bold",
    ITALIC: "Helvetica-Oblique",
    BOLD_ITALIC: "Helvetica-BoldOblique",
}
_PREVIEW_FONT = {
    REGULAR: "regular",
    BOLD: "bold",
    ITALIC: "italic",
    BOLD_ITALIC: "bolditalic",
}

# Helvetica-Bold advance widths (per 1000 em) for ASCII 32..126, from the Adobe core-14 AFM.
_BOLD_ASCII: tuple[int, ...] = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
)  # fmt: skip
_BOLD_HIGH: dict[int, int] = {
    0x80: 556, 0x82: 278, 0x83: 556, 0x84: 500, 0x85: 1000, 0x86: 556, 0x87: 556,
    0x88: 333, 0x89: 1000, 0x8A: 667, 0x8B: 333, 0x8C: 1000, 0x8E: 611, 0x91: 278,
    0x92: 278, 0x93: 500, 0x94: 500, 0x95: 350, 0x96: 556, 0x97: 1000, 0x98: 333,
    0x99: 1000, 0x9A: 556, 0x9B: 333, 0x9C: 944, 0x9E: 500, 0x9F: 667, 0xA0: 278,
    0xA1: 333, 0xA6: 280, 0xA9: 737, 0xAA: 370, 0xAB: 556, 0xAC: 584, 0xAD: 333,
    0xAE: 737, 0xB0: 400, 0xB1: 584, 0xB5: 611, 0xB6: 556, 0xB7: 278, 0xBA: 365,
    0xBB: 556, 0xBC: 834, 0xBD: 834, 0xBE: 834, 0xBF: 611, 0xC6: 1000, 0xD7: 584,
    0xDF: 611, 0xE6: 889, 0xF0: 611, 0xF7: 584, 0xFE: 611,
}  # fmt: skip

# --------------------------------------------------------------------------- #
# Characters: WinAnsi transliteration
# --------------------------------------------------------------------------- #
_TRANSLITERATIONS: dict[str, str] = {
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ", "\u2002": " ", "\u2003": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "", "\u00ad": "",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2015": "-", "\u2212": "-", "\u2043": "-",
    "\u2032": "'", "\u2033": '"', "\u2035": "'", "\u02bc": "'", "\u02bb": "'",
    "\u2192": "->", "\u2190": "<-", "\u2194": "<->", "\u21d2": "=>", "\u27a4": ">", "\u279c": "->",
    "\u2264": "<=", "\u2265": ">=", "\u2260": "!=", "\u2248": "~", "\u221e": "infinity",
    "\u2713": "x", "\u2714": "x", "\u2717": "x", "\u2718": "x", "\u2605": "*", "\u2606": "*",
    "\u25cf": "\u2022", "\u25aa": "\u2022", "\u25ab": "\u2022", "\u25e6": "\u2022", "\u2219": "\u2022",
    "\u25a0": "\u2022", "\u25a1": "\u2022", "\u25c6": "\u2022", "\u25c7": "\u2022", "\u2023": "\u2022",
    "\u2027": "\u00b7", "\u22c5": "\u00b7",
    "\u20b9": "INR ", "\u20bd": "RUB ", "\u20a9": "KRW ", "\u20ba": "TRY ", "\u20a6": "NGN ", "\u20b1": "PHP ",
    "\u0141": "L", "\u0142": "l", "\u0110": "D", "\u0111": "d", "\u0131": "i", "\u0126": "H", "\u0127": "h",
    "\u0152": "\u0152", "\u0153": "\u0153", "\u00df": "\u00df", "\u1e9e": "SS",
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u2116": "No.", "\u2122": "\u2122", "\u2026": "\u2026",
}  # fmt: skip
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _encodable(char: str) -> bool:
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _winansi_char(char: str) -> str | None:
    """The WinAnsi text for one character, or ``None`` when nothing sensible stands in for it."""
    if char in _TRANSLITERATIONS:
        return _TRANSLITERATIONS[char]
    if _encodable(char):
        return char
    if unicodedata.category(char) == "Zs":
        return " "
    for form in ("NFKD", "NFKC"):
        decomposed = unicodedata.normalize(form, char)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        if base and base != char and all(_encodable(c) for c in base):
            return base
    return None


def to_winansi(text: str, lost: set[str] | None = None) -> str:
    """``text`` with every character the Helvetica WinAnsi fonts can show; others become "?" and go to ``lost``."""
    out: list[str] = []
    for char in _CONTROL_RE.sub("", str(text or "")):
        if char in "\r\n\t":
            out.append(" " if char == "\t" else char)
            continue
        mapped = _winansi_char(char)
        if mapped is None:
            if lost is not None:
                lost.add(char)
            out.append("?")
        else:
            out.append(mapped)
    return "".join(out)


def _char_width(code: int, font: str) -> int:
    bold = font in (BOLD, BOLD_ITALIC)
    if 32 <= code <= 126:
        return (_BOLD_ASCII if bold else _ASCII_WIDTHS)[code - 32]
    if bold:
        if code in _BOLD_HIGH:
            return _BOLD_HIGH[code]
        # Accented letters share their base letter's width.
        try:
            char = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            return 556
        base = unicodedata.normalize("NFKD", char)[:1]
        if base and 32 <= ord(base) <= 126:
            return _BOLD_ASCII[ord(base) - 32]
        return _HIGH_WIDTHS.get(code, 556)
    return _HIGH_WIDTHS.get(code, 556)


def text_width(text: str, font: str = REGULAR, size: float = BODY_SIZE) -> float:
    """Width in points of WinAnsi ``text`` set in ``font`` at ``size``."""
    data = text.encode("cp1252", errors="replace")
    return sum(_char_width(code, font) for code in data) * size / 1000.0


# --------------------------------------------------------------------------- #
# Parsing the tailored text
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False


@dataclass
class Block:
    """One piece of a section: ``role`` (bold title line, dates on the right), ``meta`` (the company or place line
    under a role, italic), ``bullet``, ``para`` or ``skill`` (a "Label: values" line)."""

    kind: str
    runs: list[Run]
    right: str = ""
    level: int = 0

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass
class Section:
    title: str
    kind: str = "other"
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ResumeDoc:
    name: str = ""
    headline: str = ""
    contact: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    def section_kinds(self) -> list[str]:
        return [section.kind for section in self.sections if section.title]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "headline": self.headline,
            "contact": list(self.contact),
            "sections": [
                {
                    "title": section.title,
                    "kind": section.kind,
                    "blocks": [
                        {
                            "kind": block.kind,
                            "text": block.text,
                            "right": block.right,
                            "level": block.level,
                        }
                        for block in section.blocks
                    ],
                }
                for section in self.sections
            ],
        }


_HEADING_WORDS: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary", "professional summary", "profile", "professional profile", "career summary", "objective",
        "career objective", "about me", "about", "overview", "executive summary", "personal statement",
        "summary of qualifications", "highlights", "career highlights",
    ),
    "experience": (
        "experience", "work experience", "professional experience", "employment", "employment history",
        "work history", "career history", "relevant experience", "professional background", "experience highlights",
        "selected experience", "industry experience",
    ),
    "education": (
        "education", "education and training", "academic background", "academic qualifications", "qualifications",
        "education and certifications", "academics",
    ),
    "skills": (
        "skills", "technical skills", "core skills", "key skills", "core competencies", "competencies",
        "areas of expertise", "expertise", "skills and tools", "technologies", "tools", "tech stack",
        "skills summary", "technical proficiencies", "skills and expertise", "technical expertise", "key competencies",
    ),
    "certifications": (
        "certifications", "certificates", "licenses", "licences", "licenses and certifications",
        "licences and certifications", "certifications and licenses", "accreditations", "credentials",
    ),
    "projects": ("projects", "selected projects", "key projects", "personal projects", "open source", "portfolio"),
    "achievements": (
        "achievements", "accomplishments", "awards", "honors", "honours", "awards and honors", "awards and honours",
        "key achievements", "recognition",
    ),
    "publications": ("publications", "patents", "talks", "presentations", "speaking", "research"),
    "languages": ("languages", "spoken languages", "language skills"),
    "volunteering": ("volunteering", "volunteer experience", "volunteer work", "community", "community involvement"),
    "interests": ("interests", "hobbies", "activities", "extracurricular activities", "hobbies and interests"),
    "references": ("references",),
    "training": ("training", "courses", "coursework", "professional development", "continuing education"),
    "leadership": ("leadership", "leadership experience"),
    "contact": ("contact", "contact information", "contact details", "personal details", "personal information"),
}  # fmt: skip
_HEADING_LOOKUP: dict[str, str] = {
    word: kind for kind, words in _HEADING_WORDS.items() for word in words
}
# Sections whose plain lines may be role, school or project lines (with dates on the right).
_ROLE_SECTIONS = frozenset(
    {
        "experience",
        "education",
        "projects",
        "volunteering",
        "leadership",
        "training",
        "certifications",
        "achievements",
        "publications",
        "other",
    }
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_RE = re.compile(r"^\s*(=+|-{3,})\s*$")
_RULE_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_BULLET_RE = re.compile(
    r"^(\s*)(?:[-*+\u2022\u00b7\u25aa\u25e6\u2023\u25cf\u25a0\u2013\u2014>]|\d{1,2}[.)])\s+(.*)$"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_URL_RE = re.compile(
    r"(?:https?://|www\.)\S+|\b(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com)/\S*",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s+[|\u2022\u00b7\u25e6\u2219]\s+|\s{3,}")
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DATE = rf"(?:{_MONTH}\s+(?:19|20)\d{{2}}|\d{{1,2}}/(?:19|20)\d{{2}}|(?:19|20)\d{{2}})"
_RANGE_RE = re.compile(
    rf"\(?\b{_DATE}\s*(?:-|\u2013|\u2014|to|until|through)\s*(?:{_DATE}|present|current|now|today|date)\b\)?",
    re.IGNORECASE,
)
_END_DATE_RE = re.compile(
    rf"(?:^|[\s|,(\u2013\u2014-])\(?((?:expected\s+|graduated\s+)?{_DATE})\)?\s*$",
    re.IGNORECASE,
)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_INLINE_RE = re.compile(
    r"(\*\*\*.+?\*\*\*|\*\*.+?\*\*|__.+?__|(?<![\w*])\*(?![\s*]).+?(?<![\s*])\*(?![\w*])|(?<![\w])_(?![\s_]).+?(?<![\s_])_(?![\w]))"
)
_LABEL_RE = re.compile(r"^([^:]{2,40}):\s+(\S.*)$")


def _heading_key(text: str) -> str:
    cleaned = re.sub(r"[*_#`]", "", text).strip().rstrip(":").strip()
    cleaned = cleaned.replace("&", " and ").replace("/", " and ")
    return re.sub(r"\s+", " ", cleaned).casefold()


def heading_kind(title: str) -> str | None:
    """The standard section a heading names ("experience", "skills", ...), or ``None`` for any other title."""
    return _HEADING_LOOKUP.get(_heading_key(title))


def _strip_marks(text: str) -> str:
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    return re.sub(r"\*\*|__|`", "", text).strip()


def inline_runs(text: str) -> list[Run]:
    """Split ``**bold**``, ``*italic*``, ``***both***`` and links into runs (the marks are removed)."""

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1).strip(), match.group(2).strip()
        bare = re.sub(r"^(?:https?://)?(?:www\.)?", "", url).rstrip("/")
        if label.casefold() in {url.casefold(), bare.casefold()}:
            return label
        return f"{label} ({bare})"

    text = _LINK_RE.sub(link, str(text)).replace("`", "")
    runs: list[Run] = []
    for index, part in enumerate(_INLINE_RE.split(text)):
        if not part:
            continue
        if index % 2:
            if part.startswith("***"):
                runs.append(Run(part[3:-3], bold=True, italic=True))
            elif part.startswith(("**", "__")):
                runs.append(Run(part[2:-2].replace("*", ""), bold=True))
            else:
                runs.append(Run(part[1:-1], italic=True))
        else:
            runs.append(Run(part.replace("**", "").replace("__", "")))
    merged: list[Run] = []
    for run in runs:
        if merged and (merged[-1].bold, merged[-1].italic) == (run.bold, run.italic):
            merged[-1] = Run(merged[-1].text + run.text, run.bold, run.italic)
        elif run.text:
            merged.append(run)
    return merged


def _is_contact(line: str) -> bool:
    return bool(
        _EMAIL_RE.search(line) or _URL_RE.search(line) or _PHONE_RE.search(line)
    )


def _contact_items(line: str) -> list[str]:
    plain = _strip_marks(line)
    plain = re.sub(
        r"^(?:email|e-mail|phone|mobile|tel|linkedin|github|web|website)\s*:\s*",
        "",
        plain,
        flags=re.IGNORECASE,
    )
    items = [item.strip(" ,;|") for item in _SEPARATOR_RE.split(plain)]
    if len(items) == 1 and "," in plain and _is_contact(plain):
        # "jane@example.com, +1 555 0100, Berlin" separates its parts with commas.
        parts = [part.strip() for part in plain.split(",")]
        if sum(1 for part in parts if _is_contact(part)) >= 1 and len(parts) <= 6:
            items = parts
    return [item for item in items if item]


def _caps_heading(line: str) -> bool:
    plain = _strip_marks(line).rstrip(":").strip()
    letters = [c for c in plain if c.isalpha()]
    return (
        3 <= len(plain) <= 40
        and len(letters) >= 3
        and plain.upper() == plain
        and len(plain.split()) <= 5
        and re.fullmatch(r"[A-Z\u00c0-\u00de &/,'-]+", plain) is not None
    )


def _bold_only(line: str) -> str | None:
    match = re.fullmatch(r"\s*(?:\*\*(.+?)\*\*|__(.+?)__)\s*:?\s*", line)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip()


def split_dates(line: str) -> tuple[str, str]:
    """``(text, dates)``: a date range (or a closing year) taken out of a role line, for the right-hand column."""
    matches = list(_RANGE_RE.finditer(line))
    if matches:
        found = matches[-1]
        left = line[: found.start()] + " " + line[found.end() :]
        right = found.group(0).strip(" ()")
    else:
        end = _END_DATE_RE.search(line)
        if not end or end.start(1) < 3:
            return line.strip(), ""
        left = line[: end.start(1)]
        right = end.group(1)
    left = re.sub(r"\(\s*\)", "", left)
    left = re.sub(r"\s*([|\u00b7\u2022])\s*(?:[|\u00b7\u2022]\s*)+", r" \1 ", left)
    left = re.sub(r"\s{2,}", " ", left).strip(" |,;\u00b7\u2022\u2013\u2014-(")
    right = re.sub(r"\s*(?:-|\u2013|\u2014)\s*", " \u2013 ", right)
    right = re.sub(r"\s+to\s+", " \u2013 ", right, flags=re.IGNORECASE)
    return left, right.strip()


def _next_content(lines: Sequence[str], start: int) -> str:
    for line in lines[start:]:
        if line.strip():
            return line
    return ""


def parse_resume(text: str) -> ResumeDoc:
    """Read a resume written as text (plain, or with Markdown-like marks) into its header and sections."""
    raw = _CONTROL_RE.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        line.rstrip().replace("\t", "    ")
        for line in raw.split("\n")
        if not line.strip().startswith("```")
    ]
    doc = ResumeDoc()
    section: Section | None = None
    previous_kind = ""  # the kind of the block made from the line just before (blank lines reset it)
    index = 0

    def open_section(title: str) -> None:
        nonlocal section, previous_kind
        title = _strip_marks(title).rstrip(":").strip()
        section = Section(title=title, kind=heading_kind(title) or "other")
        doc.sections.append(section)
        previous_kind = "heading"

    def current() -> Section:
        nonlocal section
        if section is None:
            section = Section(title="", kind="other")
            doc.sections.append(section)
        return section

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        following = lines[index + 1] if index + 1 < len(lines) else ""
        index += 1
        if not stripped:
            previous_kind = ""
            continue
        if _RULE_RE.fullmatch(stripped) and not _SETEXT_RE.fullmatch(stripped):
            continue
        if _SETEXT_RE.fullmatch(stripped):
            continue
        md = _MD_HEADING_RE.match(stripped)
        header_phase = section is None
        # -- headings ---------------------------------------------------------
        if md:
            level, title = len(md.group(1)), md.group(2).strip()
            if not title:
                continue
            if level == 1 and not doc.name and header_phase:
                doc.name = _strip_marks(title)
                previous_kind = "name"
                continue
            if level <= 2 or heading_kind(title):
                open_section(title)
                continue
            # ### Role lines inside a section
            left, right = split_dates(title)
            current().blocks.append(
                Block("role", [Run(_strip_marks(left), bold=True)], right=right)
            )
            previous_kind = "role"
            continue
        if (
            following.strip()
            and _SETEXT_RE.fullmatch(following.strip())
            and not _BULLET_RE.match(line)
        ):
            if header_phase and not doc.name and following.strip().startswith("="):
                doc.name = _strip_marks(stripped)
                previous_kind = "name"
            else:
                open_section(stripped)
            index += 1
            continue
        bold_title = _bold_only(stripped)
        is_known = heading_kind(stripped) is not None or (
            bold_title is not None and heading_kind(bold_title) is not None
        )
        bullet = _BULLET_RE.match(line)
        if not bullet and (
            is_known or (_caps_heading(stripped) and not _is_contact(stripped))
        ):
            if header_phase and not doc.name and not is_known:
                doc.name = _strip_marks(stripped)
                previous_kind = "name"
                continue
            open_section(bold_title or stripped)
            continue
        # -- the header (before the first heading) ----------------------------
        if header_phase:
            if (
                not doc.name
                and not _is_contact(stripped)
                and len(_strip_marks(stripped)) <= 60
                and len(stripped.split()) <= 7
            ):
                doc.name = _strip_marks(stripped).lstrip("#").strip()
                previous_kind = "name"
                continue
            if not doc.name and _is_contact(stripped):
                # "Jane Doe | jane@example.com | +1 555 0100": the name leads the contact line.
                items = _contact_items(stripped)
                if (
                    len(items) > 1
                    and not _is_contact(items[0])
                    and len(items[0].split()) <= 5
                ):
                    doc.name = items[0]
                    doc.contact.extend(items[1:])
                    previous_kind = "contact"
                    continue
            if _is_contact(stripped) or (
                previous_kind in {"name", "contact", "headline"}
                and _SEPARATOR_RE.search(_strip_marks(stripped))
                and len(stripped) <= 160
            ):
                doc.contact.extend(_contact_items(stripped))
                previous_kind = "contact"
                continue
            if (
                not doc.headline
                and previous_kind in {"name", "contact"}
                and len(stripped) <= 110
                and not stripped.endswith(".")
            ):
                doc.headline = _strip_marks(stripped)
                previous_kind = "headline"
                continue
        target = current()
        # -- bullets -----------------------------------------------------------
        if bullet:
            indent = len(bullet.group(1))
            body = bullet.group(2).strip()
            if target.kind == "skills" and _LABEL_RE.match(_strip_marks(body)):
                label, rest = _LABEL_RE.match(_strip_marks(body)).groups()  # type: ignore[union-attr]
                target.blocks.append(
                    Block("skill", [Run(label + ": ", bold=True), *inline_runs(rest)])
                )
                previous_kind = "skill"
                continue
            target.blocks.append(
                Block("bullet", inline_runs(body), level=1 if indent >= 2 else 0)
            )
            previous_kind = "bullet"
            continue
        # A wrapped bullet continues on an indented line.
        if previous_kind == "bullet" and line.startswith("  ") and target.blocks:
            last = target.blocks[-1]
            last.runs = [*last.runs, Run(" "), *inline_runs(stripped)]
            continue
        # -- skills lines ------------------------------------------------------
        if target.kind in {"skills", "languages", "interests"}:
            label_match = _LABEL_RE.match(_strip_marks(stripped))
            if label_match:
                label, rest = label_match.groups()
                target.blocks.append(
                    Block("skill", [Run(label + ": ", bold=True), *inline_runs(rest)])
                )
            else:
                target.blocks.append(Block("para", inline_runs(stripped)))
            previous_kind = "skill"
            continue
        # -- role, company and date lines ---------------------------------------
        if target.kind in _ROLE_SECTIONS:
            left, right = split_dates(stripped)
            last = target.blocks[-1] if target.blocks else None
            if last is not None and last.kind == "role" and previous_kind == "role":
                if right and not last.right:
                    last.right = right
                    if left:
                        target.blocks.append(
                            Block("meta", [Run(_strip_marks(left), italic=True)])
                        )
                        previous_kind = "meta"
                    continue
                if len(stripped) <= 110 and not stripped.endswith("."):
                    target.blocks.append(
                        Block(
                            "meta", [Run(_strip_marks(left), italic=True)], right=right
                        )
                    )
                    previous_kind = "meta"
                    continue
            starts_bold = stripped.startswith(("**", "__"))
            next_line = _next_content(lines, index)
            followed_by_bullet = bool(
                _BULLET_RE.match(next_line)
            ) and not _BULLET_RE.match(line)
            if (
                right
                or (starts_bold and len(stripped) <= 160)
                or (
                    followed_by_bullet
                    and len(stripped) <= 120
                    and not stripped.endswith((".", ":"))
                )
            ):
                runs = inline_runs(left)
                if not any(run.bold for run in runs):
                    runs = [Run(run.text, bold=True, italic=run.italic) for run in runs]
                target.blocks.append(Block("role", runs, right=right))
                previous_kind = "role"
                continue
        # -- paragraphs (consecutive lines join into one) -------------------------
        if (
            previous_kind == "para"
            and target.blocks
            and target.blocks[-1].kind == "para"
        ):
            last = target.blocks[-1]
            last.runs = [*last.runs, Run(" "), *inline_runs(stripped)]
        else:
            target.blocks.append(Block("para", inline_runs(stripped)))
        previous_kind = "para"
    doc.sections = [item for item in doc.sections if item.title or item.blocks]
    return doc


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
@dataclass
class _Line:
    height: float
    size: float
    ops: list[
        tuple[Any, ...]
    ]  # ("text", x, font, size, text, gray) | ("rule", x1, x2, dy, width, gray)
    space_before: float = 0.0
    keep_with_next: bool = False


@dataclass
class Layout:
    """Pages of positioned text and rules; ``y`` is the baseline from the bottom of the page (PDF space)."""

    pages: list[list[tuple[Any, ...]]]
    warnings: list[str]
    title: str = ""
    author: str = ""
    subject: str = ""

    @property
    def page_count(self) -> int:
        return len(self.pages)


class _Writer:
    """Turns blocks into lines, collecting the characters the fonts cannot show."""

    def __init__(self) -> None:
        self.lost: set[str] = set()
        self.lines: list[_Line] = []

    def text(self, value: str) -> str:
        return to_winansi(value, self.lost)

    def wrap(
        self, runs: Sequence[Run], width: float, size: float, *, base: str = REGULAR
    ) -> list[list[tuple[str, str]]]:
        """Greedy wrap of styled runs: a list of lines, each a list of ``(font, text)`` segments."""
        tokens: list[tuple[str, str, bool]] = []  # (font, word, space before)
        pending_space = False
        for run in runs:
            font = _font_for(run, base)
            pieces = re.split(r"(\s+)", self.text(run.text).replace("\n", " "))
            for piece in pieces:
                if not piece:
                    continue
                if piece.isspace():
                    pending_space = True
                    continue
                tokens.append((font, piece, pending_space and bool(tokens)))
                pending_space = False
        lines: list[list[tuple[str, str]]] = []
        current: list[tuple[str, str]] = []
        used = 0.0
        for font, word, spaced in tokens:
            gap = text_width(" ", font, size) if spaced and current else 0.0
            word_width = text_width(word, font, size)
            if current and used + gap + word_width > width:
                lines.append(current)
                current, used, gap = [], 0.0, 0.0
            if not current and word_width > width:
                chunk = ""
                for char in word:
                    if chunk and text_width(chunk + char, font, size) > width:
                        lines.append([(font, chunk)])
                        chunk = char
                    else:
                        chunk += char
                current, used = [(font, chunk)], text_width(chunk, font, size)
                continue
            if gap:
                if current and current[-1][0] == font:
                    current[-1] = (font, current[-1][1] + " " + word)
                else:
                    current.append((font, " " + word))
            elif current and current[-1][0] == font:
                current[-1] = (font, current[-1][1] + word)
            else:
                current.append((font, word))
            used += gap + word_width
        if current:
            lines.append(current)
        return lines

    def segments_ops(
        self,
        segments: Sequence[tuple[str, str]],
        x: float,
        size: float,
        gray: float = 0.0,
    ) -> list[tuple[Any, ...]]:
        ops: list[tuple[Any, ...]] = []
        for font, value in segments:
            ops.append(("text", x, font, size, value, gray))
            x += text_width(value, font, size)
        return ops

    def add(self, line: _Line) -> None:
        self.lines.append(line)

    def paragraph(
        self,
        runs: Sequence[Run],
        *,
        size: float = BODY_SIZE,
        leading: float = 13.5,
        x: float = MARGIN_X,
        width: float = USABLE,
        space_before: float = 3.0,
        base: str = REGULAR,
        align: str = "left",
        gray: float = 0.0,
        keep_last_with_next: bool = False,
    ) -> None:
        wrapped = self.wrap(runs, width, size, base=base)
        count = len(wrapped)
        for number, segments in enumerate(wrapped):
            line_width = sum(text_width(t, f, size) for f, t in segments)
            start = x
            if align == "center":
                start = x + (width - line_width) / 2
            elif align == "right":
                start = x + width - line_width
            # Orphans and widows: the first line stays with the second, the next-to-last with the last.
            keep = (count >= 2 and number == 0) or (count >= 3 and number == count - 2)
            if number == count - 1 and keep_last_with_next:
                keep = True
            self.add(
                _Line(
                    height=leading,
                    size=size,
                    ops=self.segments_ops(segments, start, size, gray),
                    space_before=space_before if number == 0 else 0.0,
                    keep_with_next=keep,
                )
            )


def _font_for(run: Run, base: str = REGULAR) -> str:
    bold = run.bold or base in (BOLD, BOLD_ITALIC)
    italic = run.italic or base in (ITALIC, BOLD_ITALIC)
    if bold and italic:
        return BOLD_ITALIC
    if bold:
        return BOLD
    if italic:
        return ITALIC
    return REGULAR


def _contact_from_profile(contact: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    if not contact:
        return "", []
    name = str(contact.get("name") or "").strip()
    items = [
        str(contact.get(key) or "").strip() for key in ("email", "phone", "location")
    ]
    links = contact.get("links") or []
    if isinstance(links, str):
        links = [links]
    for link in links:
        value = re.sub(r"^https?://(?:www\.)?", "", str(link or "").strip()).rstrip("/")
        if value:
            items.append(value)
    return name, [item for item in items if item]


def _header(writer: _Writer, name: str, headline: str, contact: Sequence[str]) -> None:
    if name:
        writer.paragraph(
            [Run(name, bold=True)], size=20, leading=24, space_before=0, align="center"
        )
    if headline:
        writer.paragraph(
            [Run(headline)],
            size=11,
            leading=14,
            space_before=1,
            align="center",
            gray=0.25,
        )
    if contact:
        joined = "  |  ".join(contact)
        writer.paragraph(
            [Run(joined)],
            size=9.5,
            leading=12.5,
            space_before=2,
            align="center",
            gray=0.2,
        )
    if name or contact:
        writer.add(
            _Line(
                height=8,
                size=1,
                ops=[("rule", MARGIN_X, PAGE_WIDTH - MARGIN_X, 5.0, 0.8, 0.2)],
                space_before=2,
            )
        )


def _section_lines(writer: _Writer, section: Section, first: bool) -> None:
    if section.title:
        title = writer.text(_strip_marks(section.title)).upper()
        writer.add(
            _Line(
                height=17,
                size=10.5,
                ops=[
                    ("text", MARGIN_X, BOLD, 10.5, title, 0.1),
                    ("rule", MARGIN_X, PAGE_WIDTH - MARGIN_X, -4.0, 0.5, 0.45),
                ],
                space_before=4 if first else 11,
                keep_with_next=True,
            )
        )
    previous = ""
    for block in section.blocks:
        if block.kind == "role":
            right = writer.text(block.right)
            right_width = text_width(right, REGULAR, 9.5) if right else 0.0
            width = USABLE - (right_width + 12 if right else 0)
            wrapped = writer.wrap(
                block.runs,
                width,
                10.5,
                base=BOLD if not any(r.bold for r in block.runs) else REGULAR,
            )
            for number, segments in enumerate(wrapped):
                ops = writer.segments_ops(segments, MARGIN_X, 10.5)
                if number == 0 and right:
                    ops.append(
                        (
                            "text",
                            PAGE_WIDTH - MARGIN_X - right_width,
                            REGULAR,
                            9.5,
                            right,
                            0.25,
                        )
                    )
                writer.add(
                    _Line(
                        height=13.5,
                        size=10.5,
                        ops=ops,
                        space_before=(7 if previous else 2) if number == 0 else 0,
                        keep_with_next=True,
                    )
                )
        elif block.kind == "meta":
            right = writer.text(block.right)
            right_width = text_width(right, ITALIC, 9.5) if right else 0.0
            wrapped = writer.wrap(
                block.runs,
                USABLE - (right_width + 12 if right else 0),
                9.5,
                base=ITALIC,
            )
            for number, segments in enumerate(wrapped):
                ops = writer.segments_ops(segments, MARGIN_X, 9.5, 0.2)
                if number == 0 and right:
                    ops.append(
                        (
                            "text",
                            PAGE_WIDTH - MARGIN_X - right_width,
                            ITALIC,
                            9.5,
                            right,
                            0.25,
                        )
                    )
                writer.add(_Line(height=12.5, size=9.5, ops=ops, keep_with_next=True))
        elif block.kind == "bullet":
            indent = BULLET_INDENT + (14 if block.level else 0)
            wrapped = writer.wrap(block.runs, USABLE - indent, BODY_SIZE)
            count = len(wrapped)
            for number, segments in enumerate(wrapped):
                ops = writer.segments_ops(segments, MARGIN_X + indent, BODY_SIZE)
                if number == 0:
                    ops.insert(
                        0,
                        (
                            "text",
                            MARGIN_X + indent - 11,
                            REGULAR,
                            BODY_SIZE,
                            "\u2022",
                            0.0,
                        ),
                    )
                keep = (count >= 2 and number == 0) or (
                    count >= 3 and number == count - 2
                )
                writer.add(
                    _Line(
                        height=13,
                        size=BODY_SIZE,
                        ops=ops,
                        space_before=(3 if previous in {"role", "meta"} else 1.5)
                        if number == 0
                        else 0,
                        keep_with_next=keep,
                    )
                )
        else:  # para, skill
            writer.paragraph(
                block.runs,
                space_before=(4 if previous in {"para", "bullet"} else 2)
                if block.kind == "para"
                else 1.5,
                leading=13.5 if block.kind == "para" else 13,
            )
        previous = block.kind


def _paginate(
    lines: Sequence[_Line], warnings: list[str]
) -> list[list[tuple[Any, ...]]]:
    top = PAGE_HEIGHT - MARGIN_TOP
    bottom = MARGIN_BOTTOM
    pages: list[list[tuple[Any, ...]]] = [[]]
    cursor = top
    empty = True
    index = 0
    while index < len(lines):
        line = lines[index]
        gap = 0.0 if empty else line.space_before
        needed = gap + line.height
        if line.keep_with_next and not empty:
            # The chain this line starts (up to the first line that may end a page) must fit together.
            chain = needed
            follow = index
            while (
                follow + 1 < len(lines)
                and lines[follow].keep_with_next
                and follow - index < 8
            ):
                follow += 1
                chain += lines[follow].space_before + lines[follow].height
            if cursor - chain < bottom and chain <= top - bottom:
                needed = float("inf")
        if not empty and cursor - needed < bottom:
            if len(pages) >= MAX_PAGES:
                warnings.append(
                    f"The document is longer than {MAX_PAGES} pages; the rest was left out. Shorten it."
                )
                break
            pages.append([])
            cursor, empty = top, True
            continue
        cursor -= gap
        baseline = (
            cursor - line.height + (line.height - line.size) / 2 + 0.22 * line.size
        )
        for op in line.ops:
            if op[0] == "text":
                _, x, font, size, value, gray = op
                pages[-1].append(
                    ("text", round(x, 2), round(baseline, 2), font, size, value, gray)
                )
            else:
                _, x1, x2, dy, width, gray = op
                pages[-1].append(
                    (
                        "rule",
                        round(x1, 2),
                        round(x2, 2),
                        round(baseline + dy, 2),
                        width,
                        gray,
                    )
                )
        cursor -= line.height
        empty = False
        index += 1
    return pages


def _lost_warning(lost: Iterable[str]) -> list[str]:
    chars = sorted(set(lost))
    if not chars:
        return []
    shown = " ".join(chars[:12]) + (" \u2026" if len(chars) > 12 else "")
    return [
        f"The PDF fonts cannot show {len(chars)} character{'s' if len(chars) != 1 else ''} ({shown}); "
        'they appear as "?". Replace them, or send your Word resume, which keeps every character.'
    ]


def layout_resume(
    text: str, *, contact: Mapping[str, Any] | None = None, title: str = ""
) -> Layout:
    """Lay out a tailored resume. ``contact`` (name, email, phone, location, links) fills a missing header."""
    doc = parse_resume(text)
    profile_name, profile_contact = _contact_from_profile(contact)
    name = doc.name or profile_name
    items = doc.contact or profile_contact
    writer = _Writer()
    _header(writer, writer.text(name), doc.headline, items)
    for number, section in enumerate(doc.sections):
        _section_lines(writer, section, number == 0)
    warnings: list[str] = []
    if not doc.sections:
        warnings.append("The resume is empty.")
    if not name:
        warnings.append(
            "No name was found at the top; add your name as the first line (or in your profile)."
        )
    pages = _paginate(writer.lines, warnings)
    warnings.extend(_lost_warning(writer.lost))
    return Layout(pages, warnings, title=title, author=name, subject="Resume")


_DEAR_RE = re.compile(r"^\s*(dear|hello|hi|to whom|greetings)\b", re.IGNORECASE)
_LETTER_DATE_RE = re.compile(
    rf"^\s*(?:{_MONTH}\s+\d{{1,2}},?\s+(?:19|20)\d{{2}}|\d{{1,2}}\s+{_MONTH}\s+(?:19|20)\d{{2}}|(?:19|20)\d{{2}}-\d{{2}}-\d{{2}})\s*$",
    re.IGNORECASE,
)


def _letter_paragraphs(
    text: str, contact_values: Sequence[str], name: str
) -> list[list[str]]:
    """The letter's paragraphs, without a header that repeats the contact block or a title line."""
    raw = _CONTROL_RE.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.split("\n") for block in re.split(r"\n\s*\n", raw.strip())]
    known = {value.casefold() for value in contact_values if value}
    if name:
        known.add(name.casefold())
    out: list[list[str]] = []
    greeted = False
    for block in blocks:
        kept: list[str] = []
        for line in block:
            stripped = _strip_marks(line.strip().lstrip("#").strip())
            if not stripped:
                continue
            if not greeted:
                if _DEAR_RE.match(stripped):
                    greeted = True
                else:
                    lowered = stripped.casefold()
                    if (
                        lowered in known
                        or any(
                            value and value in lowered
                            for value in known
                            if len(value) > 5
                        )
                        or _LETTER_DATE_RE.match(stripped)
                        or lowered in {"cover letter", "letter of application"}
                    ):
                        continue
            kept.append(stripped)
        if kept:
            out.append(kept)
    return out


def layout_cover_letter(
    text: str,
    *,
    contact: Mapping[str, Any] | None = None,
    title: str = "",
    today: date | None = None,
) -> Layout:
    """Lay out a cover letter under the same name and contact header as the resume, with the date."""
    name, items = _contact_from_profile(contact)
    writer = _Writer()
    _header(writer, writer.text(name), "", items)
    moment = today or date.today()
    writer.paragraph(
        [Run(f"{moment.day} {moment.strftime('%B %Y')}")],
        size=10.5,
        leading=15,
        space_before=14,
    )
    for number, lines in enumerate(_letter_paragraphs(text, items, name)):
        short = all(len(line) < 60 for line in lines)
        if len(lines) > 1 and short:
            for offset, line in enumerate(lines):
                writer.paragraph(
                    inline_runs(line),
                    size=10.5,
                    leading=15,
                    space_before=(14 if number == 0 else 9) if offset == 0 else 0,
                    keep_last_with_next=offset < len(lines) - 1,
                )
        else:
            writer.paragraph(
                inline_runs(" ".join(lines)),
                size=10.5,
                leading=15,
                space_before=14 if number == 0 else 9,
            )
    warnings: list[str] = []
    if not str(text or "").strip():
        warnings.append("The cover letter is empty.")
    pages = _paginate(writer.lines, warnings)
    warnings.extend(_lost_warning(writer.lost))
    return Layout(pages, warnings, title=title, author=name, subject="Cover letter")


# --------------------------------------------------------------------------- #
# PDF and preview output
# --------------------------------------------------------------------------- #
def _pdf_string(value: str) -> bytes:
    raw = value.encode("cp1252", errors="replace")
    raw = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + raw + b")"


def _page_stream(ops: Sequence[tuple[Any, ...]]) -> bytes:
    parts: list[bytes] = []
    for op in ops:
        if op[0] == "text":
            _, x, y, font, size, value, gray = op
            if not value:
                continue
            parts.append(
                f"BT /{font} {size:g} Tf {gray:g} g 1 0 0 1 {x:.2f} {y:.2f} Tm ".encode(
                    "ascii"
                )
                + _pdf_string(value)
                + b" Tj ET"
            )
        else:
            _, x1, x2, y, width, gray = op
            parts.append(
                f"q {width:g} w {gray:g} G {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S Q".encode(
                    "ascii"
                )
            )
    return b"\n".join(parts) + b"\n"


def render_pdf(layout: Layout) -> bytes:
    """A PDF 1.4 file of the layout: Helvetica faces in WinAnsi, one content stream per page, a document title."""
    pages = layout.pages or [[]]
    objects: list[bytes] = []
    font_ids = {font: 3 + number for number, font in enumerate(FONT_NAMES)}
    info_id = 3 + len(FONT_NAMES)
    first_page = info_id + 1
    page_ids = [first_page + 2 * number for number in range(len(pages))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii")
    )
    for font, base in FONT_NAMES.items():
        objects.append(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>".encode(
                "ascii"
            )
        )
    info = b"<< /Producer (Monosage Career Copilot)"
    for key, value in (
        ("Title", layout.title),
        ("Author", layout.author),
        ("Subject", layout.subject),
    ):
        cleaned = " ".join(to_winansi(value).split())[:200]
        if cleaned:
            info += f" /{key} ".encode("ascii") + _pdf_string(cleaned)
    objects.append(info + b" >>")
    fonts = " ".join(f"/{font} {font_ids[font]} 0 R" for font in FONT_NAMES)
    for page_id, ops in zip(page_ids, pages, strict=True):
        # Plain (uncompressed) streams: a resume is a few kilobytes, and simple ATS text readers cope better.
        stream = _page_stream(ops)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:g} {PAGE_HEIGHT:g}] "
            f"/Resources << /Font << {fonts} >> >> /Contents {page_id + 1} 0 R >>".encode(
                "ascii"
            )
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def preview_pages(layout: Layout) -> dict[str, Any]:
    """The layout as JSON for the editor: ``y`` counts from the top of the page, as SVG does."""
    pages: list[list[dict[str, Any]]] = []
    for ops in layout.pages or [[]]:
        items: list[dict[str, Any]] = []
        for op in ops:
            if op[0] == "text":
                _, x, y, font, size, value, gray = op
                items.append(
                    {
                        "t": "text",
                        "x": x,
                        "y": round(PAGE_HEIGHT - y, 2),
                        "font": _PREVIEW_FONT[font],
                        "size": size,
                        "text": value,
                        "gray": gray,
                    }
                )
            else:
                _, x1, x2, y, width, gray = op
                items.append(
                    {
                        "t": "rule",
                        "x1": x1,
                        "x2": x2,
                        "y": round(PAGE_HEIGHT - y, 2),
                        "w": width,
                        "gray": gray,
                    }
                )
        pages.append(items)
    return {
        "width": PAGE_WIDTH,
        "height": PAGE_HEIGHT,
        "pages": pages,
        "page_count": len(pages),
        "warnings": list(layout.warnings),
    }


def resume_pdf(
    text: str, *, contact: Mapping[str, Any] | None = None, title: str = ""
) -> tuple[bytes, Layout]:
    layout = layout_resume(text, contact=contact, title=title)
    return render_pdf(layout), layout


def cover_letter_pdf(
    text: str,
    *,
    contact: Mapping[str, Any] | None = None,
    title: str = "",
    today: date | None = None,
) -> tuple[bytes, Layout]:
    layout = layout_cover_letter(text, contact=contact, title=title, today=today)
    return render_pdf(layout), layout


# --------------------------------------------------------------------------- #
# Word: the tailored content in a copy of the person's own resume
# --------------------------------------------------------------------------- #
class DocxTemplateError(ValueError):
    """The Word file cannot carry the tailored content safely (the reason is the message)."""


_DOCX_LIMIT = 15 * 1024 * 1024
_PARA_RE = re.compile(r"<w:p\b[^>]*/>|<w:p\b[^>]*>.*?</w:p>", re.DOTALL)
_PPR_RE = re.compile(r"<w:pPr\b[^>]*>.*?</w:pPr>|<w:pPr\s*/>", re.DOTALL)
_RUN_RE = re.compile(r"<w:r\b[^>]*>.*?</w:r>", re.DOTALL)
_RPR_RE = re.compile(r"<w:rPr\b[^>]*>.*?</w:rPr>", re.DOTALL)
_RUN_TEXT_RE = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>|<w:tab\b[^>]*/>", re.DOTALL)
_FLAG_RE = re.compile(r"<w:(b|bCs|i|iCs)(?:\s+w:val=\"[^\"]*\")?\s*/>")
_LEAD_RE = re.compile(r"<w:rStyle\b[^>]*/>|<w:rFonts\b[^>]*/>")
_RSID_RE = re.compile(r"\s+w:rsid\w*=\"[^\"]*\"")
_UNSUPPORTED = (
    ("<w:tbl", "tables"),
    ("<w:txbxContent", "text boxes"),
    ("<mc:AlternateContent", "text boxes or shapes"),
    ("<w:sdt", "content controls"),
    ("<w:drawing", "pictures"),
    ("<w:pict", "pictures"),
    ("<w:object", "embedded objects"),
)
_DOCX_FALLBACK = "so Monosage sends its own PDF layout instead."


def _docx_xml(data: bytes) -> tuple[zipfile.ZipFile, str]:
    if not data or len(data) > _DOCX_LIMIT:
        raise DocxTemplateError("The Word file is empty or too large.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        info = archive.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as error:
        raise DocxTemplateError("The file is not a Word document (.docx).") from error
    if info.file_size > _DOCX_LIMIT:
        raise DocxTemplateError("The Word document is too large.")
    try:
        return archive, archive.read(info).decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocxTemplateError("The Word document could not be read.") from error


def _body_parts(xml: str) -> tuple[int, int, str, str]:
    """``(start, end, content, final sectPr)`` of the document body."""
    opening = re.search(r"<w:body\b[^>]*>", xml)
    closing = xml.rfind("</w:body>")
    if not opening or closing < 0:
        raise DocxTemplateError("The Word document has no body.")
    content = xml[opening.end() : closing]
    sect = ""
    last = content.rfind("<w:sectPr")
    # The body's own section properties close the body; one inside a paragraph is a section break.
    if last >= 0 and "</w:p>" not in content[last:]:
        tail = content[last:].strip()
        if tail.endswith("</w:sectPr>") or re.fullmatch(r"<w:sectPr\b[^>]*/>", tail):
            sect = tail
    return opening.end(), closing, content, sect


def _xml_text(fragment: str) -> str:
    texts: list[str] = []
    for run in _RUN_RE.findall(fragment):
        for match in _RUN_TEXT_RE.finditer(run):
            if match.group(0).startswith("<w:tab"):
                texts.append("\t")
            else:
                texts.append(match.group(1) or "")
    raw = "".join(texts)
    for entity, char in (
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&apos;", "'"),
        ("&amp;", "&"),
    ):
        raw = raw.replace(entity, char)
    return raw


@dataclass
class _Exemplar:
    ppr: str
    rpr: str
    prefix: str = ""


def _flag_on(rpr: str, name: str) -> bool:
    for match in _FLAG_RE.finditer(rpr):
        if match.group(1) == name:
            value = re.search(r"w:val=\"([^\"]*)\"", match.group(0))
            return (value.group(1) if value else "1").lower() not in {
                "0",
                "false",
                "off",
            }
    return False


def _clean_ppr(ppr: str) -> str:
    ppr = re.sub(
        r"<w:sectPr\b.*?</w:sectPr>|<w:sectPr\b[^>]*/>", "", ppr, flags=re.DOTALL
    )
    return _RSID_RE.sub("", ppr)


def _first_text_rpr(paragraph: str) -> str:
    for run in _RUN_RE.findall(paragraph):
        if re.search(r"<w:t\b[^>]*>[^<]*\S", run):
            found = _RPR_RE.search(run)
            return _RSID_RE.sub("", found.group(0)) if found else ""
    return ""


def _template_exemplars(content: str) -> dict[str, _Exemplar]:
    """The template's own paragraphs to copy for each kind: name, contact, heading, role, meta, bullet, body."""
    found: dict[str, _Exemplar] = {}
    seen_heading = False
    previous = ""
    for paragraph in _PARA_RE.findall(content):
        text = _xml_text(paragraph).strip()
        if not text:
            continue
        ppr_match = _PPR_RE.search(paragraph)
        ppr = _clean_ppr(ppr_match.group(0)) if ppr_match else ""
        rpr = _first_text_rpr(paragraph)
        style = re.search(r"<w:pStyle\s+w:val=\"([^\"]+)\"", ppr)
        style_name = (style.group(1) if style else "").casefold()
        bullet_char = re.match(r"^([\u2022\u00b7\u25aa\u25e6\u25cf\u2013*-])\s*", text)
        if "name" not in found:
            kind = "name"
        elif heading_kind(text) or (
            style_name.startswith("heading") and len(text) <= 60
        ):
            kind = "heading"
            seen_heading = True
        elif not seen_heading:
            kind = "contact"
        elif "<w:numPr>" in ppr or "list" in style_name or bullet_char:
            kind = "bullet"
        elif _flag_on(rpr, "b") and len(text) <= 160:
            kind = "role"
        elif previous == "role" and len(text) <= 110:
            kind = "meta"
        else:
            kind = "body"
        prefix = ""
        if kind == "bullet" and bullet_char and "<w:numPr>" not in ppr:
            prefix = bullet_char.group(1) + "\t"
        found.setdefault(kind, _Exemplar(ppr, rpr, prefix))
        previous = kind
    return found


def docx_template_status(data: bytes) -> tuple[bool, str]:
    """Whether a Word resume can carry tailored content in its own styles: ``(ok, reason)``."""
    try:
        _archive, xml = _docx_xml(data)
        _start, _end, content, sect = _body_parts(xml)
    except DocxTemplateError as error:
        return False, str(error)
    for marker, label in _UNSUPPORTED:
        if marker in content:
            return False, f"Your Word resume uses {label}, {_DOCX_FALLBACK}"
    if re.search(r"<w:cols\b[^>]*w:num=\"([2-9])\"", sect + content):
        return False, f"Your Word resume uses columns, {_DOCX_FALLBACK}"
    exemplars = _template_exemplars(content)
    if "heading" not in exemplars or not ({"body", "bullet"} & set(exemplars)):
        return (
            False,
            f"Monosage could not find section headings and body text in your Word resume, {_DOCX_FALLBACK}",
        )
    return True, ""


def _styled_rpr(rpr: str, bold: bool, italic: bool) -> str:
    """The template's run properties with bold or italic added (never removed), in schema order."""
    match = re.match(r"<w:rPr\b[^>]*>(.*)</w:rPr>$", rpr, re.DOTALL)
    body = match.group(1) if match else ""
    flags = {found.group(1): found.group(0) for found in _FLAG_RE.finditer(body)}
    body = _FLAG_RE.sub("", body)
    lead = "".join(_LEAD_RE.findall(body))
    rest = _LEAD_RE.sub("", body)
    if bold:
        flags.update(b="<w:b/>", bCs="<w:bCs/>")
    if italic:
        flags.update(i="<w:i/>", iCs="<w:iCs/>")
    middle = "".join(flags[key] for key in ("b", "bCs", "i", "iCs") if key in flags)
    body = lead + middle + rest
    return f"<w:rPr>{body}</w:rPr>" if body else ""


def _docx_paragraph(exemplar: _Exemplar, runs: Sequence[Run]) -> str:
    out: list[str] = []
    for run in runs:
        if not run.text:
            continue
        props = _styled_rpr(exemplar.rpr, run.bold, run.italic)
        for number, part in enumerate(run.text.split("\t")):
            if number:
                out.append(f"<w:r>{props}<w:tab/></w:r>")
            if part:
                out.append(
                    f'<w:r>{props}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>'
                )
    return f"<w:p>{exemplar.ppr}{''.join(out)}</w:p>"


def render_docx(
    template: bytes, text: str, *, contact: Mapping[str, Any] | None = None
) -> bytes:
    """A copy of the Word resume ``template`` whose body is the tailored ``text``, in the template's own styles.

    The page setup, headers, footers, numbering and styles stay; the body paragraphs are rebuilt from the parsed
    text using the template's own name, heading, role, bullet and body paragraphs as patterns. Raises
    :class:`DocxTemplateError` when the template is not a simple one-column document.
    """
    ok, reason = docx_template_status(template)
    if not ok:
        raise DocxTemplateError(reason)
    archive, xml = _docx_xml(template)
    start, end, content, sect = _body_parts(xml)
    patterns = _template_exemplars(content)
    body = patterns.get("body") or patterns["bullet"]
    heading = patterns["heading"]
    name_pattern = patterns.get("name") or heading
    contact_pattern = patterns.get("contact") or body
    role = patterns.get("role") or body
    meta = patterns.get("meta") or body
    bullet = patterns.get("bullet") or _Exemplar(body.ppr, body.rpr, "\u2022\t")

    doc = parse_resume(text)
    profile_name, profile_contact = _contact_from_profile(contact)
    name = doc.name or profile_name
    items = doc.contact or profile_contact
    header_text = " ".join(
        _xml_text(archive.read(item).decode("utf-8", errors="replace"))
        for item in archive.namelist()
        if re.fullmatch(r"word/header\d*\.xml", item)
    ).casefold()
    paragraphs: list[str] = []
    # A template whose page header already carries the name keeps it there.
    if name and name.casefold() not in header_text:
        paragraphs.append(_docx_paragraph(name_pattern, [Run(name)]))
        if doc.headline:
            paragraphs.append(_docx_paragraph(contact_pattern, [Run(doc.headline)]))
        if items:
            paragraphs.append(
                _docx_paragraph(contact_pattern, [Run("  |  ".join(items))])
            )
    right_tab = re.search(r"<w:tab\s+w:val=\"right\"", role.ppr) is not None
    for section in doc.sections:
        if section.title:
            paragraphs.append(_docx_paragraph(heading, [Run(section.title)]))
        for block in section.blocks:
            tail = (
                [Run(("\t" if right_tab else "  |  ") + block.right)]
                if block.right
                else []
            )
            if block.kind == "role":
                paragraphs.append(_docx_paragraph(role, [*block.runs, *tail]))
            elif block.kind == "meta":
                paragraphs.append(_docx_paragraph(meta, [*block.runs, *tail]))
            elif block.kind == "bullet":
                lead = [Run(bullet.prefix)] if bullet.prefix else []
                paragraphs.append(_docx_paragraph(bullet, [*lead, *block.runs]))
            else:
                paragraphs.append(_docx_paragraph(body, block.runs))
    new_xml = xml[:start] + "".join(paragraphs) + sect + xml[end:]
    try:
        ElementTree.fromstring(new_xml.encode("utf-8"))
    except (
        ElementTree.ParseError
    ) as error:  # pragma: no cover - a guard; the pieces come from the template
        raise DocxTemplateError(
            "Monosage could not build a valid Word document from your template."
        ) from error
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in archive.infolist():
            data = (
                new_xml.encode("utf-8")
                if info.filename == "word/document.xml"
                else archive.read(info)
            )
            target.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()
