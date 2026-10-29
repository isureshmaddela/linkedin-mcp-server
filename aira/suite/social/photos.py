"""Photos people upload to Social from their device: posts, stories, profile and cover pictures, group and page
pictures, Marketplace listings.

* **Where they live.** In the media folder Videos uses (:func:`aira.suite.videos.media.root`: ``AIRA_MEDIA_DIR``
  when it is set, else the folder the host chose), under ``social/<2 hex>/<id>.<ext>``. The database keeps only who
  uploaded it, its type and size (:data:`store.SCHEMA` ``so_media``).
* **What is kept.** JPEG, PNG and WebP, recognised by their first bytes (never by the name), at most
  :data:`MAX_BYTES`. Before a file is written, its metadata is taken out: EXIF (where and when the photo was taken,
  the camera), XMP, comments and text chunks. The browser also shrinks big photos before sending them.
* **Who sees them.** A post, story or listing stores a photo as ``media:<id>``; only its owner can attach it. Views
  turn it into a signed address (:func:`url`) for the person allowed to see that thing, the same way Videos signs
  its files: an ``<img>`` cannot send the person's headers, so the address carries the permission and lasts a few
  hours. A public link's page uses :func:`public_url`, which only answers for the photos that page shows.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

from aira.suite.social import store
from aira.suite.social.store import new_id, row, rows, stamp

MB = 1024 * 1024
MAX_BYTES = 8 * MB
MAX_SIDE = 12000  # pixels: a bigger "photo" is a trick, not a photo
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
REF = re.compile(r"^media:([0-9a-f]{32})$")
# A signed address the browser sends back when it edits something (a listing's photos, the profile picture).
ADDRESS = re.compile(r"^/api/social/photos/([0-9a-f]{32})(?:\?.*)?$")
ORPHAN_HOURS = 24
KEY_SETTING = "photo_key"
_keys: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# where the files are
# ---------------------------------------------------------------------------


def _root() -> Path:
    from aira.suite.videos import media

    return media.root()


def _path(found: dict[str, Any]) -> Path:
    return _root() / found["path"]


# ---------------------------------------------------------------------------
# what a picture is, its size, and its metadata taken out
# ---------------------------------------------------------------------------


def sniff(head: bytes) -> str:
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("This picture is not a JPEG, PNG or WebP image.")


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg(data: bytes) -> tuple[bytes, int, int]:
    """The JPEG without its APP1 (EXIF, XMP), APP3-APP13, APP15 and comment segments; its width and height."""
    out = bytearray(b"\xff\xd8")
    width = height = 0
    at = 2
    end = len(data)
    while at < end:
        if data[at] != 0xFF:
            raise ValueError("This JPEG picture is damaged.")
        while at < end and data[at] == 0xFF:
            at += 1
        if at >= end:
            break
        marker = data[at]
        at += 1
        if marker == 0xD9:
            out += b"\xff\xd9"
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            out += bytes((0xFF, marker))
            continue
        if at + 2 > end:
            raise ValueError("This JPEG picture is damaged.")
        length = int.from_bytes(data[at : at + 2], "big")
        if length < 2 or at + length > end:
            raise ValueError("This JPEG picture is damaged.")
        segment = data[at : at + length]
        if marker == 0xDA:  # start of scan: the picture itself, to the end
            out += b"\xff\xda" + data[at:]
            break
        at += length
        if marker in _SOF and length >= 7:
            height, width = struct.unpack(">HH", segment[3:7])
        keep = True
        if marker == 0xFE or marker == 0xE1 or 0xE3 <= marker <= 0xED or marker == 0xEF:
            keep = False
        if marker == 0xE2 and not segment[2:14].startswith(b"ICC_PROFILE"):
            keep = False
        if keep:
            out += bytes((0xFF, marker)) + segment
    return bytes(out), width, height


_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _png(data: bytes) -> tuple[bytes, int, int]:
    out = bytearray(data[:8])
    at = 8
    width = height = 0
    while at + 12 <= len(data):
        length = int.from_bytes(data[at : at + 4], "big")
        kind = data[at + 4 : at + 8]
        chunk_end = at + 12 + length
        if chunk_end > len(data):
            raise ValueError("This PNG picture is damaged.")
        if kind == b"IHDR" and length >= 8:
            width, height = struct.unpack(">II", data[at + 8 : at + 16])
        if kind not in _PNG_DROP:
            out += data[at:chunk_end]
        at = chunk_end
        if kind == b"IEND":
            break
    return bytes(out), width, height


def _webp(data: bytes) -> tuple[bytes, int, int]:
    chunks = []
    at = 12
    width = height = 0
    while at + 8 <= len(data):
        kind = data[at : at + 4]
        size = int.from_bytes(data[at + 4 : at + 8], "little")
        body = data[at + 8 : at + 8 + size]
        if len(body) != size:
            raise ValueError("This WebP picture is damaged.")
        at += 8 + size + (size & 1)
        if kind in (b"EXIF", b"XMP "):
            continue
        if kind == b"VP8X" and size >= 10:
            body = bytes((body[0] & ~0x0C,)) + body[1:]  # no EXIF or XMP any more
            width = 1 + int.from_bytes(body[4:7], "little")
            height = 1 + int.from_bytes(body[7:10], "little")
        elif kind == b"VP8 " and size >= 10 and not width:
            width = int.from_bytes(body[6:8], "little") & 0x3FFF
            height = int.from_bytes(body[8:10], "little") & 0x3FFF
        elif kind == b"VP8L" and size >= 5 and not width:
            bits = int.from_bytes(body[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        chunks.append(
            kind + size.to_bytes(4, "little") + body + (b"\x00" if size & 1 else b"")
        )
    payload = b"WEBP" + b"".join(chunks)
    return b"RIFF" + len(payload).to_bytes(4, "little") + payload, width, height


def clean(data: bytes) -> tuple[bytes, str, int, int]:
    """(the picture without its metadata, its type, width, height); ValueError when it is not a picture."""
    mime = sniff(data[:16])
    if mime == "image/jpeg":
        out, width, height = _jpeg(data)
    elif mime == "image/png":
        out, width, height = _png(data)
    else:
        out, width, height = _webp(data)
    if width > MAX_SIDE or height > MAX_SIDE:
        raise ValueError("This picture is too large: at most 12,000 pixels on a side.")
    return out, mime, width, height


# ---------------------------------------------------------------------------
# saving and finding photos
# ---------------------------------------------------------------------------


def save(sc: sqlite3.Connection, me_: dict[str, Any], data: Any) -> dict[str, Any]:
    """Keep one photo from base64 ``data`` for this account; returns {id, ref, url, width, height}."""
    from aira.suite.videos import media

    if not isinstance(data, str) or not data.strip():
        raise ValueError("Choose a photo to upload.")
    text = data.strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    if len(text) > (MAX_BYTES * 4) // 3 + 8:
        raise ValueError("This photo is too large: the most Monosage keeps is 8 MB.")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("This photo did not arrive whole. Try again.") from None
    if not raw:
        raise ValueError("This photo is empty.")
    if len(raw) > MAX_BYTES:
        raise ValueError("This photo is too large: the most Monosage keeps is 8 MB.")
    body, mime, width, height = clean(raw)
    media.check_space(len(body))
    sweep(sc)
    photo_id = new_id()
    relative = Path("social") / photo_id[:2] / f"{photo_id}{EXTENSIONS[mime]}"
    target = _root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    temporary.write_bytes(body)
    os.replace(temporary, target)
    sc.execute(
        """INSERT INTO so_media (id, owner_id, home, mime, size, width, height, path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            photo_id,
            me_["id"],
            me_.get("home") or "",
            mime,
            len(body),
            width,
            height,
            relative.as_posix(),
            stamp(),
        ),
    )
    sc.commit()
    return {
        "id": photo_id,
        "ref": f"media:{photo_id}",
        "url": url(photo_id, sc),
        "width": width,
        "height": height,
    }


