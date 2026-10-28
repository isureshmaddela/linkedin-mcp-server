"""Media files for Videos and Ads: chunked, resumable uploads, type checks by content, a free-space reserve,
range reads for playback and signed addresses.

* **Where files live.** ``AIRA_MEDIA_DIR`` when it is set, otherwise the folder the host chose (the single-user
  build: ``media`` next to its databases; the hosted build: ``media`` in its data folder). Files are stored as
  uploaded (no transcoding) under ``files/<2 hex>/<file id>.<ext>``; uploads in progress under ``uploads/``.
  Videos are public content, so they are not encrypted; they are only served through signed addresses or a
  public link.
* **Uploads** come in parts of up to :data:`CHUNK_BYTES` (base64 in JSON, so every build's request handling and
  limits apply unchanged). The first part is checked by its leading bytes: MP4 and MOV (ISO base media), WebM
  and Matroska for video; JPEG, PNG and WebP for pictures; WebVTT for captions. A part must start where the last
  one ended, so an interrupted upload resumes from ``received``. Nothing is ever held whole in memory.
* **Space.** An upload is refused when it would leave less than ``AIRA_MEDIA_RESERVE_BYTES`` (2 GB) free on the
  drive, with a message naming ``AIRA_MEDIA_DIR``; the hosted build adds the organization's storage limit
  (:func:`set_quota`).
* **Playback** answers HTTP ranges (206) in pieces of at most :data:`RANGE_BYTES`, so seeking works and a request
  never reads more than that.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aira.suite.videos import store

MB = 1024 * 1024
GB = 1024 * MB
CHUNK_BYTES = 8 * MB
RANGE_BYTES = 4 * MB
KINDS = ("video", "image", "captions")
DEFAULT_LIMITS = {"video": 2 * GB, "image": 10 * MB, "captions": 1 * MB}
DEFAULT_RESERVE = 2 * GB
OPEN_UPLOADS = 5  # unfinished uploads one person may have at a time
STALE_HOURS = 48  # an upload nobody touched for this long is removed
EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/vtt": ".vtt",
}
# What plays where: Aira serves files as they were uploaded.
PLAYBACK = {
    "video/mp4": "Plays in every modern browser (H.264 video with AAC sound).",
    "video/webm": "Plays in Chrome, Edge and Firefox, and in recent Safari.",
    "video/quicktime": "Plays in Safari and usually in Chrome when the video is H.264; MP4 is safer.",
    "video/x-matroska": "May not play in a browser; convert it to MP4 or WebM for everyone to watch.",
}

RootFn = Callable[[], Path]
_root_default: RootFn | None = None
_root_provider: RootFn | None = None
# The hosted build: (videos connection, home, bytes wanted) -> raises when the organization's storage is full.
QuotaFn = Callable[[sqlite3.Connection, str, int], None]
_quota: QuotaFn | None = None
# Whether a refusal may name the folder (true on a person's own computer, not on a hosted server).
show_paths: bool = True


def set_default_root(provider: RootFn | None) -> None:
    global _root_default, _root_provider
    if _root_provider is None or _root_provider is _root_default:
        _root_provider = provider
    _root_default = provider


def set_root(provider: RootFn | None) -> None:
    global _root_provider
    _root_provider = provider


def reset_root() -> None:
    global _root_provider
    _root_provider = _root_default


def set_quota(fn: QuotaFn | None) -> None:
    global _quota
    _quota = fn


def root() -> Path:
    configured = os.getenv("AIRA_MEDIA_DIR", "").strip()
    if configured:
        return Path(configured)
    provider = _root_provider or _root_default
    if provider is None:
        raise RuntimeError("Videos is not set up on this server (no media folder).")
    return Path(provider())


def _env_bytes(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return max(0, value)


def max_bytes(kind: str) -> int:
    if kind == "video":
        return _env_bytes("AIRA_VIDEO_MAX_BYTES", DEFAULT_LIMITS["video"])
    return DEFAULT_LIMITS[kind]


def reserve_bytes() -> int:
    return _env_bytes("AIRA_MEDIA_RESERVE_BYTES", DEFAULT_RESERVE)


def human(size: int) -> str:
    if size >= GB:
        return f"{size / GB:.1f} GB"
    if size >= MB:
        return f"{size / MB:.0f} MB"
    return f"{max(1, size // 1024)} KB"


def free_bytes(target: Path) -> int:
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free)


def check_space(size: int) -> None:
    """Refuse an upload that would leave less than the reserve free on the media drive."""
    target = root()
    free = free_bytes(target)
    reserve = reserve_bytes()
    if free - size < reserve:
        where = f" in {target}" if show_paths else ""
        raise ValueError(
            f"There is not enough free disk space for this upload: {human(free)} is free{where} and Aira keeps "
            f"{human(reserve)} free. Set AIRA_MEDIA_DIR to a folder on a drive with more space, then restart Aira."
        )


# ---------------------------------------------------------------------------
# what a file is, by its first bytes
# ---------------------------------------------------------------------------


def sniff(head: bytes, kind: str) -> str:
    """The media type of a file from its leading bytes, or ValueError when it is not what ``kind`` allows."""
    if kind == "video":
        if len(head) >= 12 and head[4:8] == b"ftyp":
            return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
        if head[:4] == b"\x1a\x45\xdf\xa3":
            return "video/webm" if b"webm" in head[:64] else "video/x-matroska"
        raise ValueError(
            "This file is not a video Aira can keep: use MP4, MOV or WebM."
        )
    if kind == "image":
        if head[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        raise ValueError("This picture is not a JPEG, PNG or WebP image.")
    if kind == "captions":
        text = head[3:] if head[:3] == b"\xef\xbb\xbf" else head
        if text[:6] == b"WEBVTT":
            return "text/vtt"
        raise ValueError("Captions must be a WebVTT file (it starts with WEBVTT).")
    raise ValueError("Choose what you are uploading: a video, a picture or captions.")


def _boxes(data: bytes, start: int = 0, end: int | None = None):  # noqa: ANN202 - a generator of (type, body)
    end = len(data) if end is None else end
    at = start
    while at + 8 <= end:
        size, name = struct.unpack(">I4s", data[at : at + 8])
        header = 8
        if size == 1 and at + 16 <= end:
            size = struct.unpack(">Q", data[at + 8 : at + 16])[0]
            header = 16
        elif size == 0:
            size = end - at
        if size < header or at + size > end:
            return
        yield name, data[at + header : at + size]
        at += size


def mp4_info(path: Path) -> dict[str, Any]:
    """Length and picture size of an MP4/MOV, read from its ``moov`` box (best effort; {} when unknown).

    A phone's portrait video is often stored landscape with a rotation in the track matrix: the size is turned."""
    try:
        with path.open("rb") as handle:
            total = path.stat().st_size
            at = 0
            moov = b""
            while at + 8 <= total:
                handle.seek(at)
                head = handle.read(16)
                size, name = struct.unpack(">I4s", head[:8])
                header = 8
                if size == 1:
                    size = struct.unpack(">Q", head[8:16])[0]
                    header = 16
                elif size == 0:
                    size = total - at
                if size < header:
                    return {}
                if name == b"moov":
                    if size > 64 * MB:
                        return {}
                    handle.seek(at + header)
                    moov = handle.read(size - header)
                    break
                at += size
    except (OSError, struct.error):
        return {}
    info: dict[str, Any] = {}
    for name, body in _boxes(moov):
        if name == b"mvhd" and len(body) >= 20:
            if body[0] == 1 and len(body) >= 32:
                scale, duration = struct.unpack(">IQ", body[20:32])
            else:
                scale, duration = struct.unpack(">II", body[12:20])
            if scale:
                info["duration"] = round(duration / scale, 3)
        if name == b"trak" and "width" not in info:
            for inner, tkhd in _boxes(body):
                if inner != b"tkhd" or not tkhd:
                    continue
                offset = 4 + (32 if tkhd[0] == 1 else 20) + 8 + 8
                if len(tkhd) < offset + 36 + 8:
                    continue
                matrix = struct.unpack(">9i", tkhd[offset : offset + 36])
                width, height = struct.unpack(">II", tkhd[offset + 36 : offset + 44])
                width, height = width >> 16, height >> 16
                if width and height:
                    if matrix[0] == 0 and matrix[4] == 0:
                        width, height = height, width
                    info["width"], info["height"] = width, height
    return info


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------


