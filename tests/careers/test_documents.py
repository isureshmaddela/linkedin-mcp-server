import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from aira.documents import (
    ALLOWED_TYPES,
    DOCUMENT_KINDS,
    MAX_DOCUMENT_BYTES,
    DocumentKeyError,
    DocumentStore,
    ScanResult,
    load_or_create_key,
    no_scan_hook,
    sanitize_filename,
    validate_upload,
)

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)
JPG = b"\xff\xd8\xff\xe0" + bytes(16) + b"\xff\xd9"
OWNER = "user-1"
OTHER = "user-2"


def make_docx(with_document: bool = True) -> bytes:
    buffer = io.BytesIO()
    # A fixed timestamp: these bytes become test ids, which must be identical in every xdist worker.
    stamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("[Content_Types].xml", stamp), "<Types/>")
        if with_document:
            archive.writestr(
                zipfile.ZipInfo("word/document.xml", stamp), "<w:document/>"
            )
    return buffer.getvalue()


class Clock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current = self.current + timedelta(**kwargs)


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def key(tmp_path: Path) -> bytes:
    return load_or_create_key(tmp_path / "keys" / "documents.key")


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def store(tmp_path: Path, key: bytes, clock: Clock, connection) -> DocumentStore:
    store = DocumentStore(tmp_path / "docs", key, now=clock)
    store.ensure_schema(connection)
    store.ensure_schema(connection)  # idempotent
    return store


