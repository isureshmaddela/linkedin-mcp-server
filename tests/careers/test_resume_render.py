"""Formatted resumes and cover letters (aira/resume_render.py) and the files applications upload.

The tailored text is parsed into a header and sections, laid out on Letter pages and written as a PDF with
the Helvetica faces; the same layout is the editor's preview. A simple Word master resume can carry the
tailored content in its own styles. No network, no model.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, cast

from pypdf import PdfReader

from aira import resume_render as rr
from aira.resume_file import cover_letter_for_upload, resume_for_upload

RESUME = """# Jordan Example
Senior Platform Engineer
jordan@example.com | +1 555 010 2030 | Seattle, WA | linkedin.com/in/jordan-example

## Summary
Platform engineer with 8 years building **Kubernetes** platforms.
Known for calm incident leadership.

## Experience
**Senior Platform Engineer**, Acme Cloud | Jan 2021 - Present
- Led the migration of 140 services to Kubernetes, cutting deploy time from 40 to 6 minutes.
- Built a Terraform module library used by 30 teams.
  It now covers networking and IAM.

### Platform Engineer, Beta Systems (2017 - 2020)
* Ran on-call for 60 services; cut pages by 45%.

## Education
B.Sc. Computer Science, University of Washington 2016

## Skills
Cloud: AWS, GCP, Azure
Tools: Terraform, Helm, Argo CD
"""

PLAIN = """JORDAN EXAMPLE
jordan@example.com, +1 555 010 2030

PROFESSIONAL SUMMARY
Cloud architect.