def ref_id(value: Any) -> str:
    """The photo id of ``media:<id>`` or of a signed address this server made; "" for anything else."""
    text = str(value or "").strip()
    found = REF.match(text) or ADDRESS.match(text)
    return found.group(1) if found else ""


def owned(sc: sqlite3.Connection, value: Any, owner_id: str) -> str:
    """``media:<id>`` for a photo this account uploaded; LookupError for anyone else's (or a made-up one)."""
    photo_id = ref_id(value)
    found = (
        row(sc, "SELECT owner_id FROM so_media WHERE id = ?", (photo_id,))
        if photo_id
        else None
    )
    if not found or found["owner_id"] != owner_id:
        raise ValueError("That photo was not found. Upload it again.")
    return f"media:{photo_id}"


def is_ref(value: Any) -> bool:
    return bool(ref_id(value))


def file_row(sc: sqlite3.Connection, photo_id: str) -> dict[str, Any]:
    found = row(
        sc, "SELECT * FROM so_media WHERE id = ?", (store.check_id(photo_id, "photo"),)
    )
    if not found:
        raise LookupError("That photo was not found.")
    return found


def read(sc: sqlite3.Connection, photo_id: str) -> tuple[bytes, str]:
    found = file_row(sc, photo_id)
    try:
        return _path(found).read_bytes(), found["mime"]
    except OSError:
        raise LookupError("That photo is no longer available.") from None