def test_store_and_read_round_trip(store, connection, tmp_path, key):
    record = store.store(
        connection,
        owner_id=OWNER,
        kind="master_resume",
        filename="resume.pdf",
        data=PDF,
        title="Master resume",
        job_id="job-42",
        related_type="application",
        related_id="app-7",
        metadata={"source": "upload", "pages": 1},
    )

    assert record["document_id"] and len(record["document_id"]) == 32
    assert record["owner_id"] == OWNER
    assert record["kind"] == "master_resume"
    assert record["title"] == "Master resume"
    assert record["filename"] == "resume.pdf"
    assert record["content_type"] == "application/pdf"
    assert record["size"] == len(PDF)
    assert record["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert record["version"] == 1
    assert record["parent_document_id"] is None
    assert record["root_document_id"] == record["document_id"]
    assert record["job_id"] == "job-42"
    assert record["related_type"] == "application"
    assert record["related_id"] == "app-7"
    assert record["metadata"] == {"source": "upload", "pages": 1}
    assert record["scan_status"] == "not_scanned"
    assert record["created_at"] == "2026-01-01T12:00:00+00:00"
    assert record["created_by"] == "user"
    assert record["retention_days"] is None
    assert record["expires_at"] is None
    assert record["deleted_at"] is None

    blob = tmp_path / "docs" / OWNER / f"{record['document_id']}.bin"
    assert blob.exists()
    ciphertext = blob.read_bytes()
    assert PDF not in ciphertext
    assert b"%PDF" not in ciphertext
    assert Fernet(key).decrypt(ciphertext) == PDF
    assert not [p for p in blob.parent.iterdir() if p.name.startswith(".tmp-")]

    fetched, data = store.read(
        connection, owner_id=OWNER, document_id=record["document_id"]
    )
    assert data == PDF
    assert fetched == record
    assert (
        store.get(connection, owner_id=OWNER, document_id=record["document_id"])
        == record
    )


def test_tampered_blob_raises(store, connection, tmp_path, key):
    record = store.store(
        connection, owner_id=OWNER, kind="other", filename="notes.txt", data=b"hello"
    )
    blob = tmp_path / "docs" / OWNER / f"{record['document_id']}.bin"

    # Different plaintext encrypted with the right key: Fernet accepts it, sha256 must not.
    blob.write_bytes(Fernet(key).encrypt(b"tampered"))
    with pytest.raises(ValueError, match="integrity"):
        store.read(connection, owner_id=OWNER, document_id=record["document_id"])

    # Corrupted token: Fernet rejects it before the checksum.
    blob.write_bytes(b"garbage")
    with pytest.raises(ValueError, match="decrypted"):
        store.read(connection, owner_id=OWNER, document_id=record["document_id"])

    blob.unlink()
    with pytest.raises(ValueError, match="missing"):
        store.read(connection, owner_id=OWNER, document_id=record["document_id"])


def test_owner_isolation(store, connection):
    record = store.store(
        connection, owner_id=OWNER, kind="cover_letter", filename="cl.md", data=b"# Hi"
    )
    doc_id = record["document_id"]

    with pytest.raises(PermissionError):
        store.read(connection, owner_id=OTHER, document_id=doc_id)
    with pytest.raises(PermissionError):
        store.delete(connection, owner_id=OTHER, document_id=doc_id)
    with pytest.raises(PermissionError):
        store.versions(connection, owner_id=OTHER, document_id=doc_id)
    with pytest.raises(PermissionError):
        store.store(
            connection,
            owner_id=OTHER,
            kind="cover_letter",
            filename="cl.md",
            data=b"# Hi",
            parent_document_id=doc_id,
        )
    assert store.get(connection, owner_id=OTHER, document_id=doc_id) is None
    assert store.list(connection, owner_id=OTHER) == []
    assert store.audit(connection, owner_id=OTHER) == []
    assert store.usage(connection, owner_id=OTHER) == {"count": 0, "bytes": 0}
    # The owner's data is untouched and no audit row was written for the other user.
    assert store.read(connection, owner_id=OWNER, document_id=doc_id)[1] == b"# Hi"

    with pytest.raises(LookupError, match="not found"):
        store.read(connection, owner_id=OWNER, document_id="0" * 32)
    with pytest.raises(ValueError, match="not valid"):
        store.read(connection, owner_id=OWNER, document_id="../etc/passwd")
    with pytest.raises(ValueError, match="owner id"):
        store.list(connection, owner_id="../other")


@pytest.mark.parametrize(
    ("filename", "data", "kind", "message"),
    [
        ("resume.pdf", b"", "master_resume", "empty"),
        ("tool.exe", b"MZ", "other", "not allowed"),
        ("resume.pdf", PDF, "diary", "Unknown document kind"),
        ("resume.pdf", b"not a pdf at all", "master_resume", "PDF"),
        ("shot.png", JPG, "screenshot", "PNG"),
        ("shot.jpg", PNG, "screenshot", "JPEG"),
        ("shot.jpeg", PNG, "screenshot", "JPEG"),
        ("resume.docx", b"PK\x03\x04 not really a zip", "master_resume", "Word"),
        ("resume.docx", make_docx(with_document=False), "master_resume", "Word"),
        ("data.json", b"{not json", "other", "JSON"),
        ("notes.txt", b"\xff\xfe\x00bad", "other", "UTF-8"),
        ("invite.ics", b"\xff\xfe", "other", "UTF-8"),
        ("../../etc/passwd.txt", b"root", "other", "path separators"),
        ("..\\secret.txt", b"root", "other", "path separators"),
        ("a\x00b.txt", b"text", "other", "control characters"),
        ("", b"text", "other", "file name is required"),
        ("   ", b"text", "other", "file name is required"),
        (".pdf", PDF, "other", "name before the extension"),
        ("noext", b"text", "other", "not allowed"),
    ],
)
def test_validate_upload_rejections(filename, data, kind, message):
    with pytest.raises(ValueError, match=message):
        validate_upload(filename, data, kind)


def test_validate_upload_rejects_oversized_files():
    with pytest.raises(ValueError, match="too large"):
        validate_upload(
            "big.pdf", b"%PDF-" + bytes(MAX_DOCUMENT_BYTES), "master_resume"
        )
    assert (
        validate_upload("max.txt", b"a" * MAX_DOCUMENT_BYTES, "other")[1]
        == "text/plain"
    )


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("resume.pdf", PDF),
        ("shot.png", PNG),
        ("shot.jpg", JPG),
        ("photo.JPEG", JPG),
        ("resume.docx", make_docx()),
        ("data.json", b'{"a": 1}'),
        ("notes.txt", "héllo".encode()),
        ("notes.md", b"# Title"),
        ("mail.eml", b"Subject: hi\r\n\r\nbody"),
        ("invite.ics", b"BEGIN:VCALENDAR\r\nEND:VCALENDAR"),
        ("rows.csv", b"a,b\n1,2"),
    ],
)
def test_validate_upload_accepts_valid_files(filename, data):
    safe, content_type = validate_upload(filename, data, "other")
    assert safe.lower() == filename.lower()
    assert content_type == ALLOWED_TYPES[Path(filename).suffix.lower()]


def test_validate_upload_accepts_every_kind():
    for kind in DOCUMENT_KINDS:
        assert validate_upload("x.txt", b"ok", kind) == ("x.txt", "text/plain")