def _part(upload_id: str) -> Path:
    return root() / "uploads" / f"{upload_id}.part"


def _upload_view(found: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": found["id"],
        "kind": found["kind"],
        "filename": found["filename"],
        "size": found["size"],
        "received": found["received"],
        "mime": found["mime"],
        "chunk_bytes": CHUNK_BYTES,
        "done": found["received"] >= found["size"],
    }


def _sweep(sc: sqlite3.Connection) -> None:
    """Remove uploads nobody finished within two days."""
    for stale in store.rows(
        sc,
        "SELECT id FROM vi_uploads WHERE updated_at < ?",
        (store.later(-STALE_HOURS),),
    ):
        try:
            _part(stale["id"]).unlink(missing_ok=True)
        except OSError:
            continue
        sc.execute("DELETE FROM vi_uploads WHERE id = ?", (stale["id"],))


def start(
    sc: sqlite3.Connection,
    owner_key: str,
    home: str,
    kind: str,
    filename: str,
    size: Any,
) -> dict[str, Any]:
    """Begin an upload of ``size`` bytes; the parts follow with :func:`append`."""
    if kind not in KINDS:
        raise ValueError(
            "Choose what you are uploading: a video, a picture or captions."
        )
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise ValueError("Send the file's size in bytes.") from None
    limit = max_bytes(kind)
    if size < 1:
        raise ValueError("This file is empty.")
    if size > limit:
        raise ValueError(
            f"This file is too large: the most Aira keeps is {human(limit)}."
        )
    _sweep(sc)
    open_count = store.scalar(
        sc, "SELECT COUNT(*) FROM vi_uploads WHERE owner_key = ?", (owner_key,)
    )
    if int(open_count or 0) >= OPEN_UPLOADS:
        raise PermissionError("Finish or cancel your other uploads first.")
    check_space(size)
    if _quota is not None:
        _quota(sc, home, size)
    upload_id = store.new_id()
    part = _part(upload_id)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"")
    moment = store.stamp()
    name = "".join(ch for ch in str(filename or "")[:120] if ch.isprintable())
    sc.execute(
        """INSERT INTO vi_uploads (id, owner_key, home, kind, filename, size, received, mime, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, '', ?, ?)""",
        (upload_id, owner_key, home, kind, name, size, moment, moment),
    )
    sc.commit()
    return upload(sc, upload_id, owner_key)