WORK EXPERIENCE
Principal Architect - Acme   2019 - 2024
Designed the landing zone
- Cut cost 30%
"""


def _pdf_text(data: bytes) -> tuple[int, str]:
    reader = PdfReader(io.BytesIO(data))
    return len(reader.pages), "\n".join(page.extract_text() for page in reader.pages)


def test_markdown_resume_parses_into_header_sections_roles_and_bullets():
    doc = rr.parse_resume(RESUME)
    assert doc.name == "Jordan Example"
    assert doc.headline == "Senior Platform Engineer"
    assert doc.contact == [
        "jordan@example.com",
        "+1 555 010 2030",
        "Seattle, WA",
        "linkedin.com/in/jordan-example",
    ]
    assert [s.kind for s in doc.sections] == [
        "summary",
        "experience",
        "education",
        "skills",
    ]
    experience = doc.sections[1].blocks
    assert [b.kind for b in experience] == [
        "role",
        "bullet",
        "bullet",
        "role",
        "bullet",
    ]
    assert experience[0].right == "Jan 2021 \u2013 Present"
    assert experience[0].runs[0].bold and experience[0].text.startswith(
        "Senior Platform Engineer, Acme Cloud"
    )
    # A wrapped bullet continues on its indented line; the dates of a ### role go right.
    assert experience[2].text.endswith("It now covers networking and IAM.")
    assert experience[3].right == "2017 \u2013 2020"
    # Consecutive summary lines are one paragraph, and **bold** becomes a bold run.
    [summary] = doc.sections[0].blocks
    assert summary.kind == "para" and any(
        r.bold and r.text == "Kubernetes" for r in summary.runs
    )
    skills = doc.sections[3].blocks
    assert [b.kind for b in skills] == ["skill", "skill"] and skills[0].runs[0].bold


def test_plain_text_resume_with_caps_headings_and_a_comma_contact_line():
    doc = rr.parse_resume(PLAIN)
    assert doc.name == "JORDAN EXAMPLE"
    assert doc.contact == ["jordan@example.com", "+1 555 010 2030"]
    assert [s.kind for s in doc.sections] == ["summary", "experience"]
    blocks = doc.sections[1].blocks
    assert blocks[0].kind == "role" and blocks[0].right == "2019 \u2013 2024"
    assert blocks[1].kind == "meta" and blocks[2].kind == "bullet"


def test_the_pdf_has_the_layout_fonts_and_extractable_text():
    data, layout = rr.resume_pdf(RESUME, title="Resume - Acme")
    pages, text = _pdf_text(data)
    assert pages == 1 and layout.page_count == 1 and not layout.warnings
    for expected in (
        "Jordan Example",
        "SUMMARY",
        "EXPERIENCE",
        "Senior Platform Engineer, Acme Cloud",
        "Jan 2021 \u2013 Present",
        "\u2022 Led the migration",
    ):
        assert expected in text
    # Bold, regular and oblique Helvetica are all declared; the title is in the document information.
    for font in (b"/Helvetica-Bold", b"/Helvetica ", b"/Helvetica-Oblique"):
        assert font in data
    assert PdfReader(io.BytesIO(data)).metadata.title == "Resume - Acme"
    # Headings sit on the left margin with a rule under them; the dates are set right.
    ops = layout.pages[0]
    heading = next(op for op in ops if op[0] == "text" and op[5] == "EXPERIENCE")
    assert heading[1] == rr.MARGIN_X and heading[3] == rr.BOLD
    dates = next(
        op for op in ops if op[0] == "text" and op[5] == "Jan 2021 \u2013 Present"
    )
    assert (
        abs(
            dates[1]
            + rr.text_width(dates[5], rr.REGULAR, dates[4])
            - (rr.PAGE_WIDTH - rr.MARGIN_X)
        )
        < 0.5
    )
    assert any(op[0] == "rule" for op in ops)


def test_page_breaks_never_leave_a_heading_or_role_alone_at_the_foot():
    pushed = 0
    for count in range(40, 56):
        filler = "\n".join(f"- Achievement {i} on one line." for i in range(count))
        variant = f"# Jordan Example\njordan@example.com\n\n## Experience\n**Role A**, Acme | 2020 - 2024\n{filler}\n\n## Education\n**B.Sc.**, Uni | 2016\n- Thesis on distributed systems\n"
        layout = rr.layout_resume(variant)
        assert layout.page_count == 2
        second = [op[5] for op in layout.pages[1] if op[0] == "text"]
        if second[:2] == ["EDUCATION", "B.Sc."]:
            pushed += 1  # the heading moved to the next page with its first entry
        for page in layout.pages:
            texts = [op for op in page if op[0] == "text"]
            last = min(texts, key=lambda op: op[2])  # the lowest line on the page
            assert last[5] not in {"EXPERIENCE", "EDUCATION"}
            assert not (last[3] == rr.BOLD and last[5] in {"B.Sc.", "Role A"})
        # Nothing falls below the bottom margin.
        assert all(
            op[2] >= rr.MARGIN_BOTTOM - 1
            for page in layout.pages
            for op in page
            if op[0] == "text"
        )
    assert pushed >= 1


def test_characters_outside_winansi_are_transliterated_or_reported():
    lost: set[str] = set()
    assert rr.to_winansi(
        "\u0141\u00f3d\u017a \u2192 Gda\u0144sk \u2264 5 \u2713", lost
    ) == "\u0141\u00f3d\u017a -> Gdansk <= 5 x".replace("\u0141", "L").replace(
        "\u017a", "z"
    )
    assert not lost
    assert (
        rr.to_winansi("caf\u00e9 \u201cquotes\u201d \u20ac5", lost)
        == "caf\u00e9 \u201cquotes\u201d \u20ac5"
    )
    assert rr.to_winansi("\u4e2d\u6587", lost) == "??" and lost == {"\u4e2d", "\u6587"}
    _data, layout = rr.resume_pdf("# Jordan\n## Skills\nMandarin \u4e2d\u6587")
    assert any("cannot show 2 characters" in warning for warning in layout.warnings)


def test_the_profile_fills_a_missing_header_and_the_letter_gets_the_same_one():
    contact = {
        "name": "Jordan Example",
        "email": "jordan@example.com",
        "phone": "+1 555 010 2030",
        "location": "Seattle, WA",
        "links": ["https://www.linkedin.com/in/jordan-example/"],
    }
    data, _ = rr.resume_pdf("## Experience\n- Built things", contact=contact)
    _, text = _pdf_text(data)
    assert "Jordan Example" in text and "linkedin.com/in/jordan-example" in text
    letter = "Jordan Example\njordan@example.com\n\nDear Hiring Team,\n\nI would like to join Acme.\n\nKind regards,\nJordan Example"
    data, layout = rr.cover_letter_pdf(letter, contact=contact)
    _, text = _pdf_text(data)
    # The letter's own header lines are not repeated under the formatted header; the sign-off keeps its lines.
    assert text.count("jordan@example.com") == 1
    assert "Dear Hiring Team," in text and "Kind regards,\nJordan Example" in text
    preview = rr.preview_pages(layout)
    assert preview["page_count"] == 1 and preview["width"] == rr.PAGE_WIDTH
    names = [
        item for item in preview["pages"][0] if item.get("text") == "Jordan Example"
    ]
    assert names and names[0]["font"] == "bold" and names[0]["y"] < 100


# --------------------------------------------------------------------------- #
# Word templates
# --------------------------------------------------------------------------- #
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14"'


def _p(
    text: str, *, style: str = "", bold: bool = False, numbered: bool = False
) -> str:
    ppr = ""
    if style or numbered:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "")
        ppr += (
            '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
            if numbered
            else ""
        )
        ppr += "</w:pPr>"
    rpr = (
        '<w:rPr><w:rFonts w:ascii="Georgia"/><w:b/></w:rPr>'
        if bold
        else '<w:rPr><w:rFonts w:ascii="Georgia"/></w:rPr>'
    )
    return f'<w:p w:rsidR="00A1">{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>'


def _docx(body: str) -> bytes:
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1000" w:right="1000" w:bottom="1000" w:left="1000"/></w:sectPr>'
        "</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document)
        archive.writestr(
            "word/styles.xml",
            '<?xml version="1.0"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
    return out.getvalue()


TEMPLATE = _docx(
    _p("Jordan Example", style="Title")
    + _p("jordan@example.com | Seattle")
    + _p("Experience", style="Heading1")
    + _p("Architect, Acme | 2019 - 2024", bold=True)
    + _p("Designed the platform", numbered=True)
    + _p("Education", style="Heading1")
    + _p("Studied computer science at a university for four years.")
)


def test_a_simple_word_resume_carries_the_tailored_content_in_its_own_styles():
    assert rr.docx_template_status(TEMPLATE) == (True, "")
    tailored = RESUME.replace("Seattle, WA", "Seattle \u2013 \u00c5rhus \u4e2d")
    data = rr.render_docx(TEMPLATE, tailored)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
        assert archive.read("word/styles.xml")  # every other part is copied
    from xml.etree import ElementTree

    ElementTree.fromstring(xml.encode())  # well-formed, namespaces intact
    assert 'mc:Ignorable="w14"' in xml and '<w:pgSz w:w="11906"' in xml
    # The template's heading, bullet and role paragraphs are the patterns; Unicode survives in Word.
    assert xml.count('<w:pStyle w:val="Heading1"/>') == 4
    assert xml.count('<w:numId w:val="1"/>') == 3
    assert (
        "Senior Platform Engineer" in xml and "Led the migration of 140 services" in xml
    )
    assert "\u4e2d" in xml and "Designed the platform" not in xml
    assert (
        '<w:rFonts w:ascii="Georgia"/><w:b/><w:bCs/>' in xml
    )  # bold added after rFonts, in schema order
    assert "w:rsidR" not in xml.split("<w:body>")[1].split("<w:sectPr>")[0]


def test_word_resumes_with_tables_or_columns_fall_back_to_the_pdf_layout():
    with_table = _docx(
        _p("Jordan")
        + _p("Experience", style="Heading1")
        + "<w:tbl><w:tr><w:tc>"
        + _p("x")
        + "</w:tc></w:tr></w:tbl>"
    )
    ok, reason = rr.docx_template_status(with_table)
    assert not ok and "tables" in reason
    assert rr.docx_template_status(b"not a zip")[0] is False
    no_headings = _docx(_p("Jordan") + _p("Some text"))
    assert rr.docx_template_status(no_headings)[0] is False


class _Store:
    """A document store with one master resume."""

    def __init__(self, filename: str, data: bytes) -> None:
        self.filename, self.data = filename, data

    def ensure_schema(self, connection):
        return None

    def list(self, connection, **_kwargs):
        return [{"document_id": "d" * 32, "filename": self.filename}]

    def read(self, connection, **_kwargs):
        return {}, self.data


def test_applications_upload_the_formatted_pdf_or_the_word_copy():
    contact = {"name": "Jordan Example", "email": "jordan@example.com"}
    job = {"company": "Acme", "approved_resume": RESUME}
    path, cleanup = resume_for_upload(
        cast(Any, None),
        cast(Any, _Store("cv.docx", TEMPLATE)),
        "me",
        job,
        "",
        contact=contact,
    )
    try:
        assert path.suffix == ".pdf" and path.name == "Resume - Acme.pdf"
        assert "EXPERIENCE" in _pdf_text(path.read_bytes())[1]
    finally:
        cleanup()
    assert not path.exists()
    word_job = {**job, "approved_resume_format": "docx"}
    path, cleanup = resume_for_upload(
        cast(Any, None), cast(Any, _Store("cv.docx", TEMPLATE)), "me", word_job, ""
    )
    try:
        assert path.suffix == ".docx"
        with zipfile.ZipFile(path) as archive:
            assert (
                "Senior Platform Engineer" in archive.read("word/document.xml").decode()
            )
    finally:
        cleanup()
    # A PDF master cannot be a template: the approved text goes out in the PDF layout instead.
    path, cleanup = resume_for_upload(
        cast(Any, None), cast(Any, _Store("cv.pdf", b"%PDF-1.4")), "me", word_job, ""
    )
    try:
        assert path.suffix == ".pdf"
    finally:
        cleanup()
    letter, cleanup = cover_letter_for_upload(
        {"company": "Acme", "cover_letter": "Dear Acme,\n\nHello."}, contact=contact
    )
    try:
        assert (
            letter.name == "Cover Letter - Acme.pdf"
            and "Dear Acme," in _pdf_text(letter.read_bytes())[1]
        )
    finally:
        cleanup()
