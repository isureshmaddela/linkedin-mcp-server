"""Encrypted document storage for the Aira Career Copilot.

Documents (resumes, cover letters, offers, screenshots, ...) are stored as
Fernet-encrypted blobs in a per-owner directory on local disk. Metadata,
version chains and the audit trail live in SQLite. The caller passes an open
``sqlite3.Connection`` (with ``row_factory = sqlite3.Row``) to every method so
the module stays testable in isolation and never owns the connection.

Security properties:

* content is encrypted at rest with a Fernet key the caller supplies;
* every read verifies the owner, decrypts and re-checks the SHA-256 of the
  plaintext before returning it;
* uploads are validated for size, extension and content signature, and are
  passed through a pluggable malware-scan hook before anything is written;
* file names are sanitised, path traversal is rejected, and error messages
  never reveal filesystem paths;
* every store, download, delete and purge is written to an audit table.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "ALLOWED_TYPES",
    "DOCUMENT_KINDS",
    "MAX_DOCUMENT_BYTES",
    "MAX_FILENAME_LENGTH",
    "MAX_METADATA_BYTES",
    "DocumentKeyError",
    "DocumentStorageError",
    "DocumentStore",
    "ScanHook",
    "Record",
    "RecordList",
    "ScanResult",
    "ensure_schema",
    "load_or_create_key",
    "no_scan_hook",
    "sanitize_filename",
    "validate_upload",
]

DOCUMENT_KINDS: tuple[str, ...] = (
    "master_resume",
    "tailored_resume",
    "cover_letter",
    "application_confirmation",
    "screenshot",
    "offer",
    "recruiter_document",
    "assessment",
    "certificate",
    "email",
    "other",
)

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024
MAX_FILENAME_LENGTH = 120
MAX_TITLE_LENGTH = 200
MAX_ACTOR_LENGTH = 100
MAX_REFERENCE_LENGTH = 200
MAX_LIST_LIMIT = 1000

ALLOWED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".eml": "message/rfc822",
    ".json": "application/json",
    ".ics": "text/calendar",
    ".csv": "text/csv",
}

_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".eml", ".csv", ".ics"})
_FILENAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._ -]")
_OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_DOCUMENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_AUDIT_ACTIONS = frozenset({"store", "download", "delete", "purge"})


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of a malware scan.

    ``status`` is one of ``"clean"``, ``"not_scanned"``, ``"infected"`` or
    ``"error"``. ``ok`` decides whether the upload may be stored at all.
    """

    ok: bool
    status: str
    message: str


ScanHook = Callable[[bytes, str], ScanResult]

Record = dict[str, Any]
"""A document or audit record as returned by :class:`DocumentStore`."""

RecordList = list[Record]


def no_scan_hook(data: bytes, filename: str) -> ScanResult:
    """Default scan hook used when no malware scanner is configured."""
    return ScanResult(True, "not_scanned", "No malware scanner is configured.")


class DocumentKeyError(ValueError):
    """Raised when an encryption key is missing, unreadable or malformed."""


class DocumentStorageError(RuntimeError):
    """Raised when encrypted content cannot be written to or removed from disk."""


def _validate_key(key: Any) -> Any:
    if callable(getattr(key, "encrypt", None)) and callable(
        getattr(key, "decrypt", None)
    ):
        # A Fernet or MultiFernet (a hosted deployment rotating keys keeps older keys readable).
        return key
    if not isinstance(key, (bytes, bytearray)):
        raise DocumentKeyError("The document encryption key must be bytes.")
    try:
        return Fernet(bytes(key))
    except (ValueError, TypeError) as exc:
        raise DocumentKeyError(
            "The document encryption key is not a valid Fernet key "
            "(expected 32 url-safe base64-encoded bytes)."
        ) from exc


def load_or_create_key(path: Path) -> bytes:
    """Return the Fernet key stored at ``path``, creating it when absent.

    A new key is generated from ``secrets`` and written atomically. On POSIX
    systems the file is restricted to the owner (mode 0600). Calling this
    twice with the same path returns the same key.
    """
    key_path = Path(path)
    if key_path.exists():
        try:
            raw = key_path.read_bytes().strip()
        except OSError as exc:
            raise DocumentKeyError(
                "The document encryption key file could not be read."
            ) from exc
        if not raw:
            raise DocumentKeyError("The document encryption key file is empty.")
        _validate_key(raw)
        return raw

    key = base64.urlsafe_b64encode(secrets.token_bytes(32))
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(key_path, key + b"\n", private=True)
    except OSError as exc:
        raise DocumentKeyError(
            "The document encryption key file could not be created."
        ) from exc
    return key