def upload(sc: sqlite3.Connection, upload_id: str, owner_key: str) -> dict[str, Any]:
    found = store.row(
        sc,
        "SELECT * FROM vi_uploads WHERE id = ? AND owner_key = ?",
        (store.check_id(upload_id, "upload"), owner_key),
    )
    if not found:
        raise LookupError("That upload was not found. Start it again.")
    return _upload_view(found)


def append(
    sc: sqlite3.Connection, upload_id: str, owner_key: str, offset: Any, data: Any
) -> dict[str, Any]:
    """Write one part. ``offset`` must be where the last part ended (``received``)."""
    found = store.row(
        sc,
        "SELECT * FROM vi_uploads WHERE id = ? AND owner_key = ?",
        (store.check_id(upload_id, "upload"), owner_key),
    )
    if not found:
        raise LookupError("That upload was not found. Start it again.")
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        raise ValueError("Send where this part starts (offset).") from None
    if offset != found["received"]:
        raise ValueError(f"Send the part that starts at byte {found['received']}.")
    if not isinstance(data, str) or not data:
        raise ValueError("Send the part as base64 text (data).")
    if len(data) > (CHUNK_BYTES * 4) // 3 + 8:
        raise ValueError(f"A part is at most {human(CHUNK_BYTES)}.")
    try:
        chunk = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("This part is not valid base64.") from None
    if not chunk or len(chunk) > CHUNK_BYTES:
        raise ValueError(f"A part is between 1 byte and {human(CHUNK_BYTES)}.")
    if offset + len(chunk) > found["size"]:
        raise ValueError("This part goes past the end of the file.")
    mime = found["mime"]
    if offset == 0:
        mime = sniff(chunk[:64], found["kind"])
    part = _part(found["id"])
    if not part.exists():
        raise LookupError("That upload was not found. Start it again.")
    with part.open("r+b") as handle:
        handle.seek(offset)
        handle.write(chunk)
        handle.truncate()
    sc.execute(
        "UPDATE vi_uploads SET received = ?, mime = ?, updated_at = ? WHERE id = ?",
        (offset + len(chunk), mime, store.stamp(), found["id"]),
    )
    sc.commit()
    return upload(sc, found["id"], owner_key)


