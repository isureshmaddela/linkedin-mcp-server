"""Helpers for the Videos and Ads tests: tiny media files made in the test, a clock to move, and uploads.

make_mp4(seconds=12, width=720, height=1280)  # an ISO base media file with ftyp, moov (mvhd, trak/tkhd) and mdat
make_webm()                                    # the start of a WebM file (EBML header, DocType webm)
clock = Clock(monkeypatch)                     # Videos' and Ads' "now"; clock.advance(seconds=31)
file_id = upload(person, "video", make_mp4())  # the resumable upload over HTTP, in parts
"""

from __future__ import annotations

import base64
import struct
from datetime import datetime, timedelta, timezone
from typing import Any

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
VTT = b"WEBVTT\n\n00:00.000 --> 00:02.000\nHello\n"


def _box(name: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), name) + body


def make_mp4(
    seconds: float = 12,
    width: int = 720,
    height: int = 1280,
    *,
    rotate: bool = False,
    payload: int = 2048,
) -> bytes:
    """A small MP4 whose header says how long it is and how big the picture is (not a playable film)."""
    scale = 1000
    mvhd = (
        struct.pack(">I", 0)
        + struct.pack(">IIII", 0, 0, scale, int(seconds * scale))
        + b"\x00" * 80
    )
    matrix = (
        (0, 65536, 0, -65536, 0, 0, 0, 0, 1 << 30)
        if rotate
        else (65536, 0, 0, 0, 65536, 0, 0, 0, 1 << 30)
    )
    tkhd = (
        struct.pack(">I", 0)
        + struct.pack(">IIIII", 0, 0, 1, 0, int(seconds * scale))
        + b"\x00" * 8
        + b"\x00" * 8
        + struct.pack(">9i", *matrix)
        + struct.pack(">II", width << 16, height << 16)
    )
    moov = _box(b"moov", _box(b"mvhd", mvhd) + _box(b"trak", _box(b"tkhd", tkhd)))
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    return ftyp + moov + _box(b"mdat", bytes(range(256)) * (payload // 256))


def make_webm(payload: int = 4096) -> bytes:
    header = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82\x84webm\x42\x87\x81\x02\x42\x85\x81\x02"
    return header + b"\x00" * payload


class Clock:
    """Videos' and Ads' idea of now (aira.suite.videos.store.utcnow), moved by the test."""

    def __init__(self, monkeypatch: Any, start: datetime | None = None) -> None:
        from aira.suite.videos import store

        # noon today: a whole day's pacing either side, whatever time the tests run
        self.now = start or datetime.now(timezone.utc).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        monkeypatch.setattr(store, "utcnow", self)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now = self.now + timedelta(**delta)
        return self.now


def upload(
    person: Any,
    kind: str,
    data: bytes,
    *,
    base: str = "/api/videos",
    chunk: int = 0,
    name: str = "",
) -> str:
    """Upload ``data`` in parts through the API as ``person`` (anything with ``call``); returns the file id."""
    status, started = person.call(
        "POST",
        f"{base}/uploads",
        {"kind": kind, "filename": name or f"test.{kind}", "size": len(data)},
    )
    assert status == 201, started
    upload_id = started["upload"]["id"]
    size = chunk or started["upload"]["chunk_bytes"]
    offset = 0
    while offset < len(data):
        part = data[offset : offset + size]
        status, body = person.call(
            "POST",
            f"{base}/uploads/{upload_id}/chunk",
            {"offset": offset, "data": base64.b64encode(part).decode()},
        )
        assert status == 200, body
        offset += len(part)
    status, done = person.call("POST", f"{base}/uploads/{upload_id}/complete", {})
    assert status == 201, done
    return done["file"]["id"]