def test_filename_sanitization():
    assert sanitize_filename("My Résumé (final)!.pdf") == "My R_sum_ _final__.pdf"
    assert sanitize_filename("  spaced   name .TXT ") == "spaced name.txt"
    assert sanitize_filename("report;rm -rf.csv") == "report_rm -rf.csv"
    long_name = "a" * 300 + ".pdf"
    safe = sanitize_filename(long_name)
    assert len(safe) == 120
    assert safe.endswith(".pdf")
    with pytest.raises(ValueError):
        sanitize_filename("tab\there.txt")
    with pytest.raises(ValueError):
        sanitize_filename("dir/file.txt")


def test_versioning_chain(store, connection, clock):
    v1 = store.store(
        connection, owner_id=OWNER, kind="tailored_resume", filename="r.pdf", data=PDF
    )
    clock.advance(minutes=1)
    v2 = store.store(
        connection,
        owner_id=OWNER,
        kind="tailored_resume",
        filename="r-v2.pdf",
        data=PDF + b"\n% v2\n",
        parent_document_id=v1["document_id"],
    )
    clock.advance(minutes=1)
    v3 = store.store(
        connection,
        owner_id=OWNER,
        kind="tailored_resume",
        filename="r-v3.pdf",
        data=PDF + b"\n% v3\n",
        parent_document_id=v2["document_id"],
    )

    assert (v1["version"], v2["version"], v3["version"]) == (1, 2, 3)
    assert v2["parent_document_id"] == v1["document_id"]
    assert v3["parent_document_id"] == v2["document_id"]
    assert v1["root_document_id"] == v2["root_document_id"] == v3["root_document_id"]
    assert v3["root_document_id"] == v1["document_id"]

    chain = store.versions(connection, owner_id=OWNER, document_id=v3["document_id"])
    assert [item["document_id"] for item in chain] == [
        v1["document_id"],
        v2["document_id"],
        v3["document_id"],
    ]
    assert (
        store.versions(connection, owner_id=OWNER, document_id=v1["document_id"])
        == chain
    )

    with pytest.raises(ValueError, match="same kind"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="cover_letter",
            filename="cl.txt",
            data=b"x",
            parent_document_id=v3["document_id"],
        )
    with pytest.raises(ValueError, match="not found"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="tailored_resume",
            filename="r.pdf",
            data=PDF,
            parent_document_id="f" * 32,
        )


def test_soft_delete_removes_blob_and_hides_from_list(store, connection, tmp_path):
    record = store.store(
        connection, owner_id=OWNER, kind="offer", filename="offer.pdf", data=PDF
    )
    blob = tmp_path / "docs" / OWNER / f"{record['document_id']}.bin"
    assert blob.exists()

    deleted = store.delete(
        connection,
        owner_id=OWNER,
        document_id=record["document_id"],
        actor="user",
        reason="Declined offer",
    )
    assert deleted["deleted_at"] == "2026-01-01T12:00:00+00:00"
    assert not blob.exists()

    assert store.list(connection, owner_id=OWNER) == []
    listed = store.list(connection, owner_id=OWNER, include_deleted=True)
    assert [item["document_id"] for item in listed] == [record["document_id"]]
    assert listed[0]["deleted_at"] == deleted["deleted_at"]
    assert store.get(connection, owner_id=OWNER, document_id=record["document_id"])[
        "deleted_at"
    ]

    with pytest.raises(LookupError, match="deleted"):
        store.read(connection, owner_id=OWNER, document_id=record["document_id"])
    with pytest.raises(ValueError, match="already been deleted"):
        store.delete(connection, owner_id=OWNER, document_id=record["document_id"])

    entries = store.audit(connection, owner_id=OWNER, document_id=record["document_id"])
    assert entries[0]["action"] == "delete"
    assert entries[0]["detail"] == "Declined offer"


