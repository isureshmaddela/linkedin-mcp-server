"""Files for application uploads: the user's real resume or a formatted document built from text.

ATS forms want a file on disk. When the person approved a tailored resume for the job, that text is laid out
as a formatted resume (:mod:`aira.resume_render`: name and contact header, section headings, bold role lines
with dates on the right, bullets, page breaks that keep headings with their content) and uploaded as a PDF, or,
when they chose "Word, in my own template" and their master resume is a simple ``.docx``, as a copy of that
Word file carrying the tailored content in its own styles. Without an approved version the master resume the
user uploaded (``.pdf`` or ``.docx``, Fernet-encrypted in the :class:`aira.documents.DocumentStore`) is
decrypted to a private temporary directory for one application run. When there is no uploaded file either, a
formatted PDF is built from the job's tailored resume draft (fallback: the profile resume text). Cover letters
are always built from text, in the same template, because Monosage writes them per job.

Both helpers return ``(path, cleanup)``; call ``cleanup()`` in a ``finally`` once the browser has finished with
the file. Cleanup is idempotent and never raises.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aira.documents import DocumentStorageError, DocumentStore
from aira.resume_render import (
    DocxTemplateError,
    cover_letter_pdf,
    docx_template_status,
    render_docx,
    resume_pdf,
)

__all__ = [
    "RESUME_DOCUMENT_KIND",
    "UPLOAD_EXTENSIONS",
    "UPLOAD_FORMATS",
    "cover_letter_for_upload",
    "master_docx_template",
    "resume_for_upload",
]

logger = logging.getLogger(__name__)

# The user's uploaded master resume. (``tailored_resume`` documents are text
# snapshots of generated drafts, so they go through the formatted path below.)
RESUME_DOCUMENT_KIND = "master_resume"
UPLOAD_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx")
# How an approved tailored resume is sent: Monosage's PDF layout, or the person's own Word template.
UPLOAD_FORMATS = ("pdf", "docx")
DEFAULT_ACTOR = "aira-apply"
_MAX_CANDIDATES = 50
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe_name(filename: str, fallback: str) -> str:
    """A file name safe for a temp directory and sensible for an employer to see."""
    cleaned = _UNSAFE_RE.sub("_", str(filename or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or fallback


def _display_name(job: Mapping[str, Any], base: str) -> str:
    company = re.sub(r"\s+", " ", str(job.get("company") or "")).strip()
    return f"{base} - {company}"[:80] if company else base


def _write_temp(filename: str, data: bytes) -> tuple[Path, Callable[[], None]]:
    directory = Path(tempfile.mkdtemp(prefix="aira-upload-"))
    path = directory / filename
    path.write_bytes(data)

    def cleanup() -> None:
        shutil.rmtree(directory, ignore_errors=True)

    return path, cleanup


def _master_records(
    connection: sqlite3.Connection, store: DocumentStore, owner_id: str
) -> list[dict[str, Any]]:
    store.ensure_schema(connection)
    return [
        dict(record)
        for record in store.list(
            connection,
            owner_id=owner_id,
            kind=RESUME_DOCUMENT_KIND,
            limit=_MAX_CANDIDATES,
        )
    ]


def master_docx_template(
    connection: sqlite3.Connection,
    store: DocumentStore,
    owner_id: str,
    *,
    actor: str = DEFAULT_ACTOR,
) -> tuple[bytes | None, str]:
    """``(bytes, "")`` of the newest master resume when it is a Word file that can carry tailored content in its
    own styles, else ``(None, reason)``."""
    for record in _master_records(connection, store, owner_id):
        filename = str(record.get("filename") or "")
        suffix = Path(filename).suffix.lower()
        if suffix not in UPLOAD_EXTENSIONS:
            continue
        if suffix != ".docx":
            return (
                None,
                "Your master resume is a PDF; upload a Word (.docx) resume to use your own template.",
            )
        try:
            _, data = store.read(
                connection,
                owner_id=owner_id,
                document_id=record["document_id"],
                actor=actor,
            )
        except (LookupError, PermissionError, ValueError, DocumentStorageError):
            continue
        ok, reason = docx_template_status(data)
        return (data, "") if ok else (None, reason)
    return (
        None,
        "Upload your resume as a Word (.docx) file in Documents to use your own template.",
    )


def resume_for_upload(
    connection: sqlite3.Connection,
    store: DocumentStore,
    owner_id: str,
    job: Mapping[str, Any],
    profile_resume_text: str,
    *,
    actor: str = DEFAULT_ACTOR,
    contact: Mapping[str, Any] | None = None,
) -> tuple[Path, Callable[[], None]]:
    """Return ``(path, cleanup)`` for the file to attach as the resume.

    Preference order: ``job["approved_resume"]`` (the tailored resume the person approved for this job in the
    resume editor) as a formatted PDF, or as a copy of their Word master resume when
    ``job["approved_resume_format"]`` is ``"docx"`` and that file qualifies; then the newest readable
    ``master_resume`` document with a ``.pdf``/``.docx`` name (decrypted to a temp file keeping its original
    name); then a formatted PDF of ``job["resume_draft"]``; then of ``profile_resume_text``. ``contact`` (name,
    email, phone, location, links) fills the header when the text has none. Raises ``LookupError`` when none of
    those exist.
    """
    approved = str(job.get("approved_resume") or "").strip()
    name = _display_name(job, "Resume")
    if approved:
        if str(job.get("approved_resume_format") or "") == "docx":
            try:
                template, _reason = master_docx_template(
                    connection, store, owner_id, actor=actor
                )
                if template is not None:
                    return _write_temp(
                        f"{_safe_name(name, 'Resume')}.docx",
                        render_docx(template, approved, contact=contact),
                    )
            except (DocxTemplateError, DocumentStorageError, LookupError, ValueError):
                logger.info(
                    "Word template unavailable; sending the PDF layout", exc_info=True
                )
        data, _layout = resume_pdf(approved, contact=contact, title=name)
        return _write_temp(f"{_safe_name(name, 'Resume')}.pdf", data)
    for record in _master_records(connection, store, owner_id):
        filename = str(record.get("filename") or "")
        if Path(filename).suffix.lower() not in UPLOAD_EXTENSIONS:
            continue
        try:
            _, data = store.read(
                connection,
                owner_id=owner_id,
                document_id=record["document_id"],
                actor=actor,
            )
        except (LookupError, PermissionError, ValueError, DocumentStorageError):
            # Missing blob, bad key or failed checksum: an older version may still work.
            continue
        return _write_temp(_safe_name(filename, "Resume.pdf"), data)

    text = (
        str(job.get("resume_draft") or "").strip()
        or str(profile_resume_text or "").strip()
    )
    if not text:
        raise LookupError(
            "No resume is available to upload. Upload your resume (PDF or Word) in "
            "Documents or add resume text to your profile."
        )
    data, _layout = resume_pdf(text, contact=contact, title=name)
    return _write_temp(f"{_safe_name(name, 'Resume')}.pdf", data)


def cover_letter_for_upload(
    job: Mapping[str, Any],
    text: str | None = None,
    *,
    contact: Mapping[str, Any] | None = None,
) -> tuple[Path, Callable[[], None]]:
    """Return ``(path, cleanup)`` for a formatted PDF cover letter built from text.

    ``text`` overrides ``job["cover_letter"]`` (for a letter edited during review); ``contact`` gives the
    letter the same name and contact header as the resume. Raises ``LookupError`` when there is no text.
    """
    body = (text if text is not None else str(job.get("cover_letter") or "")).strip()
    if not body:
        raise LookupError("No cover letter text is available to upload.")
    name = _display_name(job, "Cover Letter")
    data, _layout = cover_letter_pdf(body, contact=contact, title=name)
    return _write_temp(f"{_safe_name(name, 'Cover Letter')}.pdf", data)
