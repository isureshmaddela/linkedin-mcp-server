"""Academy lesson files (aira/suite/academy/files.py): videos, audio and PDFs uploaded to a lesson, kept in the media
store of Videos and served only through signed, expiring addresses that check the enrolment again on every request;
external video links stay but are flagged. Unit tests, the single-user server and the hosted gateway."""

from __future__ import annotations

import base64
import json
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest
from cloud_support import Browser, build_org, sign_in
from videos_support import make_mp4, make_webm, upload

import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.academy import files as lesson_files
from aira.suite.academy import service, store
from aira.suite.videos import db as videos_db
from aira.suite.videos import media

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + bytes(2000)
WAV = (
    b"RIFF"
    + struct.pack("<I", 1036)
    + b"WAVEfmt "
    + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
    + b"data"
    + struct.pack("<I", 1000)
    + bytes(1000)
)
MKV = (
    b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82\x88matroska"
    + bytes(512)
)
HOME = "local:test"


@pytest.fixture(autouse=True)
def no_model():
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "academy.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    import auto_apply_assistant as local

    public.set_index(local._public_link_index)


def build(db, *, price=4900):
    course = service.create_course(db, {"title": "Guitar", "price_cents": price})
    module = service.add_module(db, course["id"], "Start")
    preview = service.add_lesson(
        db, module["id"], {"title": "Tuning", "body": "Free.", "preview": True}
    )
    paid = service.add_lesson(db, module["id"], {"title": "Chords", "body": "Paid."})
    service.set_status(db, course["id"], "published")
    return course, module, preview, paid


def put(db, lesson_id, kind, name, data, home=HOME):
    """Upload ``data`` to a lesson in one part, in process; returns the file as the editor sees it."""
    with closing(videos_db.connect()) as sc:
        started = lesson_files.start(db, sc, home, lesson_id, kind, name, len(data))
        media.append(
            sc,
            started["id"],
            lesson_files.owner_key(home),
            0,
            base64.b64encode(data).decode(),
        )
        return lesson_files.attach(db, sc, home, lesson_id, started["id"])


def on_disk(file_id):
    """The media file's place on the disk (None once it is gone from the media store)."""
    with closing(videos_db.connect()) as sc:
        found = sc.execute(
            "SELECT path FROM vi_files WHERE id = ?", (file_id,)
        ).fetchone()
    return (media.root() / found["path"]) if found else None


def media_id(db, lesson_file_id):
    return db.execute(
        "SELECT file_id FROM ac_lesson_files WHERE id = ?", (lesson_file_id,)
    ).fetchone()[0]