def _write_atomically(target: Path, data: bytes, *, private: bool = False) -> None:
    """Write ``data`` to ``target`` via a temporary file and an atomic replace."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp-", suffix=target.suffix
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if private and os.name == "posix":
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #


def sanitize_filename(filename: str) -> str:
    """Return a safe file name limited to ``[A-Za-z0-9._ -]`` and 120 chars.

    Raises ``ValueError`` for empty names, control characters, path separators
    and ``..`` segments. Characters outside the safe set are replaced with ``_``.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("A file name is required.")
    if any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise ValueError("The file name contains control characters.")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(
            "The file name may not contain path separators or '..' segments."
        )
    cleaned = _FILENAME_UNSAFE_RE.sub("_", filename.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    if not cleaned:
        raise ValueError("The file name has no usable characters.")
    dot = cleaned.rfind(".")
    if dot >= 0:
        suffix = cleaned[dot:].lower()
        stem = cleaned[:dot].rstrip(" .")
        if not stem:
            raise ValueError("The file name needs a name before the extension.")
    else:
        suffix = ""
        stem = cleaned
    if len(stem) + len(suffix) > MAX_FILENAME_LENGTH:
        stem = stem[: MAX_FILENAME_LENGTH - len(suffix)].rstrip(" .")
        if not stem:
            raise ValueError("The file name is too long.")
    return f"{stem}{suffix}"


def _sniff(extension: str, data: bytes) -> None:
    """Raise ``ValueError`` when ``data`` does not look like ``extension``."""
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("The file is not a valid PDF document.")
    elif extension == ".png":
        if not data.startswith(b"\x89PNG"):
            raise ValueError("The file is not a valid PNG image.")
    elif extension in (".jpg", ".jpeg"):
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("The file is not a valid JPEG image.")
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
            raise ValueError("The file is not a valid Word (.docx) document.") from exc
        if "word/document.xml" not in names:
            raise ValueError("The file is not a valid Word (.docx) document.")
    elif extension == ".json":
        try:
            json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("The file is not valid UTF-8 encoded JSON.") from exc
    elif extension in _TEXT_EXTENSIONS:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"The file must be UTF-8 encoded text to be stored as {extension}."
            ) from exc


