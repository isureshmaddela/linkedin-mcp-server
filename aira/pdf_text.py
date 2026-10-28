"""Dependency-free plain-text to PDF conversion for application uploads.

ATS forms want a file, Aira often only has text (a tailored resume draft or a
cover letter). ``text_to_pdf`` writes a minimal but fully valid PDF 1.4 file:
US Letter pages, Helvetica 11 pt, 1 inch margins, lines wrapped on real
Helvetica glyph widths, as many pages as the text needs, WinAnsi encoding so
accented Latin characters survive, and a correct cross-reference table so
Chromium, Acrobat and ATS resume parsers all accept it.

Only the standard library is used; the Helvetica width table below comes from
the Adobe core-14 AFM metrics and covers WinAnsi (cp1252) code points.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

__all__ = [
    "FONT_SIZE",
    "LEADING",
    "MARGIN",
    "PAGE_HEIGHT",
    "PAGE_WIDTH",
    "text_to_pdf",
    "text_width",
    "wrap_text",
]

PAGE_WIDTH = 612.0  # US Letter, points
PAGE_HEIGHT = 792.0
MARGIN = 72.0
FONT_SIZE = 11.0
LEADING = 14.0
LINES_PER_PAGE = int((PAGE_HEIGHT - 2 * MARGIN) // LEADING)  # 46
USABLE_WIDTH = PAGE_WIDTH - 2 * MARGIN  # 468 pt
TAB_WIDTH = 4
_ENCODING = "cp1252"

# Helvetica advance widths (per 1000 em) for WinAnsi code points, from the Adobe
# core-14 AFM. Code points without an entry (control range, undefined cp1252
# slots) fall back to ``_DEFAULT_WIDTH``.
_ASCII_WIDTHS: tuple[int, ...] = (
    # 32..47:  space ! " # $ % & ' ( ) * + , - . /
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    # 48..63:  0-9 : ; < = > ?
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    # 64..79:  @ A-O
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    # 80..95:  P-Z [ \ ] ^ _
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    # 96..111: ` a-o
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    # 112..126: p-z { | } ~
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
)  # fmt: skip
_HIGH_WIDTHS: dict[int, int] = {
    0x80: 556, 0x82: 222, 0x83: 556, 0x84: 333, 0x85: 1000, 0x86: 556, 0x87: 556,
    0x88: 333, 0x89: 1000, 0x8A: 667, 0x8B: 333, 0x8C: 1000, 0x8E: 611, 0x91: 222,
    0x92: 222, 0x93: 333, 0x94: 333, 0x95: 350, 0x96: 556, 0x97: 1000, 0x98: 333,
    0x99: 1000, 0x9A: 500, 0x9B: 333, 0x9C: 944, 0x9E: 500, 0x9F: 667, 0xA0: 278,
    0xA1: 333, 0xA2: 556, 0xA3: 556, 0xA4: 556, 0xA5: 556, 0xA6: 260, 0xA7: 556,
    0xA8: 333, 0xA9: 737, 0xAA: 370, 0xAB: 556, 0xAC: 584, 0xAD: 333, 0xAE: 737,
    0xAF: 333, 0xB0: 400, 0xB1: 584, 0xB2: 333, 0xB3: 333, 0xB4: 333, 0xB5: 556,
    0xB6: 537, 0xB7: 278, 0xB8: 333, 0xB9: 333, 0xBA: 365, 0xBB: 556, 0xBC: 834,
    0xBD: 834, 0xBE: 834, 0xBF: 611, 0xC0: 667, 0xC1: 667, 0xC2: 667, 0xC3: 667,
    0xC4: 667, 0xC5: 667, 0xC6: 1000, 0xC7: 722, 0xC8: 667, 0xC9: 667, 0xCA: 667,
    0xCB: 667, 0xCC: 278, 0xCD: 278, 0xCE: 278, 0xCF: 278, 0xD0: 722, 0xD1: 722,
    0xD2: 778, 0xD3: 778, 0xD4: 778, 0xD5: 778, 0xD6: 778, 0xD7: 584, 0xD8: 778,
    0xD9: 722, 0xDA: 722, 0xDB: 722, 0xDC: 722, 0xDD: 667, 0xDE: 667, 0xDF: 611,
    0xE0: 556, 0xE1: 556, 0xE2: 556, 0xE3: 556, 0xE4: 556, 0xE5: 556, 0xE6: 889,
    0xE7: 500, 0xE8: 556, 0xE9: 556, 0xEA: 556, 0xEB: 556, 0xEC: 278, 0xED: 278,
    0xEE: 278, 0xEF: 278, 0xF0: 556, 0xF1: 556, 0xF2: 556, 0xF3: 556, 0xF4: 556,
    0xF5: 556, 0xF6: 556, 0xF7: 584, 0xF8: 611, 0xF9: 556, 0xFA: 556, 0xFB: 556,
    0xFC: 556, 0xFD: 500, 0xFE: 556, 0xFF: 500,
}  # fmt: skip
_DEFAULT_WIDTH = 556

# Characters that cp1252 cannot carry but that have an obvious ASCII stand-in.
_FALLBACKS: dict[str, str] = {
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\u2192": "->",
    "\u2190": "<-",
    "\u2713": "x",
    "\u2714": "x",
    "\u25cf": "\u2022",
    "\u25aa": "\u2022",
    "\u25e6": "\u2022",
    "\u2219": "\u2022",
    "\u00a0": " ",
    "\u2007": " ",
    "\u202f": " ",
    "\u200b": "",
    "\ufeff": "",
}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _byte_width(code: int) -> int:
    if 32 <= code <= 126:
        return _ASCII_WIDTHS[code - 32]
    return _HIGH_WIDTHS.get(code, _DEFAULT_WIDTH)


def _encode(text: str) -> bytes:
    """Encode one line as cp1252, substituting what the font cannot show."""
    out = bytearray()
    for char in text:
        char = _FALLBACKS.get(char, char)
        if not char:
            continue
        try:
            out += char.encode(_ENCODING)
        except UnicodeEncodeError:
            out += b"?"
    return bytes(out)


def text_width(text: str, size: float = FONT_SIZE) -> float:
    """Width in points of ``text`` set in Helvetica at ``size``."""
    return sum(_byte_width(code) for code in _encode(text)) * size / 1000.0


def _break_long_word(word: str, limit: float) -> list[str]:
    """Split a word wider than the line into chunks that each fit."""
    pieces: list[str] = []
    current = ""
    for char in word:
        if current and text_width(current + char) > limit:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return pieces


def wrap_text(text: str, limit: float = USABLE_WIDTH) -> list[str]:
    """Wrap ``text`` into lines no wider than ``limit`` points at 11 pt Helvetica.

    Paragraph breaks are kept (a blank input line stays a blank output line),
    tabs become spaces, control characters are dropped and words wider than the
    line are broken by character so nothing ever overflows the page.
    """
    cleaned = _CONTROL_RE.sub("", str(text or "")).replace("\r\n", "\n")
    cleaned = cleaned.replace("\r", "\n").replace("\t", " " * TAB_WIDTH)
    lines: list[str] = []
    for paragraph in cleaned.split("\n"):
        words = paragraph.split(" ")
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for word in words:
            if not word:
                # Consecutive spaces: keep a single space in the flow.
                continue
            candidate = f"{current} {word}" if current else word
            if text_width(candidate) <= limit:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if text_width(word) <= limit:
                current = word
            else:
                pieces = _break_long_word(word, limit)
                lines.extend(pieces[:-1])
                current = pieces[-1]
        lines.append(current)
    return lines


def _pdf_string(text: str) -> bytes:
    """A PDF literal string: cp1252 bytes with the delimiters escaped."""
    raw = _encode(text)
    raw = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    raw = raw.replace(b"\r", b"\\r").replace(b"\n", b"\\n")
    return b"(" + raw + b")"


def _page_stream(lines: list[str]) -> bytes:
    top = PAGE_HEIGHT - MARGIN - FONT_SIZE
    parts = [
        b"BT",
        f"/F1 {FONT_SIZE:g} Tf".encode("ascii"),
        f"{LEADING:g} TL".encode("ascii"),
        f"{MARGIN:g} {top:g} Td".encode("ascii"),
    ]
    for index, line in enumerate(lines):
        if index:
            parts.append(b"T*")
        if line:
            parts.append(_pdf_string(line) + b" Tj")
    parts.append(b"ET")
    return b"\n".join(parts) + b"\n"


def text_to_pdf(text: str, title: str = "") -> bytes:
    """Render ``text`` as a multi-page Letter PDF in Helvetica 11 pt.

    ``title`` goes into the document information dictionary (shown in PDF
    viewers and read by ATS parsers); it is not drawn on the page. The result
    always has at least one page, even for empty text.
    """
    lines = wrap_text(text)
    pages = [
        lines[start : start + LINES_PER_PAGE]
        for start in range(0, len(lines), LINES_PER_PAGE)
    ] or [[]]

    # Object numbering: 1 catalog, 2 pages tree, 3 font, 4 info, then one page
    # object and one content stream per page.
    objects: list[bytes] = []
    page_ids = [5 + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids).encode("ascii")
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(len(pages)).encode("ascii")
        + b" >>"
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
        b" /Encoding /WinAnsiEncoding >>"
    )
    stamp = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    info = b"<< /Producer (Monosage Career Copilot) /CreationDate (" + stamp.encode("ascii")
    if title.strip():
        info += b") /Title " + _pdf_string(" ".join(title.split())[:200]) + b" >>"
    else:
        info += b") >>"
    objects.append(info)
    for page_id, page_lines in zip(page_ids, pages, strict=True):
        stream = _page_stream(page_lines)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
            + f"{PAGE_WIDTH:g} {PAGE_HEIGHT:g}".encode("ascii")
            + b"] /Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(page_id + 1).encode("ascii")
            + b" 0 R >>"
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)
