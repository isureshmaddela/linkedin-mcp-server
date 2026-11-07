"""Videos safety: where a video was filmed is taken out, media addresses are checked again when they are used,
and a teen's video stays private until a grown-up of their household says yes."""

from __future__ import annotations

import struct

import pytest
from videos_support import PNG, make_mp4

import test_suite_videos
from aira.suite.videos import media, service

sc = test_suite_videos.sc  # shared fixtures
_no_model = test_suite_videos._no_model
_person = test_suite_videos._person
_file = test_suite_videos._file
_video = test_suite_videos._video

GPS = b"+51.5007-000.1246+011.000/"


def _box(name: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), name) + body


def _with_location() -> bytes:
    """An MP4 that says where it was filmed three ways: QuickTime's ©xyz in udta, Apple's metadata keys, XMP."""
    plain = make_mp4(30, 1280, 720)
    ftyp_size = struct.unpack(">I", plain[:4])[0]
    moov_size = struct.unpack(">I", plain[ftyp_size : ftyp_size + 4])[0]
    moov_body = plain[ftyp_size + 8 : ftyp_size + moov_size]
    xyz = _box(b"\xa9xyz", struct.pack(">HH", len(GPS), 0) + GPS)
    key = b"com.apple.quicktime.location.ISO6709"
    keys = _box(
        b"keys",
        b"\x00" * 4
        + struct.pack(">I", 1)
        + struct.pack(">I", 8 + len(key))
        + b"mdta"
        + key,
    )
    item = _box(struct.pack(">I", 1), _box(b"data", struct.pack(">II", 1, 0) + GPS))
    hdlr = _box(b"hdlr", b"\x00" * 8 + b"mdta" + b"\x00" * 13)
    meta = _box(b"meta", b"\x00" * 4 + hdlr + keys + _box(b"ilst", item))
    xmp = _box(b"uuid", media.XMP_UUID + b"<x:xmpmeta>GPSLatitude 51.5</x:xmpmeta>")
    moov = _box(b"moov", moov_body + _box(b"udta", xyz) + meta + xmp)
    return plain[:ftyp_size] + moov + plain[ftyp_size + moov_size :]


def test_where_a_video_was_filmed_is_taken_out(sc, tmp_path):
    data = _with_location()
    assert GPS in data and b"GPSLatitude" in data
    path = tmp_path / "clip.mp4"
    path.write_bytes(data)
    assert media.strip_location(path) == 3
    cleaned = path.read_bytes()
    assert len(cleaned) == len(data)  # blanked in place: every offset still holds
    assert GPS not in cleaned and b"GPSLatitude" not in cleaned
    assert media.mp4_info(path) == {"duration": 30.0, "width": 1280, "height": 720}
    assert media.strip_location(path) == 0
    # an upload is cleaned on the way in
    pat = _person(sc, "p:pat", "Pat")
    found = _file(sc, pat, data)
    stored = (media.root() / media.file_row(sc, found["id"])["path"]).read_bytes()
    assert GPS not in stored and len(stored) == len(data)


def test_a_thumbnail_loses_its_metadata(sc):
    from test_suite_social_plus import png

    pat = _person(sc, "p:pat", "Pat")
    picture = png(b"GPS\x0051.5N 0.1W")
    assert b"51.5N" in picture
    found = _file(sc, pat, picture, kind="image")
    stored = (media.root() / media.file_row(sc, found["id"])["path"]).read_bytes()
    assert b"51.5N" not in stored and stored.startswith(PNG[:8])


def test_media_addresses_are_checked_again_when_used(sc):
    pat = _person(sc, "p:pat", "Pat")
    sam = _person(sc, "p:sam", "Sam", home="home-3")
    video = _video(sc, pat, visibility="public")
    seen = service.video_view(sc, sam, service._video_row(sc, video["id"]), full=True)
    url = seen["stream_url"]
    assert f"&v={sam['id']}&" in url
    file_id = service._video_row(sc, video["id"])["file_id"]
    query = dict(part.split("=") for part in url.split("?", 1)[1].split("&"))
    media.verify(sc, file_id, query["e"], query["s"], query["v"])
    with pytest.raises(LookupError):
        media.verify(
            sc, file_id, query["e"], query["s"], pat["id"]
        )  # someone else's address
    assert service.may_serve(sc, file_id, sam["id"])
    # made private, taken down or deleted after the address was made: it stops playing
    service.edit_video(sc, pat, video["id"], {"visibility": "private"})
    assert not service.may_serve(sc, file_id, sam["id"])
    assert service.may_serve(sc, file_id, pat["id"])  # the owner still watches it
    service.edit_video(sc, pat, video["id"], {"visibility": "public"})
    sc.execute(
        "UPDATE vi_videos SET takedown_at = '2026-01-01T00:00:00Z' WHERE id = ?",
        (video["id"],),
    )
    assert not service.may_serve(sc, file_id, sam["id"])
    assert not service.may_serve(sc, file_id, "")
    sc.execute("UPDATE vi_videos SET takedown_at = NULL WHERE id = ?", (video["id"],))
    assert service.may_serve(sc, file_id, "")
    # a file no video uses yet (a fresh upload) follows its address
    assert service.may_serve(sc, "f" * 32, sam["id"])


def test_a_teens_video_waits_for_a_grown_up_before_it_is_public(sc):
    mum = _person(sc, "p:mum", "Mum", home="home-2")
    teo = _person(sc, "p:teo", "Teo", age="teen", home="home-2")
    sam = _person(sc, "p:sam", "Sam", home="home-3")
    # private unless chosen otherwise; rated at least for teens, never "made for kids"
    quiet = _video(sc, teo)
    assert quiet["visibility"] == "private" and quiet["rating"] == "teen"
    kids = _video(sc, teo, rating="everyone", visibility="unlisted")
    assert (
        kids["rating"] == "teen"
        and not kids["for_kids"]
        and kids["visibility"] == "unlisted"
    )
    asked = _video(sc, teo, visibility="public", title="My skate park")
    assert asked["visibility"] == "private" and asked["public_pending"] is True
    [waiting] = service.public_requests(sc, mum)
    assert waiting["video"]["id"] == asked["id"]
    # Mum may watch it first; nobody else may
    row_ = service._video_row(sc, asked["id"])
    assert service.can_watch(sc, mum, row_) and not service.can_watch(sc, sam, row_)
    assert service.public_requests(sc, sam) == []
    with pytest.raises(LookupError):
        service.answer_public(sc, sam, asked["id"], True)
    assert service.answer_public(sc, mum, asked["id"], True)["visibility"] == "public"
    assert service._video_row(sc, asked["id"])["visibility"] == "public"
    assert service.public_requests(sc, mum) == []
    # once public it stays public through edits; another video asks again
    edited = service.edit_video(sc, teo, asked["id"], {"title": "Skate park"})
    assert edited["visibility"] == "public"
    later = service.edit_video(sc, teo, quiet["id"], {"visibility": "public"})
    assert later["visibility"] == "private" and later["public_pending"]
    service.answer_public(sc, mum, quiet["id"], False)
    assert service._video_row(sc, quiet["id"])["visibility"] == "private"
    # grown-ups pick freely
    adult = _video(sc, mum, rating="everyone", visibility="public")
    assert adult["visibility"] == "public" and adult["rating"] == "everyone"