def cancel(sc: sqlite3.Connection, upload_id: str, owner_key: str) -> None:
    upload(sc, upload_id, owner_key)
    _part(upload_id).unlink(missing_ok=True)
    sc.execute("DELETE FROM vi_uploads WHERE id = ?", (upload_id,))
    sc.commit()


def complete(sc: sqlite3.Connection, upload_id: str, owner_key: str) -> dict[str, Any]:
    """Turn a finished upload into a file (checked again from its first bytes) and return it."""
    found = store.row(
        sc,
        "SELECT * FROM vi_uploads WHERE id = ? AND owner_key = ?",
        (store.check_id(upload_id, "upload"), owner_key),
    )
    if not found:
        raise LookupError("That upload was not found. Start it again.")
    if found["received"] != found["size"]:
        raise ValueError(
            f"The upload is not finished: {found['received']} of {found['size']} bytes arrived."
        )
    part = _part(found["id"])
    with part.open("rb") as handle:
        mime = sniff(handle.read(64), found["kind"])
    if part.stat().st_size != found["size"]:
        raise ValueError("The upload is damaged. Start it again.")
    file_id = store.new_id()
    relative = Path("files") / file_id[:2] / f"{file_id}{EXTENSIONS[mime]}"
    target = root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, target)
    info = mp4_info(target) if mime in ("video/mp4", "video/quicktime") else {}
    sc.execute(
        """INSERT INTO vi_files (id, owner_key, home, kind, mime, size, path, duration, width, height, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            file_id,
            owner_key,
            found["home"],
            found["kind"],
            mime,
            found["size"],
            relative.as_posix(),
            info.get("duration"),
            info.get("width"),
            info.get("height"),
            store.stamp(),
        ),
    )
    sc.execute("DELETE FROM vi_uploads WHERE id = ?", (found["id"],))
    sc.commit()
    return file_view(file_row(sc, file_id))


def file_row(sc: sqlite3.Connection, file_id: str) -> dict[str, Any]:
    found = store.row(
        sc, "SELECT * FROM vi_files WHERE id = ?", (store.check_id(file_id, "file"),)
    )
    if not found:
        raise LookupError("That file was not found.")
    return found


def file_view(found: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": found["id"],
        "kind": found["kind"],
        "mime": found["mime"],
        "size": found["size"],
        "duration": found["duration"],
        "width": found["width"],
        "height": found["height"],
        "playback": PLAYBACK.get(found["mime"], ""),
    }


def owned_file(
    sc: sqlite3.Connection, file_id: str, owner_key: str, kind: str
) -> dict[str, Any]:
    """A file this owner uploaded, of this kind (a video's picture must be a picture)."""
    found = file_row(sc, file_id)
    if found["owner_key"] != owner_key:
        raise LookupError("That file was not found.")
    if found["kind"] != kind:
        raise ValueError(
            f"That file is not a {'picture' if kind == 'image' else kind}."
        )
    return found


def delete_file(sc: sqlite3.Connection, file_id: str) -> None:
    if not file_id:
        return
    found = store.row(sc, "SELECT * FROM vi_files WHERE id = ?", (file_id,))
    if not found:
        return
    try:
        (root() / found["path"]).unlink(missing_ok=True)
    except OSError:
        pass  # the row goes; a file left behind is only disk space
    sc.execute("DELETE FROM vi_files WHERE id = ?", (file_id,))


ORPHAN_HOURS = 24  # a finished video file, picture or captions file that no video uses after this long is removed


def sweep_orphans(sc: sqlite3.Connection, hours: float = ORPHAN_HOURS) -> int:
    """Remove people's finished files that never became part of a video (the form was refused after the upload,
    or the tab closed before the last step). Only files of Videos channels: an advertiser's files belong to Ads."""
    found = store.rows(
        sc,
        """SELECT f.id FROM vi_files f WHERE f.created_at < ?
        AND f.owner_key IN (SELECT id FROM vi_channels)
        AND NOT EXISTS (SELECT 1 FROM vi_videos v WHERE v.file_id = f.id OR v.thumb_file_id = f.id
            OR v.captions_file_id = f.id)""",
        (store.later(-hours),),
    )
    for item in found:
        delete_file(sc, item["id"])
    if found:
        sc.commit()
    return len(found)


def delete_owner(sc: sqlite3.Connection, owner_key: str) -> int:
    """Every file and unfinished upload of one owner (a person or an advertiser leaving)."""
    files = store.rows(sc, "SELECT id FROM vi_files WHERE owner_key = ?", (owner_key,))
    for item in files:
        delete_file(sc, item["id"])
    for item in store.rows(
        sc, "SELECT id FROM vi_uploads WHERE owner_key = ?", (owner_key,)
    ):
        _part(item["id"]).unlink(missing_ok=True)
        sc.execute("DELETE FROM vi_uploads WHERE id = ?", (item["id"],))
    return len(files)


def usage_bytes(sc: sqlite3.Connection, home: str) -> int:
    """Bytes an account keeps here (finished files and uploads in progress)."""
    kept = store.scalar(
        sc, "SELECT COALESCE(SUM(size), 0) FROM vi_files WHERE home = ?", (home,)
    )
    pending = store.scalar(
        sc, "SELECT COALESCE(SUM(size), 0) FROM vi_uploads WHERE home = ?", (home,)
    )
    return int(kept or 0) + int(pending or 0)


# ---------------------------------------------------------------------------
# playback
# ---------------------------------------------------------------------------


def read_range(
    found: dict[str, Any], header: str
) -> tuple[int, bytes, tuple[tuple[str, str], ...]]:
    """(status, body, headers) for ``Range: bytes=...``: 206 with at most RANGE_BYTES, 200 for a small whole
    file, 416 for a range outside the file."""
    path = root() / found["path"]
    try:
        size = path.stat().st_size
    except OSError:
        raise LookupError("That file is no longer available.") from None
    start, end = 0, size - 1
    partial = False
    text = str(header or "").strip().lower()
    if text.startswith("bytes="):
        first = text[6:].split(",", 1)[0].strip()
        low, _, high = first.partition("-")
        try:
            if low == "":
                start = max(0, size - int(high))
            else:
                start = int(low)
                end = int(high) if high else size - 1
        except ValueError:
            start, end = size, size
        if start >= size or start > end:
            return (
                416,
                b"",
                (("Content-Range", f"bytes */{size}"), ("Accept-Ranges", "bytes")),
            )
        partial = True
    elif size > RANGE_BYTES:
        partial = True
    end = min(end, size - 1, start + RANGE_BYTES - 1)
    with path.open("rb") as handle:
        handle.seek(start)
        body = handle.read(end - start + 1)
    headers: tuple[tuple[str, str], ...] = (("Accept-Ranges", "bytes"),)
    if partial:
        headers += (("Content-Range", f"bytes {start}-{start + len(body) - 1}/{size}"),)
        return 206, body, headers
    return 200, body, headers


# ---------------------------------------------------------------------------
# signed addresses (a <video> or <img> cannot send the person's headers, so the address carries the permission)
# ---------------------------------------------------------------------------


def _key(sc: sqlite3.Connection) -> bytes:
    secret = store.setting(sc, "signing_key", "")
    if not secret:
        secret = secrets.token_hex(32)
        store.set_setting(sc, "signing_key", secret)
        sc.commit()
    return bytes.fromhex(secret)


def sign(sc: sqlite3.Connection, value: str) -> str:
    return hmac.new(_key(sc), value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def signed_url(
    sc: sqlite3.Connection,
    file_id: str,
    prefix: str = "/api/videos/media",
    hours: int = 6,
) -> str:
    """An address that plays this file for ``hours`` (rounded to the hour, so the address stays the same and
    the browser can reuse what it fetched)."""
    if not file_id:
        return ""
    expires = (int(time.time()) // 3600 + 1 + hours) * 3600
    return f"{prefix}/{file_id}?e={expires}&s={sign(sc, f'{file_id}:{expires}')}"


def verify(sc: sqlite3.Connection, file_id: str, expires: str, signature: str) -> None:
    try:
        when = int(expires)
    except (TypeError, ValueError):
        raise LookupError("That file was not found.") from None
    if when < time.time() or not hmac.compare_digest(
        sign(sc, f"{file_id}:{when}"), str(signature or "")
    ):
        raise LookupError("That file was not found.")