def test_purge_expired_uses_injected_clock(store, connection, clock, tmp_path):
    expiring = store.store(
        connection,
        owner_id=OWNER,
        kind="screenshot",
        filename="s.png",
        data=PNG,
        retention_days=30,
    )
    other_owner = store.store(
        connection,
        owner_id=OTHER,
        kind="screenshot",
        filename="s.png",
        data=PNG,
        retention_days=30,
    )
    forever = store.store(
        connection, owner_id=OWNER, kind="certificate", filename="c.pdf", data=PDF
    )
    assert expiring["expires_at"] == "2026-01-31T12:00:00+00:00"
    assert forever["expires_at"] is None

    clock.advance(days=29)
    assert store.purge_expired(connection) == 0

    clock.advance(days=2)
    assert store.purge_expired(connection, owner_id=OWNER) == 1
    assert not (tmp_path / "docs" / OWNER / f"{expiring['document_id']}.bin").exists()
    assert (
        store.get(connection, owner_id=OWNER, document_id=expiring["document_id"])[
            "deleted_at"
        ]
        == "2026-02-01T12:00:00+00:00"
    )
    assert (
        store.get(connection, owner_id=OWNER, document_id=forever["document_id"])[
            "deleted_at"
        ]
        is None
    )
    # Other owners are untouched when an owner filter is passed.
    assert (
        store.get(connection, owner_id=OTHER, document_id=other_owner["document_id"])[
            "deleted_at"
        ]
        is None
    )
    assert store.purge_expired(connection) == 1
    assert store.purge_expired(connection) == 0

    purge_entries = [
        entry
        for entry in store.audit(connection, owner_id=OWNER)
        if entry["action"] == "purge"
    ]
    assert len(purge_entries) == 1
    assert purge_entries[0]["actor"] == "system"
    assert "30 days" in purge_entries[0]["detail"]

    with pytest.raises(ValueError, match="at least 1"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="other",
            filename="x.txt",
            data=b"x",
            retention_days=0,
        )


def test_audit_trail(store, connection, clock):
    record = store.store(
        connection,
        owner_id=OWNER,
        kind="email",
        filename="m.eml",
        data=b"Subject: x\r\n\r\nhi",
        actor="assistant",
    )
    clock.advance(seconds=1)
    store.read(
        connection, owner_id=OWNER, document_id=record["document_id"], actor="user"
    )
    clock.advance(seconds=1)
    store.delete(
        connection, owner_id=OWNER, document_id=record["document_id"], actor="user"
    )

    entries = store.audit(connection, owner_id=OWNER, document_id=record["document_id"])
    assert [entry["action"] for entry in entries] == ["delete", "download", "store"]
    assert [entry["actor"] for entry in entries] == ["user", "user", "assistant"]
    assert all(entry["owner_id"] == OWNER for entry in entries)
    assert all(entry["document_id"] == record["document_id"] for entry in entries)
    assert all(entry["audit_id"] for entry in entries)
    assert entries[-1]["at"] == "2026-01-01T12:00:00+00:00"
    assert "m.eml" in entries[-1]["detail"]
    assert "not_scanned" in entries[-1]["detail"]

    assert len(store.audit(connection, owner_id=OWNER, limit=2)) == 2
    with pytest.raises(ValueError):
        store.audit(connection, owner_id=OWNER, limit=0)
    with pytest.raises(ValueError, match="actor"):
        store.read(
            connection, owner_id=OWNER, document_id=record["document_id"], actor=""
        )


def test_usage_totals(store, connection):
    first = store.store(
        connection, owner_id=OWNER, kind="other", filename="a.txt", data=b"abc"
    )
    store.store(
        connection, owner_id=OWNER, kind="other", filename="b.txt", data=b"defgh"
    )
    store.store(connection, owner_id=OTHER, kind="other", filename="c.txt", data=b"zz")

    assert store.usage(connection, owner_id=OWNER) == {"count": 2, "bytes": 8}
    store.delete(connection, owner_id=OWNER, document_id=first["document_id"])
    assert store.usage(connection, owner_id=OWNER) == {"count": 1, "bytes": 5}
    assert store.usage(connection, owner_id=OTHER) == {"count": 1, "bytes": 2}


def test_list_filters_and_paging(store, connection, clock):
    ids = []
    for index in range(3):
        clock.advance(minutes=1)
        record = store.store(
            connection,
            owner_id=OWNER,
            kind="cover_letter" if index < 2 else "other",
            filename=f"{index}.txt",
            data=b"x",
            job_id="job-1" if index == 0 else None,
        )
        ids.append(record["document_id"])

    newest_first = [
        item["document_id"] for item in store.list(connection, owner_id=OWNER)
    ]
    assert newest_first == list(reversed(ids))
    assert [
        item["document_id"]
        for item in store.list(connection, owner_id=OWNER, kind="cover_letter")
    ] == [ids[1], ids[0]]
    assert [
        item["document_id"]
        for item in store.list(connection, owner_id=OWNER, job_id="job-1")
    ] == [ids[0]]
    assert [
        item["document_id"]
        for item in store.list(connection, owner_id=OWNER, limit=1, offset=1)
    ] == [ids[1]]
    with pytest.raises(ValueError):
        store.list(connection, owner_id=OWNER, kind="nope")
    with pytest.raises(ValueError):
        store.list(connection, owner_id=OWNER, limit=0)
    with pytest.raises(ValueError):
        store.list(connection, owner_id=OWNER, offset=-1)