def signed(url):
    """(file id, e, s) of a signed lesson-file address."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return parts.path.rsplit("/", 1)[1], query["e"][0], query["s"][0]


def visit(db, link, url):
    file_id, expires, signature = signed(url)
    with closing(videos_db.connect()) as sc:
        return lesson_files.for_visitor(db, sc, link, file_id, expires, signature)


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_uploads_check_the_kind_the_name_the_content_and_the_size(db, monkeypatch):
    _, _, preview, paid = build(db)
    with closing(videos_db.connect()) as sc:
        refusals = [
            ("image", "photo.jpg", 100, "Choose what you are uploading"),
            ("document", "notes.exe", 100, "does not end like a PDF"),
            ("audio", "song.pdf", 100, "does not end like audio"),
            ("video", "clip.mkv", 100, "does not end like a video"),
            ("document", "notes.pdf", 50 * media.MB + 1, "too large"),
            ("audio", "talk.mp3", 500 * media.MB + 1, "too large"),
            ("document", "notes.pdf", 0, "empty"),
        ]
        for kind, name, size, message in refusals:
            with pytest.raises(ValueError, match=message):
                lesson_files.start(db, sc, HOME, paid["id"], kind, name, size)
        with pytest.raises(LookupError):
            lesson_files.start(db, sc, HOME, "0" * 32, "document", "a.pdf", 10)
        # the content decides, whatever the name says
        started = lesson_files.start(
            db, sc, HOME, paid["id"], "document", "fake.pdf", 20
        )
        with pytest.raises(ValueError, match="not a PDF"):
            media.append(
                sc,
                started["id"],
                lesson_files.owner_key(HOME),
                0,
                base64.b64encode(b"<html>not a pdf</html>"[:20]).decode(),
            )
        media.cancel(sc, started["id"], lesson_files.owner_key(HOME))

    pdf = put(db, paid["id"], "document", "Chord chart.pdf", PDF)
    mp3 = put(db, preview["id"], "audio", "tuning.mp3", MP3)
    wav = put(db, preview["id"], "audio", "drone.wav", WAV)
    webm = put(db, paid["id"], "video", "strum.webm", make_webm())
    mp4 = put(db, paid["id"], "video", "Lesson 2.mp4", make_mp4(seconds=42))
    assert [pdf["mime"], mp3["mime"], wav["mime"], webm["mime"], mp4["mime"]] == [
        "application/pdf",
        "audio/mpeg",
        "audio/wav",
        "video/webm",
        "video/mp4",
    ]
    assert mp4["duration"] == 42 and pdf["name"] == "Chord chart.pdf"
    assert pdf["url"].startswith(f"/api/academy/lesson-files/{pdf['id']}?e=")
    for item in (pdf, mp3, wav, webm, mp4):
        assert on_disk(media_id(db, item["id"])).exists()

    # a Matroska file plays in no browser: refused when it completes, and nothing is kept
    with pytest.raises(ValueError, match="cannot be played"):
        put(db, paid["id"], "video", "clip.webm", MKV)
    assert (
        db.execute(
            "SELECT COUNT(*) FROM ac_lesson_files WHERE lesson_id = ?", (paid["id"],)
        ).fetchone()[0]
        == 3
    )
    with closing(videos_db.connect()) as sc:
        kept = sc.execute(
            "SELECT COUNT(*) FROM vi_files WHERE owner_key = ?",
            (lesson_files.owner_key(HOME),),
        ).fetchone()[0]
    assert kept == 5

    monkeypatch.setattr(lesson_files, "MAX_PER_LESSON", 2)
    with closing(videos_db.connect()) as sc, pytest.raises(ValueError, match="2 files"):
        lesson_files.start(
            db, sc, HOME, preview["id"], "document", "more.pdf", len(PDF)
        )


def test_signed_addresses_are_checked_again_against_the_enrolment(db):
    course, _, preview, paid = build(db)
    pdf = put(db, paid["id"], "document", "chart.pdf", PDF)
    mp3 = put(db, preview["id"], "audio", "tuning.mp3", MP3)
    ana = service.enrol(db, course["id"], {"name": "Ana", "email": "ana@example.com"})
    ana_link = public.resolve(db, ana["link"]["token"])
    view = service.student_view(db, ana_link["ref_id"], ana_link["token"])
    lessons = {item["id"]: item for item in view["modules"][0]["lessons"]}
    # waiting for payment: the paid lesson is locked and carries no file; the free preview's file is there
    assert lessons[paid["id"]]["locked"] and "files" not in lessons[paid["id"]]
    preview_url = lessons[preview["id"]]["files"][0]["url"]
    assert preview_url.startswith(
        f"/api/public/{ana_link['token']}/lesson-file/{mp3['id']}?e="
    )
    assert "ana" not in preview_url.lower()  # no personal data in the address
    assert visit(db, ana_link, preview_url)["id"] == mp3["id"]
    # an address for the paid file, made for Ana, is refused until she has paid
    with closing(videos_db.connect()) as sc:
        early = lesson_files.address(
            sc,
            lesson_files.public_base(ana_link["token"]),
            pdf["id"],
            lesson_files.student_viewer(ana_link["ref_id"]),
        )
    with pytest.raises(PermissionError, match="payment"):
        visit(db, ana_link, early)

    service.mark_paid(db, ana_link["ref_id"])
    view = service.student_view(db, ana_link["ref_id"], ana_link["token"])
    url = next(
        item for item in view["modules"][0]["lessons"] if item["id"] == paid["id"]
    )["files"][0]["url"]
    assert visit(db, ana_link, url)["id"] == pdf["id"]

    file_id, expires, signature = signed(url)
    tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
    with pytest.raises(LookupError):
        visit(db, ana_link, tampered)
    with pytest.raises(LookupError):  # a later expiry is not signed
        visit(db, ana_link, url.replace(f"e={expires}", f"e={int(expires) + 3600}"))
    with closing(videos_db.connect()) as sc:
        old = lesson_files.address(
            sc,
            lesson_files.public_base(ana_link["token"]),
            pdf["id"],
            lesson_files.student_viewer(ana_link["ref_id"]),
            expires=int(time.time()) - 60,
        )
    with pytest.raises(PermissionError, match="expired"):
        visit(db, ana_link, old)

    # someone else's link with Ana's address: not made for them
    ben = service.enrol(db, course["id"], {"name": "Ben", "email": "ben@example.com"})
    ben_link = public.resolve(db, ben["link"]["token"])
    service.mark_paid(db, ben_link["ref_id"])
    with pytest.raises(LookupError):
        visit(db, ben_link, url)
    course_link = public.resolve(
        db, public.create_link(db, "academy", "course", course["id"])["token"]
    )
    with pytest.raises(LookupError):
        visit(db, course_link, url)

    # refunded after the address was handed out: refused, however fresh it looks
    service.refund(db, ana_link["ref_id"])
    with pytest.raises(PermissionError, match="refunded"):
        visit(db, ana_link, url)

    # the course page: free previews only, while they stay previews
    page = service.course_page(db, course["id"], course_link["token"])
    shown = {item["title"]: item for item in page["modules"][0]["lessons"]}
    assert "files" not in shown["Chords"] and len(shown["Tuning"]["files"]) == 1
    page_url = shown["Tuning"]["files"][0]["url"]
    assert visit(db, course_link, page_url)["id"] == mp3["id"]
    service.update_lesson(db, preview["id"], {"preview": False})
    with pytest.raises(LookupError):
        visit(db, course_link, page_url)

    # the editor's own address works for this account only
    detail = service.course_detail(db, course["id"], home=HOME)
    staff_url = next(
        item
        for m in detail["modules"]
        for item in m["lessons"]
        if item["id"] == paid["id"]
    )["files"][0]["url"]
    file_id, expires, signature = signed(staff_url)
    with closing(videos_db.connect()) as sc:
        assert (
            lesson_files.for_staff(db, sc, HOME, file_id, expires, signature)["id"]
            == pdf["id"]
        )
        with pytest.raises(LookupError):
            lesson_files.for_staff(
                db, sc, "local:someone-else", file_id, expires, signature
            )


def test_external_links_are_flagged_and_deleting_removes_the_files(db):
    course, module, preview, paid = build(db, price=0)
    service.update_lesson(
        db, preview["id"], {"video_url": "https://youtube.com/watch?v=abc"}
    )
    detail = service.course_detail(db, course["id"], home=HOME)
    lessons = {item["id"]: item for item in detail["modules"][0]["lessons"]}
    assert lessons[preview["id"]]["video_external"] is True
    assert (
        lessons[paid["id"]]["video_external"] is False
        and lessons[paid["id"]]["files"] == []
    )

    one = put(db, paid["id"], "document", "a.pdf", PDF)
    two = put(db, paid["id"], "audio", "b.mp3", MP3)
    three = put(db, preview["id"], "document", "c.pdf", PDF)
    paths = {
        item["id"]: on_disk(media_id(db, item["id"])) for item in (one, two, three)
    }
    assert all(path.exists() for path in paths.values())

    lesson_files.remove(db, two["id"])  # one file taken off its lesson
    assert not paths[two["id"]].exists()
    with pytest.raises(LookupError):
        lesson_files.row(db, two["id"])

    one_media = media_id(db, one["id"])
    service.delete_lesson(db, paid["id"])
    assert not paths[one["id"]].exists() and on_disk(one_media) is None
    assert (
        db.execute(
            "SELECT COUNT(*) FROM ac_lesson_files WHERE lesson_id = ?", (paid["id"],)
        ).fetchone()[0]
        == 0
    )

    three_media = media_id(db, three["id"])
    service.delete_module(db, module["id"])
    assert not paths[three["id"]].exists() and on_disk(three_media) is None

    # a whole course
    other, _, first, _ = build(db, price=0)
    four = put(db, first["id"], "video", "intro.mp4", make_mp4())
    four_path = on_disk(media_id(db, four["id"]))
    service.delete_course(db, other["id"])
    assert not four_path.exists()
    assert db.execute("SELECT COUNT(*) FROM ac_lesson_files").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def fetch(url, *, opener=None, headers=None):
    """(status, body, headers) of a GET, signed in through ``opener`` or anonymous."""
    request = urllib.request.Request(url)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with (opener.open if opener else urllib.request.urlopen)(
            request, timeout=30
        ) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def test_the_local_build_serves_lesson_files_to_enrolled_students_only(
    env, api, local_index
):
    kit.forget_schema()
    course = api.post(
        "/api/academy/courses", {"title": "Guitar", "price_cents": 4900}, expect=201
    )["course"]
    module = api.post(
        f"/api/academy/courses/{course['id']}/modules", {"title": "Start"}, expect=201
    )["module"]
    preview = api.post(
        f"/api/academy/modules/{module['id']}/lessons",
        {
            "title": "Tuning",
            "preview": True,
            "video_url": "https://youtube.com/watch?v=abc",
        },
        expect=201,
    )["lesson"]
    paid = api.post(
        f"/api/academy/modules/{module['id']}/lessons", {"title": "Chords"}, expect=201
    )["lesson"]
    base = f"/api/academy/lessons/{paid['id']}"

    # refused at the start (name, size) and on the first part (content)
    status, body = api.call(
        "POST", f"{base}/uploads", {"kind": "document", "filename": "x.exe", "size": 10}
    )
    assert status == 400 and "PDF" in body["error"]
    status, body = api.call(
        "POST",
        f"{base}/uploads",
        {"kind": "document", "filename": "big.pdf", "size": 50 * media.MB + 1},
    )
    assert status == 400 and "too large" in body["error"]
    status, started = api.call(
        "POST",
        f"{base}/uploads",
        {"kind": "document", "filename": "fake.pdf", "size": 12},
    )
    assert status == 201
    status, body = api.call(
        "POST",
        f"{base}/uploads/{started['upload']['id']}/chunk",
        {"offset": 0, "data": base64.b64encode(b"MZ\x90\x00 not pdf").decode()},
    )
    assert status == 422 and body["code"] == "wrong_file"

    upload(api, "document", PDF, base=base, name="Chord chart.pdf")
    video = make_mp4(payload=8192)
    upload(
        api,
        "video",
        video,
        base=f"/api/academy/lessons/{preview['id']}",
        name="tuning.mp4",
    )
    api.post(f"/api/academy/courses/{course['id']}/status", {"status": "published"})
    detail = api.get(f"/api/academy/courses/{course['id']}")
    lessons = {item["id"]: item for item in detail["modules"][0]["lessons"]}
    assert (
        lessons[preview["id"]]["video_external"] is True
        and lessons[paid["id"]]["video_external"] is False
    )
    pdf = lessons[paid["id"]]["files"][0]
    assert pdf["kind"] == "document" and pdf["name"] == "Chord chart.pdf"

    # the editor opens it with its signed address (and only with it)
    status, data, headers = fetch(env.base + pdf["url"], opener=api.opener)
    assert status == 200 and data == PDF
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Length"] == str(len(PDF))
    assert headers["Cache-Control"] == "private, no-store"
    assert headers["Content-Disposition"].startswith(
        'inline; filename="Chord chart.pdf"'
    )
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert fetch(env.base + pdf["url"].split("?")[0], opener=api.opener)[0] == 404
    assert fetch(env.base + pdf["url"])[0] == 401  # signed in only

    # the course page: the free preview's video, in ranges
    link = api.post(f"/api/academy/courses/{course['id']}/link", {})["link"]
    status, page, _ = fetch(f"{env.base}/api/public/{link['token']}")
    page = json.loads(page)
    shown = {item["title"]: item for item in page["page"]["modules"][0]["lessons"]}
    assert "files" not in shown["Chords"]
    preview_url = shown["Tuning"]["files"][0]["url"]
    status, data, headers = fetch(
        env.base + preview_url, headers={"Range": "bytes=0-99"}
    )
    assert (
        status == 206 and data == video[:100] and headers["Content-Type"] == "video/mp4"
    )
    assert (
        headers["Content-Range"] == f"bytes 0-99/{len(video)}"
        and headers["Accept-Ranges"] == "bytes"
    )

    # a student: nothing until they have paid, then the file through their own page's address
    request = urllib.request.Request(
        f"{env.base}/api/public/{link['token']}/enrol",
        data=json.dumps({"name": "Ana", "email": "ana@example.com"}).encode(),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    with urllib.request.urlopen(request, timeout=30) as response:
        student_token = json.loads(response.read())["student_path"].removeprefix("/p/")

    def student_files():
        status, raw, _ = fetch(f"{env.base}/api/public/{student_token}")
        assert status == 200
        lessons = {
            item["title"]: item
            for item in json.loads(raw)["page"]["modules"][0]["lessons"]
        }
        return lessons["Chords"].get("files")

    assert student_files() is None  # locked
    # the editor's address for the PDF does not work on a student's page
    file_id, expires, signature = signed(pdf["url"])
    assert (
        fetch(
            f"{env.base}/api/public/{student_token}/lesson-file/{file_id}?e={expires}&s={signature}"
        )[0]
        == 404
    )
    student = api.get("/api/academy/students")["students"][0]
    api.post(f"/api/academy/students/{student['id']}/paid", {})
    url = student_files()[0]["url"]
    status, data, headers = fetch(env.base + url)
    assert (
        status == 200
        and data == PDF
        and headers["Cache-Control"] == "private, no-store"
    )
    assert (
        fetch(env.base + url[:-1] + ("0" if url[-1] != "0" else "1"))[0] == 404
    )  # tampered
    # the course page's token with the student's address: not made for it
    assert fetch(env.base + url.replace(student_token, link["token"]))[0] == 404

    # refunded: the same address stops working at once
    api.post(f"/api/academy/students/{student['id']}/refund", {})
    status, body, _ = fetch(env.base + url)
    assert status == 403 and b"refunded" in body

    # another student whose link is turned off
    status, other = api.call(
        "POST",
        "/api/academy/students",
        {
            "course_id": course["id"],
            "name": "Ben",
            "email": "ben@example.com",
            "paid": True,
        },
    )
    assert status == 201
    ben_token = other["link"]["token"]
    status, raw, _ = fetch(f"{env.base}/api/public/{ben_token}")
    ben_url = next(
        i
        for i in json.loads(raw)["page"]["modules"][0]["lessons"]
        if i["title"] == "Chords"
    )["files"][0]["url"]
    assert fetch(env.base + ben_url)[0] == 200
    api.call("DELETE", f"/api/academy/students/{other['enrolment']['id']}/link")
    assert fetch(env.base + ben_url)[0] == 404

    # removing a file, then deleting a lesson, removes them from the disk
    assert api.call("DELETE", f"/api/academy/lesson-files/{pdf['id']}")[0] == 200
    assert fetch(env.base + pdf["url"], opener=api.opener)[0] == 404
    detail = api.get(f"/api/academy/courses/{course['id']}")
    video_file = next(
        i for i in detail["modules"][0]["lessons"] if i["id"] == preview["id"]
    )["files"][0]
    with closing(videos_db.connect()) as sc:
        rows = sc.execute(
            "SELECT path FROM vi_files WHERE owner_key LIKE 'academy:%'"
        ).fetchall()
    assert len(rows) == 1 and (media.root() / rows[0]["path"]).exists()
    api.call("DELETE", f"/api/academy/lessons/{preview['id']}")
    assert not (media.root() / rows[0]["path"]).exists()
    assert fetch(env.base + preview_url)[0] == 404
    assert fetch(env.base + video_file["url"], opener=api.opener)[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_lesson_files_need_the_enrolment_and_have_policy_rules(cloud):
    from monosage_cloud.policy import POLICY

    names = {
        "lesson_upload_start": "suite.manage",
        "lesson_upload_get": "suite.read",
        "lesson_upload_chunk": "suite.manage",
        "lesson_upload_complete": "suite.manage",
        "lesson_upload_cancel": "suite.manage",
        "lesson_file_delete": "suite.manage",
        "lesson_file_get": "suite.read",
    }
    for name, permission in names.items():
        rule = POLICY[f"airaapi__academy_routes__{name}"]
        assert (rule.scope, rule.permission) == ("org_api", permission), name
    assert POLICY["airaapi__academy_routes__public_lesson_file"].scope == "public_link"

    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "academy")
    owner = sign_in(cloud, org.owner)
    course = owner.call(
        "POST", "/api/academy/courses", {"title": "Leadership", "price_cents": 0}
    )[1]["course"]
    module = owner.call(
        "POST", f"/api/academy/courses/{course['id']}/modules", {"title": "One"}
    )[1]["module"]
    lesson = owner.call(
        "POST", f"/api/academy/modules/{module['id']}/lessons", {"title": "Listening"}
    )[1]["lesson"]
    base = f"/api/academy/lessons/{lesson['id']}"
    upload(owner, "audio", MP3, base=base, name="talk.mp3")
    auditor = sign_in(cloud, org.auditor)
    status, _ = auditor.call(
        "POST", f"{base}/uploads", {"kind": "audio", "filename": "a.mp3", "size": 10}
    )
    assert status == 403  # reading is not uploading
    owner.call(
        "POST", f"/api/academy/courses/{course['id']}/status", {"status": "published"}
    )
    detail = owner.call("GET", f"/api/academy/courses/{course['id']}")[1]
    staff_url = detail["modules"][0]["lessons"][0]["files"][0]["url"]
    status, data = owner.call("GET", staff_url, raw=True)
    assert (
        status == 200 and data == MP3 and owner.header("Content-Type") == "audio/mpeg"
    )

    link = owner.call("POST", f"/api/academy/courses/{course['id']}/link", {})[1][
        "link"
    ]
    visitor = Browser(cloud)
    status, enrolled = visitor.call(
        "POST",
        f"/api/public/{link['token']}/enrol",
        {"name": "Ana", "email": "ana@example.com"},
    )
    assert status == 201
    student_token = enrolled["student_path"].removeprefix("/p/")
    page = visitor.call("GET", f"/api/public/{student_token}")[1]
    url = page["page"]["modules"][0]["lessons"][0]["files"][0]["url"]
    status, data = visitor.call("GET", url, raw=True, headers={"Range": "bytes=0-9"})
    assert status == 206 and data == MP3[:10]
    assert visitor.header("Cache-Control") == "private, no-store"
    assert visitor.header("Content-Disposition").startswith(
        'inline; filename="talk.mp3"'
    )
    assert (
        visitor.call("GET", url[:-1] + ("0" if url[-1] != "0" else "1"), raw=True)[0]
        == 404
    )
    # the editor's address is not a visitor's
    assert visitor.call("GET", staff_url, raw=True)[0] in (401, 403)

    student = owner.call("GET", "/api/academy/students")[1]["students"][0]
    owner.call("POST", f"/api/academy/students/{student['id']}/refund", {})
    assert visitor.call("GET", url, raw=True)[0] == 403

    # another organization cannot reach the file
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "academy")
    theirs = sign_in(cloud, other.owner)
    assert theirs.call("GET", staff_url, raw=True)[0] == 404
    assert (
        theirs.call("DELETE", f"/api/academy/lesson-files/{signed(staff_url)[0]}")[0]
        == 404
    )
