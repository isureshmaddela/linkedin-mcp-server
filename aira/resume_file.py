"""Files for application uploads: the user's real resume or a PDF built from text.

ATS forms want a file on disk. The best file is the master resume the user
uploaded (``.pdf`` or ``.docx``) which lives Fernet-encrypted in the
:class:`aira.documents.DocumentStore`; it is decrypted to a private temporary
directory for the duration of one application run and removed by the returned
cleanup callable. When there is no uploaded file, a PDF is built from the job's
tailored resume draft (fallback: the profile resume text). Cover letters are
always built from text because Aira writes them per job.

Both helpers return ``(path, cleanup)``; call ``cleanup()`` in a ``finally``
once the browser has finished with the file. Cleanup is idempotent and never
raises.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aira.documents import DocumentStorageError, DocumentStore
from aira.pdf_text import text_to_pdf

__all__ = [
    "RESUME_DOCUMENT_KIND",
    "UPLOAD_EXTENSIONS",
    "cover_letter_for_upload",
    "resume_for_upload",
]

# The user's uploaded master resume. (``tailored_resume`` documents are text
# snapshots of generated drafts, so they go through the PDF path below.)
RESUME_DOCUMENT_KIND = "master_resume"
UPLOAD_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx")
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


def resume_for_upload(
    connection: sqlite3.Connection,
    store: DocumentStore,
    owner_id: str,
    job: Mapping[str, Any],
    profile_resume_text: str,
    *,
    actor: str = DEFAULT_ACTOR,
) -> tuple[Path, Callable[[], None]]:
    """Return ``(path, cleanup)`` for the file to attach as the resume.

    Preference order: a PDF of ``job["approved_resume"]`` (the tailored resume
    the person approved for this job in the resume editor), then the newest
    readable ``master_resume`` document with a ``.pdf``/``.docx`` name
    (decrypted to a temp file keeping its original name), then a PDF rendered
    from ``job["resume_draft"]``, then a PDF from ``profile_resume_text``.
    Raises ``LookupError`` when none of those exist.
    """
    approved = str(job.get("approved_resume") or "").strip()
    if approved:
        name = _display_name(job, "Resume")
        return _write_temp(
            f"{_safe_name(name, 'Resume')}.pdf", text_to_pdf(approved, name)
        )
    store.ensure_schema(connection)
    records = store.list(
        connection, owner_id=owner_id, kind=RESUME_DOCUMENT_KIND, limit=_MAX_CANDIDATES
    )
    for record in records:
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
    name = _display_name(job, "Resume")
    return _write_temp(f"{_safe_name(name, 'Resume')}.pdf", text_to_pdf(text, name))


def cover_letter_for_upload(
    job: Mapping[str, Any], text: str | None = None
) -> tuple[Path, Callable[[], None]]:
    """Return ``(path, cleanup)`` for a PDF cover letter built from text.

    ``text`` overrides ``job["cover_letter"]`` (for a letter edited during
    review). Raises ``LookupError`` when there is no text to render.
    """
    body = (text if text is not None else str(job.get("cover_letter") or "")).strip()
    if not body:
        raise LookupError("No cover letter text is available to upload.")
    name = _display_name(job, "Cover Letter")
    return _write_temp(
        f"{_safe_name(name, 'Cover Letter')}.pdf", text_to_pdf(body, name)
    )