def delete(sc: sqlite3.Connection, photo_id: str) -> None:
    found = row(sc, "SELECT * FROM so_media WHERE id = ?", (photo_id,))
    if not found:
        return
    try:
        _path(found).unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass  # the row goes; a file left behind is only disk space
    sc.execute("DELETE FROM so_media WHERE id = ?", (photo_id,))


def delete_owner(sc: sqlite3.Connection, owner_id: str) -> int:
    found = rows(sc, "SELECT id FROM so_media WHERE owner_id = ?", (owner_id,))
    for item in found:
        delete(sc, item["id"])
    return len(found)


_USES = (
    "SELECT 1 FROM so_posts WHERE photos LIKE ?",
    "SELECT 1 FROM so_stories WHERE photo = ?",
    "SELECT 1 FROM so_accounts WHERE avatar = ? OR cover = ?",
    "SELECT 1 FROM so_groups WHERE photo = ?",
    "SELECT 1 FROM so_pages WHERE photo = ?",
    "SELECT 1 FROM so_listings WHERE photos LIKE ?",
    "SELECT 1 FROM so_messages WHERE photo = ?",
    "SELECT 1 FROM so_dating_profiles WHERE photos LIKE ?",  # Dating's photos (aira.suite.social.dating)
)


def in_use(sc: sqlite3.Connection, photo_id: str) -> bool:
    ref = f"media:{photo_id}"
    for sql in _USES:
        params = tuple(
            f'%"{ref}"%' if "LIKE" in sql else ref for _ in range(sql.count("?"))
        )
        if sc.execute(f"{sql} LIMIT 1", params).fetchone():  # noqa: S608 - fixed statements
            return True
    return False


def sweep(sc: sqlite3.Connection, hours: float = ORPHAN_HOURS) -> int:
    """Remove photos uploaded more than a day ago that nothing uses (a post that was never sent)."""
    removed = 0
    for item in rows(
        sc,
        "SELECT id FROM so_media WHERE created_at < ? ORDER BY created_at LIMIT 200",
        (store.later(-hours),),
    ):
        if not in_use(sc, item["id"]):
            delete(sc, item["id"])
            removed += 1
    if removed:
        sc.commit()
    return removed


# ---------------------------------------------------------------------------
# signed addresses
# ---------------------------------------------------------------------------


def _key(sc: sqlite3.Connection | None = None) -> bytes:
    from aira.suite.social import db

    where = str(db.path())
    if where in _keys:
        return _keys[where]
    if sc is None:
        from contextlib import closing

        with closing(db.connect()) as fresh:
            secret = store.setting(fresh, KEY_SETTING, "")
    else:
        secret = store.setting(sc, KEY_SETTING, "")
    if not secret:
        raise RuntimeError("Social's photo key is missing.")
    _keys[where] = bytes.fromhex(secret)
    return _keys[where]


def forget() -> None:
    _keys.clear()


def _sign(value: str, sc: sqlite3.Connection | None = None) -> str:
    return hmac.new(_key(sc), value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _expires(hours: int = 6) -> int:
    # rounded to the hour: the address stays the same for a while and the browser reuses what it fetched
    return (int(time.time()) // 3600 + 1 + hours) * 3600


def url(photo_id: str, sc: sqlite3.Connection | None = None) -> str:
    expires = _expires()
    return f"/api/social/photos/{photo_id}?e={expires}&s={_sign(f'{photo_id}:{expires}', sc)}"


def public_url(token: str, photo_id: str) -> str:
    return f"/api/public/{token}/social-photo/{photo_id}"


def verify(
    photo_id: str, expires: str, signature: str, sc: sqlite3.Connection | None = None
) -> None:
    try:
        when = int(expires)
    except (TypeError, ValueError):
        raise LookupError("That photo was not found.") from None
    if when < time.time() or not hmac.compare_digest(
        _sign(f"{photo_id}:{when}", sc), str(signature or "")
    ):
        raise LookupError("That photo was not found.")


def display(
    value: Any, *, token: str = "", sc: sqlite3.Connection | None = None
) -> str:
    """What a browser loads for a stored picture: an https link as it is, ``media:<id>`` as a signed address
    (or, on a public link's page, the address that page answers for)."""
    text = str(value or "")
    photo_id = ref_id(text) if text.startswith("media:") else ""
    if not photo_id:
        return text
    return public_url(token, photo_id) if token else url(photo_id, sc)


def display_list(
    values: Any, *, token: str = "", sc: sqlite3.Connection | None = None
) -> list[str]:
    return [display(item, token=token, sc=sc) for item in (values or [])]
