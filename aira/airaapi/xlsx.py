"""A minimal XLSX writer (one sheet, text and numbers) using only the standard library.

Strings are written as inline strings, which spreadsheet apps never evaluate, so a
cell that starts with ``=`` stays text and needs no CSV-style escaping.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Sequence
from xml.sax.saxutils import escape

# Characters XML 1.0 cannot carry at all, even escaped.
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
MAX_CELL_CHARS = 32_767  # Excel's limit per cell
_NUMBER = re.compile(r"-?(0|[1-9]\d{0,14})(\.\d{1,10})?")

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""
_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
# Style 0 is the default; style 1 is bold, used for the header row.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
</styleSheet>"""


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _sheet_name(name: str) -> str:
    cleaned = " ".join(re.sub(r"[\[\]:*?/\\]", " ", name).split()) or "Sheet1"
    return cleaned[:31]


def _cell(ref: str, value: str, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if not style and _NUMBER.fullmatch(value):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape(_INVALID_XML.sub("", value)[:MAX_CELL_CHARS])
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t xml:space="preserve">{text}</t></is></c>'


def workbook(
    sheet: str, columns: Sequence[str], rows: Sequence[Sequence[str]]
) -> bytes:
    """An .xlsx file with one sheet: a bold header row, then ``rows``. Plain numbers become numeric cells."""
    lines = [
        '<row r="1">'
        + "".join(
            _cell(f"{_column_name(i)}1", str(name), 1) for i, name in enumerate(columns)
        )
        + "</row>"
    ]
    for r, row in enumerate(rows, start=2):
        lines.append(
            f'<row r="{r}">'
            + "".join(
                _cell(f"{_column_name(i)}{r}", str(value))
                for i, value in enumerate(row)
                if value != ""
            )
            + "</row>"
        )
    widths = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{min(60, max(10, len(str(name)) + 4))}" customWidth="1"/>'
        for i, name in enumerate(columns)
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        + (f"<cols>{widths}</cols>" if widths else "")
        + f"<sheetData>{''.join(lines)}</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(_sheet_name(sheet))}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()