def test_metadata_limits_and_round_trip(store, connection):
    record = store.store(
        connection,
        owner_id=OWNER,
        kind="other",
        filename="m.json",
        data=b"{}",
        metadata={"tags": ["a", "b"], "score": 0.5, "note": "ünïcode"},
    )
    assert record["metadata"] == {"tags": ["a", "b"], "score": 0.5, "note": "ünïcode"}
    stored = store.get(connection, owner_id=OWNER, document_id=record["document_id"])
    assert stored["metadata"] == record["metadata"]
    raw = connection.execute(
        "SELECT metadata FROM documents WHERE document_id = ?", (record["document_id"],)
    ).fetchone()["metadata"]
    assert json.loads(raw) == record["metadata"]

    with pytest.raises(ValueError, match="too large"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="other",
            filename="m.json",
            data=b"{}",
            metadata={"blob": "x" * 5000},
        )
    with pytest.raises(ValueError, match="JSON"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="other",
            filename="m.json",
            data=b"{}",
            metadata={"when": datetime(2026, 1, 1)},
        )
    with pytest.raises(ValueError, match="keys"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="other",
            filename="m.json",
            data=b"{}",
            metadata={1: "x"},
        )
    with pytest.raises(ValueError, match="mapping"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="other",
            filename="m.json",
            data=b"{}",
            metadata=["not", "a", "mapping"],
        )


def test_scan_hook_refusal_and_status(tmp_path, key, connection, clock):
    seen = []

    def hook(data: bytes, filename: str) -> ScanResult:
        seen.append((data, filename))
        if b"EICAR" in data:
            return ScanResult(False, "infected", "Malware signature EICAR detected.")
        return ScanResult(True, "clean", "Scanned by test scanner.")

    store = DocumentStore(tmp_path / "docs", key, scan_hook=hook, now=clock)
    store.ensure_schema(connection)

    with pytest.raises(ValueError, match="EICAR"):
        store.store(
            connection,
            owner_id=OWNER,
            kind="other",
            filename="bad!.txt",
            data=b"X5O EICAR",
        )
    assert seen[-1] == (b"X5O EICAR", "bad_.txt")
    assert store.list(connection, owner_id=OWNER) == []
    assert store.audit(connection, owner_id=OWNER) == []
    assert not (tmp_path / "docs" / OWNER).exists()

    record = store.store(
        connection, owner_id=OWNER, kind="other", filename="good.txt", data=b"fine"
    )
    assert record["scan_status"] == "clean"
    assert record["scan_message"] == "Scanned by test scanner."

    assert no_scan_hook(b"x", "x.txt") == ScanResult(
        True, "not_scanned", "No malware scanner is configured."
    )

    def broken(data: bytes, filename: str):
        return "clean"

    broken_store = DocumentStore(tmp_path / "docs2", key, scan_hook=broken)  # type: ignore[arg-type]
    broken_store.ensure_schema(connection)
    with pytest.raises(ValueError, match="ScanResult"):
        broken_store.store(
            connection, owner_id=OWNER, kind="other", filename="x.txt", data=b"x"
        )


def test_load_or_create_key_is_idempotent(tmp_path):
    path = tmp_path / "nested" / "documents.key"
    first = load_or_create_key(path)
    second = load_or_create_key(path)
    assert first == second
    assert len(first) == 44
    Fernet(first)
    assert path.read_bytes().strip() == first

    bad = tmp_path / "bad.key"
    bad.write_bytes(b"not-a-key")
    with pytest.raises(DocumentKeyError):
        load_or_create_key(bad)
    empty = tmp_path / "empty.key"
    empty.write_bytes(b"")
    with pytest.raises(DocumentKeyError):
        load_or_create_key(empty)
    with pytest.raises(DocumentKeyError):
        DocumentStore(tmp_path / "docs", b"short")


def test_errors_never_mention_filesystem_paths(store, connection, tmp_path):
    record = store.store(
        connection, owner_id=OWNER, kind="other", filename="n.txt", data=b"n"
    )
    (tmp_path / "docs" / OWNER / f"{record['document_id']}.bin").unlink()
    with pytest.raises(ValueError) as excinfo:
        store.read(connection, owner_id=OWNER, document_id=record["document_id"])
    assert str(tmp_path) not in str(excinfo.value)
    assert "docs" not in str(excinfo.value)