def validate_upload(filename: str, data: bytes, kind: str) -> tuple[str, str]:
    """Validate an upload and return ``(safe_filename, content_type)``.

    Raises ``ValueError`` with a plain-language message when the kind is
    unknown, the data is empty or too large, the extension is not allowed,
    the content does not match the extension, or the file name is unsafe.
    """
    if kind not in DOCUMENT_KINDS:
        raise ValueError(
            "Unknown document kind. Expected one of: " + ", ".join(DOCUMENT_KINDS) + "."
        )
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Document content must be bytes.")
    if len(data) == 0:
        raise ValueError("The file is empty.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"The file is too large. The limit is {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB."
        )
    safe_name = sanitize_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_TYPES:
        raise ValueError(
            "This file type is not allowed. Allowed types: "
            + ", ".join(sorted(ALLOWED_TYPES))
            + "."
        )
    _sniff(extension, bytes(data))
    return safe_name, ALLOWED_TYPES[extension]


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS documents (
        document_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        parent_document_id TEXT,
        root_document_id TEXT NOT NULL,
        job_id TEXT,
        related_type TEXT,
        related_id TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        scan_status TEXT NOT NULL,
        scan_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        retention_days INTEGER,
        expires_at TEXT,
        deleted_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_documents_owner_created"
    " ON documents (owner_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_documents_owner_kind ON documents (owner_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_documents_owner_job ON documents (owner_id, job_id)",
    "CREATE INDEX IF NOT EXISTS idx_documents_root ON documents (root_document_id)",
    "CREATE INDEX IF NOT EXISTS idx_documents_expires ON documents (expires_at)",
    """
    CREATE TABLE IF NOT EXISTS document_audit (
        audit_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor TEXT NOT NULL,
        at TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_document_audit_owner"
    " ON document_audit (owner_id, at)",
    "CREATE INDEX IF NOT EXISTS idx_document_audit_document"
    " ON document_audit (document_id)",
)


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the ``documents`` and ``document_audit`` tables if missing."""
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse_iso(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def _validate_owner_id(owner_id: str) -> str:
    if not isinstance(owner_id, str) or not _OWNER_ID_RE.match(owner_id):
        raise ValueError(
            "The owner id must be 1-128 characters of letters, digits, '.', '_', "
            "'@' or '-' and must start with a letter or digit."
        )
    return owner_id


def _validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not _DOCUMENT_ID_RE.match(document_id):
        raise ValueError("The document id is not valid.")
    return document_id


def _validate_actor(actor: str) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("An actor is required for the audit trail.")
    actor = actor.strip()
    if len(actor) > MAX_ACTOR_LENGTH:
        raise ValueError(f"The actor may not exceed {MAX_ACTOR_LENGTH} characters.")
    return actor


def _validate_reference(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"The {label} must be text.")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_REFERENCE_LENGTH:
        raise ValueError(
            f"The {label} may not exceed {MAX_REFERENCE_LENGTH} characters."
        )
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"The {label} contains control characters.")
    return value


def _validate_title(title: str) -> str:
    if title is None:
        return ""
    if not isinstance(title, str):
        raise ValueError("The title must be text.")
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"The title may not exceed {MAX_TITLE_LENGTH} characters.")
    return title


def _serialize_metadata(metadata: Mapping[str, Any] | None) -> str:
    if metadata is None:
        return "{}"
    if not isinstance(metadata, Mapping):
        raise ValueError("Metadata must be a mapping of string keys to JSON values.")
    if any(not isinstance(key, str) for key in metadata):
        raise ValueError("Metadata keys must be strings.")
    try:
        encoded = json.dumps(dict(metadata), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Metadata must only contain JSON-serializable values."
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError(
            f"Metadata is too large. The limit is {MAX_METADATA_BYTES // 1024} KB when "
            "serialized as JSON."
        )
    return encoded


def _validate_retention(retention_days: int | None) -> int | None:
    if retention_days is None:
        return None
    if isinstance(retention_days, bool) or not isinstance(retention_days, int):
        raise ValueError("Retention days must be a whole number of days.")
    if retention_days < 1:
        raise ValueError("Retention days must be at least 1.")
    return retention_days


def _row_to_record(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    record = dict(row)
    try:
        metadata = json.loads(record.get("metadata") or "{}")
    except ValueError:
        metadata = {}
    record["metadata"] = metadata if isinstance(metadata, dict) else {}
    return record


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


class LocalBlobBackend:
    """Ciphertext files under ``<root>/<owner_id>/<document_id>.bin`` on local disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path(self, owner_id: str, document_id: str) -> Path:
        return self.root / owner_id / f"{document_id}.bin"

    def put(self, owner_id: str, document_id: str, token: bytes) -> None:
        target = self.path(owner_id, document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(target, token)

    def get(self, owner_id: str, document_id: str) -> bytes:
        return self.path(owner_id, document_id).read_bytes()

    def delete(self, owner_id: str, document_id: str) -> None:
        self.path(owner_id, document_id).unlink(missing_ok=True)


class DocumentStore:
    """Encrypted, versioned, audited document storage for one deployment.

    ``root`` is the directory under which ``<owner_id>/<document_id>.bin``
    ciphertext files are written. ``key`` is a Fernet key (see
    :func:`load_or_create_key`). ``scan_hook`` is called with the plaintext
    and safe file name before anything is written. ``now`` can be injected for
    deterministic timestamps and retention tests.
    """

    def __init__(
        self,
        root: Path | Any,
        key: bytes,
        *,
        scan_hook: ScanHook = no_scan_hook,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        # ``root`` is a directory (local disk) or a blob backend with put/get/delete,
        # for example the object storage a hosted deployment uses.
        if callable(getattr(root, "put", None)) and callable(
            getattr(root, "get", None)
        ):
            self._blobs: Any = root
            self._root = Path(getattr(root, "root", "."))
        else:
            self._root = Path(root)
            self._blobs = LocalBlobBackend(self._root)
        self._fernet = _validate_key(key)
        if not callable(scan_hook):
            raise ValueError("The scan hook must be callable.")
        self._scan_hook: ScanHook = scan_hook
        self._now: Callable[[], datetime] = now or (lambda: datetime.now(timezone.utc))

    # -- schema -------------------------------------------------------------

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Create the document tables and indexes if they do not exist yet."""
        ensure_schema(connection)

    # -- internals ----------------------------------------------------------

    def _timestamp(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime):
            raise ValueError("The clock must return a datetime.")
        return _utc(value)

    def _fetch(
        self, connection: sqlite3.Connection, document_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def _require_owned(
        self, connection: sqlite3.Connection, owner_id: str, document_id: str
    ) -> dict[str, Any]:
        """Return the record or raise ``LookupError``/``PermissionError``."""
        record = self._fetch(connection, _validate_document_id(document_id))
        if record is None:
            raise LookupError("The document was not found.")
        if record["owner_id"] != owner_id:
            raise PermissionError("You do not have access to this document.")
        return record

    def _write_blob(self, owner_id: str, document_id: str, token: bytes) -> None:
        try:
            self._blobs.put(owner_id, document_id, token)
        except OSError as exc:
            raise DocumentStorageError(
                "The document could not be written to storage."
            ) from exc

    def _remove_blob(self, owner_id: str, document_id: str) -> None:
        try:
            self._blobs.delete(owner_id, document_id)
        except OSError as exc:
            raise DocumentStorageError(
                "The document content could not be removed from storage."
            ) from exc

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        document_id: str,
        action: str,
        actor: str,
        detail: str,
        at: datetime | None = None,
    ) -> None:
        if action not in _AUDIT_ACTIONS:
            raise ValueError("Unknown audit action.")
        connection.execute(
            "INSERT INTO document_audit"
            " (audit_id, owner_id, document_id, action, actor, at, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                owner_id,
                document_id,
                action,
                actor,
                _iso(at or self._timestamp()),
                detail,
            ),
        )

    def _soft_delete(
        self,
        connection: sqlite3.Connection,
        record: dict[str, Any],
        *,
        action: str,
        actor: str,
        detail: str,
        at: datetime,
    ) -> dict[str, Any]:
        deleted_at = _iso(at)
        connection.execute(
            "UPDATE documents SET deleted_at = ? WHERE document_id = ?",
            (deleted_at, record["document_id"]),
        )
        self._audit(
            connection,
            owner_id=record["owner_id"],
            document_id=record["document_id"],
            action=action,
            actor=actor,
            detail=detail,
            at=at,
        )
        self._remove_blob(record["owner_id"], record["document_id"])
        updated = dict(record)
        updated["deleted_at"] = deleted_at
        return updated

    # -- public API ---------------------------------------------------------

    def store(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        kind: str,
        filename: str,
        data: bytes,
        title: str = "",
        job_id: str | None = None,
        related_type: str | None = None,
        related_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        parent_document_id: str | None = None,
        actor: str = "user",
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        """Validate, scan, encrypt and persist a document; return its record."""
        owner_id = _validate_owner_id(owner_id)
        safe_name, content_type = validate_upload(filename, data, kind)
        title = _validate_title(title)
        job_id = _validate_reference(job_id, "job id")
        related_type = _validate_reference(related_type, "related type")
        related_id = _validate_reference(related_id, "related id")
        metadata_json = _serialize_metadata(metadata)
        actor = _validate_actor(actor)
        retention_days = _validate_retention(retention_days)

        version = 1
        root_document_id: str | None = None
        if parent_document_id is not None:
            try:
                parent = self._require_owned(connection, owner_id, parent_document_id)
            except LookupError as error:
                # The parent is named by the caller, so a missing one is bad input, not a missing resource.
                raise ValueError(str(error)) from error
            if parent["kind"] != kind:
                raise ValueError(
                    "A new version must have the same kind as the document it replaces."
                )
            version = int(parent["version"]) + 1
            root_document_id = parent["root_document_id"]

        try:
            result = self._scan_hook(bytes(data), safe_name)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise ValueError(
                "The malware scan could not be completed, so the document was not stored."
            ) from exc
        if not isinstance(result, ScanResult):
            raise ValueError("The scan hook must return a ScanResult.")
        if not result.ok:
            raise ValueError(
                result.message or "The document was rejected by the malware scanner."
            )

        plaintext = bytes(data)
        document_id = uuid.uuid4().hex
        root_document_id = root_document_id or document_id
        digest = hashlib.sha256(plaintext).hexdigest()
        created_at = self._timestamp()
        expires_at = (
            created_at + timedelta(days=retention_days) if retention_days else None
        )
        token = self._fernet.encrypt(plaintext)

        record: dict[str, Any] = {
            "document_id": document_id,
            "owner_id": owner_id,
            "kind": kind,
            "title": title or safe_name,
            "filename": safe_name,
            "content_type": content_type,
            "size": len(plaintext),
            "sha256": digest,
            "version": version,
            "parent_document_id": parent_document_id,
            "root_document_id": root_document_id,
            "job_id": job_id,
            "related_type": related_type,
            "related_id": related_id,
            "metadata": json.loads(metadata_json),
            "scan_status": result.status,
            "scan_message": result.message,
            "created_at": _iso(created_at),
            "created_by": actor,
            "retention_days": retention_days,
            "expires_at": _iso(expires_at) if expires_at else None,
            "deleted_at": None,
        }

        self._write_blob(owner_id, document_id, token)
        try:
            connection.execute(
                "INSERT INTO documents (document_id, owner_id, kind, title, filename,"
                " content_type, size, sha256, version, parent_document_id,"
                " root_document_id, job_id, related_type, related_id, metadata,"
                " scan_status, scan_message, created_at, created_by, retention_days,"
                " expires_at, deleted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    owner_id,
                    kind,
                    record["title"],
                    safe_name,
                    content_type,
                    len(plaintext),
                    digest,
                    version,
                    parent_document_id,
                    root_document_id,
                    job_id,
                    related_type,
                    related_id,
                    metadata_json,
                    result.status,
                    result.message,
                    record["created_at"],
                    actor,
                    retention_days,
                    record["expires_at"],
                    None,
                ),
            )
            self._audit(
                connection,
                owner_id=owner_id,
                document_id=document_id,
                action="store",
                actor=actor,
                detail=(
                    f"Stored {safe_name} as version {version} ({len(plaintext)} bytes);"
                    f" scan status: {result.status}."
                ),
                at=created_at,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            self._remove_blob(owner_id, document_id)
            raise
        return record

    def read(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        document_id: str,
        actor: str = "user",
    ) -> tuple[dict[str, Any], bytes]:
        """Decrypt and return ``(record, plaintext)`` after verifying ownership."""
        owner_id = _validate_owner_id(owner_id)
        actor = _validate_actor(actor)
        record = self._require_owned(connection, owner_id, document_id)
        if record["deleted_at"]:
            # The record stays for the audit trail, but the content is gone.
            raise LookupError("This document has been deleted.")
        try:
            token = self._blobs.get(owner_id, record["document_id"])
        except FileNotFoundError as exc:
            raise ValueError("The document content is missing from storage.") from exc
        except OSError as exc:
            raise DocumentStorageError(
                "The document content could not be read from storage."
            ) from exc
        try:
            plaintext = self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise ValueError(
                "The document could not be decrypted. It may have been tampered "
                "with or encrypted with a different key."
            ) from exc
        if hashlib.sha256(plaintext).hexdigest() != record["sha256"]:
            raise ValueError(
                "The document failed integrity verification: its content does not "
                "match the stored checksum."
            )
        self._audit(
            connection,
            owner_id=owner_id,
            document_id=record["document_id"],
            action="download",
            actor=actor,
            detail=f"Downloaded {record['filename']} ({record['size']} bytes).",
        )
        connection.commit()
        return record, plaintext

    def get(
        self, connection: sqlite3.Connection, *, owner_id: str, document_id: str
    ) -> dict[str, Any] | None:
        """Return the record, or ``None`` if it does not exist for this owner."""
        owner_id = _validate_owner_id(owner_id)
        record = self._fetch(connection, _validate_document_id(document_id))
        if record is None or record["owner_id"] != owner_id:
            return None
        return record

    def list(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        kind: str | None = None,
        job_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> RecordList:
        """List an owner's documents, newest first."""
        owner_id = _validate_owner_id(owner_id)
        if kind is not None and kind not in DOCUMENT_KINDS:
            raise ValueError(
                "Unknown document kind. Expected one of: "
                + ", ".join(DOCUMENT_KINDS)
                + "."
            )
        job_id = _validate_reference(job_id, "job id")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("The limit must be a positive whole number.")
        if limit > MAX_LIST_LIMIT:
            raise ValueError(f"The limit may not exceed {MAX_LIST_LIMIT}.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("The offset must be zero or a positive whole number.")

        clauses = ["owner_id = ?"]
        params: list[Any] = [owner_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(job_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        params.extend([limit, offset])
        rows = connection.execute(
            "SELECT * FROM documents WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def versions(
        self, connection: sqlite3.Connection, *, owner_id: str, document_id: str
    ) -> RecordList:
        """Return every version in the document's chain, root version first."""
        owner_id = _validate_owner_id(owner_id)
        record = self._require_owned(connection, owner_id, document_id)
        rows = connection.execute(
            "SELECT * FROM documents WHERE owner_id = ? AND root_document_id = ?"
            " ORDER BY version ASC, created_at ASC, rowid ASC",
            (owner_id, record["root_document_id"]),
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def delete(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        document_id: str,
        actor: str = "user",
        reason: str = "",
    ) -> dict[str, Any]:
        """Soft-delete the record and remove its encrypted content from disk."""
        owner_id = _validate_owner_id(owner_id)
        actor = _validate_actor(actor)
        if reason is None:
            reason = ""
        if not isinstance(reason, str):
            raise ValueError("The deletion reason must be text.")
        reason = reason.strip()
        if len(reason) > 500:
            raise ValueError("The deletion reason may not exceed 500 characters.")
        record = self._require_owned(connection, owner_id, document_id)
        if record["deleted_at"]:
            raise ValueError("This document has already been deleted.")
        try:
            updated = self._soft_delete(
                connection,
                record,
                action="delete",
                actor=actor,
                detail=reason or f"Deleted {record['filename']}.",
                at=self._timestamp(),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return updated

    def purge_expired(
        self, connection: sqlite3.Connection, *, owner_id: str | None = None
    ) -> int:
        """Delete documents whose retention period has elapsed; return the count."""
        if owner_id is not None:
            owner_id = _validate_owner_id(owner_id)
        now = self._timestamp()
        query = (
            "SELECT * FROM documents WHERE deleted_at IS NULL"
            " AND retention_days IS NOT NULL"
        )
        params: list[Any] = []
        if owner_id is not None:
            query += " AND owner_id = ?"
            params.append(owner_id)
        query += " ORDER BY created_at ASC, rowid ASC"
        rows = connection.execute(query, params).fetchall()

        purged = 0
        try:
            for row in rows:
                record = _row_to_record(row)
                try:
                    created_at = _parse_iso(record["created_at"])
                except ValueError:
                    continue
                expires_at = created_at + timedelta(days=int(record["retention_days"]))
                if expires_at > now:
                    continue
                self._soft_delete(
                    connection,
                    record,
                    action="purge",
                    actor="system",
                    detail=(
                        f"Retention period of {record['retention_days']} days elapsed."
                    ),
                    at=now,
                )
                purged += 1
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return purged

    def audit(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        document_id: str | None = None,
        limit: int = 100,
    ) -> RecordList:
        """Return audit entries for an owner (optionally one document), newest first."""
        owner_id = _validate_owner_id(owner_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("The limit must be a positive whole number.")
        if limit > MAX_LIST_LIMIT:
            raise ValueError(f"The limit may not exceed {MAX_LIST_LIMIT}.")
        clauses = ["owner_id = ?"]
        params: list[Any] = [owner_id]
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(_validate_document_id(document_id))
        params.append(limit)
        rows = connection.execute(
            "SELECT audit_id, owner_id, document_id, action, actor, at, detail"
            " FROM document_audit WHERE "
            + " AND ".join(clauses)
            + " ORDER BY at DESC, rowid DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def usage(self, connection: sqlite3.Connection, *, owner_id: str) -> dict[str, int]:
        """Return ``{"count", "bytes"}`` for the owner's live documents."""
        owner_id = _validate_owner_id(owner_id)
        row = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes"
            " FROM documents WHERE owner_id = ? AND deleted_at IS NULL",
            (owner_id,),
        ).fetchone()
        return {"count": int(row["count"]), "bytes": int(row["bytes"])}
