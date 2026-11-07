"""Lesson files: the videos, audio and PDFs a course author uploads to a lesson, for that course's students only.

* **Where they live.** They are media files of Videos (:mod:`aira.suite.videos.media`), uploaded the same way:
  resumable parts, the type told from the first bytes, the free-space reserve, the hosted organization's storage
  limit and HTTP range reads for playback. They belong to the business (owner ``academy:<home>``, kept apart from
  people's channels and advertisers); the account's database keeps which lesson has each one
  (``ac_lesson_files``). Deleting a lesson, a module or a course removes its files from the disk too.
* **What is kept.** :data:`KINDS`: a video (MP4, MOV, WebM), audio (MP3, M4A, Ogg, WAV, FLAC, WebM audio) or a PDF.
  The file name must end in one of :data:`NAMES`, and the first bytes must say one of :data:`TYPES` (checked when
  the first part arrives and again when the upload completes). The most per kind is
  :func:`aira.suite.videos.media.max_bytes` (video 2 GB, audio 500 MB, PDF 50 MB); a lesson has at most
  :data:`MAX_PER_LESSON` files.
* **Who gets them.** Only through a signed address that expires (:func:`address`): an HMAC (``media.sign``, the
  key Videos signs its addresses with) over the file, who it was made for and when it expires. Who it was made for
  is ``student:<enrolment id>`` on a student's own page, ``course:<course id>`` on the course page (free-preview
  lessons only, which anyone shown the page may already read) and ``staff:<home>`` in the editor; never a name or
  an email, and it is not in the address: the server works it out again from the link or the session. Every
  request checks the signature and the time, then the database again (:func:`for_visitor`): a student who was
  refunded, whose link was turned off or whose course was deleted gets nothing, however fresh the address looks.
  Files go out with ``Cache-Control: private, no-store``, ``Content-Disposition: inline`` and a safe file name.

External video links (``ac_lessons.video_url``) still work, but anyone with such a link can watch it: the editor
marks them external (``video_external``) and says so.
"""

from __future__ import annotations

import hmac
import logging
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping
from contextlib import closing
from typing import Any
from urllib.parse import quote

from aira.suite import kit
from aira.suite.academy import store

logger = logging.getLogger("aira.suite.academy")

KINDS = ("video", "audio", "document")
NAMES = {
    "video": (".mp4", ".m4v", ".mov", ".webm"),
    "audio": (
        ".mp3",
        ".m4a",
        ".ogg",
        ".oga",
        ".opus",
        ".wav",
        ".flac",
        ".weba",
        ".webm",
    ),
    "document": (".pdf",),
}
TYPES = {
    "video": ("video/mp4", "video/quicktime", "video/webm"),
    "audio": (
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "audio/flac",
        "audio/webm",
    ),
    "document": ("application/pdf",),
}
KIND_WORDS = {"video": "a video", "audio": "audio", "document": "a PDF"}
MAX_PER_LESSON = 10
HOURS = 3  # how long an address works (rounded up to the hour, so it stays the same while a page is open)
STAFF_BASE = "/api/academy/lesson-files"
HEADERS: tuple[tuple[str, str], ...] = (
    ("Cache-Control", "private, no-store"),
    ("X-Content-Type-Options", "nosniff"),
)
NOT_FOUND = "That file was not found."


# ---------------------------------------------------------------------------
# whose files they are
# ---------------------------------------------------------------------------


def home_of(request: Any) -> str:
    """The account the files count against: the organization (hosted) or the computer's account (single-user)."""
    from aira.suite.videos import service as videos

    return videos.home_of(request)


def owner_key(home: str) -> str:
    """Whose uploads a business's lesson files are in the media store (apart from channels and advertisers)."""
    return f"academy:{home}"


def public_base(token: str) -> str:
    return f"/api/public/{token}/lesson-file"


def student_viewer(enrolment_id: str) -> str:
    return f"student:{enrolment_id}"


def course_viewer(course_id: str) -> str:
    return f"course:{course_id}"


def staff_viewer(home: str) -> str:
    return f"staff:{home}"


def _videos() -> sqlite3.Connection:
    from aira.suite.videos import db as videos_db

    return videos_db.connect()


# ---------------------------------------------------------------------------
# uploading to a lesson
# ---------------------------------------------------------------------------


def check_name(kind: Any, filename: Any) -> str:
    """The kind, when the file's name fits it (ValueError otherwise). The content is checked by the media store."""
    kind = str(kind or "")
    if kind not in KINDS:
        raise ValueError("Choose what you are uploading: a video, audio or a PDF.")
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if not name.endswith(NAMES[kind]):
        allowed = ", ".join(NAMES[kind])
        raise ValueError(
            f"This file name does not end like {KIND_WORDS[kind]}: use {allowed}."
        )
    return kind


def _count(connection: sqlite3.Connection, lesson_id: str) -> int:
    return kit.count(
        connection,
        "SELECT COUNT(*) FROM ac_lesson_files WHERE lesson_id = ?",
        (lesson_id,),
    )


def _room(connection: sqlite3.Connection, lesson_id: str) -> None:
    if _count(connection, lesson_id) >= MAX_PER_LESSON:
        raise ValueError(
            f"A lesson can have {MAX_PER_LESSON} files. Remove one before adding another."
        )


def start(
    connection: sqlite3.Connection,
    sc: sqlite3.Connection,
    home: str,
    lesson_id: str,
    kind: Any,
    filename: Any,
    size: Any,
) -> dict[str, Any]:
    """Begin uploading a file to a lesson (the parts follow through the media store)."""
    from aira.suite.videos import media

    store.lesson(connection, lesson_id)
    kind = check_name(kind, filename)
    _room(connection, lesson_id)
    return media.start(
        sc, owner_key(home), home, kind, str(filename or ""), size, kinds=KINDS
    )


def attach(
    connection: sqlite3.Connection,
    sc: sqlite3.Connection,
    home: str,
    lesson_id: str,
    upload_id: str,
) -> dict[str, Any]:
    """Finish an upload and add the file to the lesson; returns the file as the editor shows it."""
    from aira.suite.videos import media

    lesson = store.lesson(connection, lesson_id)
    _room(connection, lesson_id)
    owner = owner_key(home)
    name = media.upload(sc, upload_id, owner)["filename"]
    done = media.complete(sc, upload_id, owner)
    if done["kind"] not in KINDS or done["mime"] not in TYPES[done["kind"]]:
        media.delete_file(sc, done["id"])
        sc.commit()
        raise ValueError(
            "This file cannot be played in a browser: use MP4 or WebM for a video."
            if done["kind"] == "video"
            else "This kind of file is not taken for a lesson."
        )
    entry = {
        "id": kit.new_id(),
        "lesson_id": lesson_id,
        "course_id": lesson["course_id"],
        "file_id": done["id"],
        "kind": done["kind"],
        "name": name or f"lesson{media.EXTENSIONS.get(done['mime'], '')}",
        "mime": done["mime"],
        "size": int(done["size"] or 0),
        "duration": done.get("duration"),
        "position": _count(connection, lesson_id),
        "created_at": kit.now(),
    }
    try:
        connection.execute(
            """INSERT INTO ac_lesson_files (id, lesson_id, course_id, file_id, kind, name, mime, size, duration, position, created_at)
            VALUES (:id, :lesson_id, :course_id, :file_id, :kind, :name, :mime, :size, :duration, :position, :created_at)""",
            entry,
        )
        connection.execute(
            "UPDATE ac_courses SET updated_at = ? WHERE id = ?",
            (kit.now(), lesson["course_id"]),
        )
        connection.commit()
    except sqlite3.Error:
        # no lesson has it: it must not stay on the disk
        media.delete_file(sc, done["id"])
        sc.commit()
        raise
    return view(sc, entry, staff_viewer(home), STAFF_BASE)


# ---------------------------------------------------------------------------
# reading and removing
# ---------------------------------------------------------------------------


def row(connection: sqlite3.Connection, lesson_file_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ac_lesson_files", lesson_file_id, "file")


def by_lesson(
    connection: sqlite3.Connection, course_id: str
) -> dict[str, list[dict[str, Any]]]:
    """A course's lesson files, by lesson, in the order they were added."""
    out: dict[str, list[dict[str, Any]]] = {}
    for item in kit.fetch_all(
        connection,
        "SELECT * FROM ac_lesson_files WHERE course_id = ? ORDER BY position, created_at",
        (course_id,),
    ):
        out.setdefault(item["lesson_id"], []).append(item)
    return out


def forget(
    connection: sqlite3.Connection, *, lesson_id: str = "", course_id: str = ""
) -> list[str]:
    """Delete the rows of a lesson's (or a whole course's) files, without committing; returns their media file ids
    for :func:`discard` once the deletion is committed."""
    column, value = ("lesson_id", lesson_id) if lesson_id else ("course_id", course_id)
    if not value:
        return []
    found = [
        item["file_id"]
        for item in kit.fetch_all(
            connection,
            f"SELECT file_id FROM ac_lesson_files WHERE {column} = ?",  # noqa: S608 - a fixed column name
            (value,),
        )
    ]
    connection.execute(f"DELETE FROM ac_lesson_files WHERE {column} = ?", (value,))  # noqa: S608
    return found


def discard(file_ids: Iterable[str]) -> int:
    """Remove media files (from their database and the disk). Never stops the deletion that asked for it."""
    ids = [item for item in file_ids if item]
    if not ids:
        return 0
    from aira.suite.videos import media

    try:
        with closing(_videos()) as sc:
            for file_id in ids:
                media.delete_file(sc, file_id)
            sc.commit()
    except (OSError, sqlite3.Error, RuntimeError):
        logger.exception(
            "Could not remove %s lesson file(s) from the media store", len(ids)
        )
        return 0
    return len(ids)


def remove(connection: sqlite3.Connection, lesson_file_id: str) -> dict[str, Any]:
    """Take a file off its lesson and delete it."""
    found = row(connection, lesson_file_id)
    connection.execute("DELETE FROM ac_lesson_files WHERE id = ?", (lesson_file_id,))
    connection.execute(
        "UPDATE ac_courses SET updated_at = ? WHERE id = ?",
        (kit.now(), found["course_id"]),
    )
    connection.commit()
    discard([found["file_id"]])
    return found


def erase_home(sc: sqlite3.Connection, home: str) -> int:
    """Every lesson file and unfinished lesson upload of one account (an organization deleted)."""
    from aira.suite.videos import media

    return media.delete_owner(sc, owner_key(home))


# ---------------------------------------------------------------------------
# signed addresses
# ---------------------------------------------------------------------------


def _expires(hours: int = HOURS) -> int:
    return (int(time.time()) // 3600 + 1 + hours) * 3600


def _signature(sc: sqlite3.Connection, file_id: str, viewer: str, expires: int) -> str:
    from aira.suite.videos import media

    return media.sign(sc, f"academy-file:{file_id}:{viewer}:{int(expires)}")


def address(
    sc: sqlite3.Connection,
    base: str,
    file_id: str,
    viewer: str,
    *,
    expires: int | None = None,
) -> str:
    """The address that serves this lesson file to ``viewer`` until it expires."""
    when = _expires() if expires is None else int(expires)
    return f"{base}/{file_id}?e={when}&s={_signature(sc, file_id, viewer, when)}"


def check(
    sc: sqlite3.Connection, file_id: str, viewer: str, expires: Any, signature: Any
) -> None:
    """LookupError for an address that was not made for this viewer (or was changed); PermissionError once it
    expired."""
    try:
        when = int(str(expires or ""))
    except ValueError:
        raise LookupError(NOT_FOUND) from None
    if not re.fullmatch(
        r"[0-9a-f]{32}", str(signature or "")
    ) or not hmac.compare_digest(_signature(sc, file_id, viewer, when), str(signature)):
        raise LookupError(NOT_FOUND)
    if when < time.time():
        raise PermissionError(
            "This address has expired. Reload the page to open the file again."
        )


def view(
    sc: sqlite3.Connection, item: Mapping[str, Any], viewer: str, base: str
) -> dict[str, Any]:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "name": item["name"],
        "mime": item["mime"],
        "size": int(item["size"] or 0),
        "duration": item["duration"],
        "url": address(sc, base, item["id"], viewer),
    }


def views(
    items: Mapping[str, list[dict[str, Any]]],
    lesson_ids: Iterable[str],
    viewer: str,
    base: str,
) -> dict[str, list[dict[str, Any]]]:
    """The files of these lessons with addresses for ``viewer`` (the media store is only opened when there are any)."""
    wanted = [lesson_id for lesson_id in lesson_ids if items.get(lesson_id)]
    if not wanted:
        return {}
    with closing(_videos()) as sc:
        return {
            lesson_id: [view(sc, item, viewer, base) for item in items[lesson_id]]
            for lesson_id in wanted
        }


# ---------------------------------------------------------------------------
# who may have a file, checked again on every request
# ---------------------------------------------------------------------------


def for_staff(
    connection: sqlite3.Connection,
    sc: sqlite3.Connection,
    home: str,
    lesson_file_id: str,
    expires: Any,
    signature: Any,
) -> dict[str, Any]:
    """The file for the business's own editor (signed for this account, and its lesson still exists)."""
    found = row(connection, lesson_file_id)
    check(sc, found["id"], staff_viewer(home), expires, signature)
    store.lesson(connection, found["lesson_id"])
    return found


def for_visitor(
    connection: sqlite3.Connection,
    sc: sqlite3.Connection,
    link: Mapping[str, Any],
    lesson_file_id: str,
    expires: Any,
    signature: Any,
) -> dict[str, Any]:
    """The file for a visitor of a public page: a student's own page (their enrolment still gives them the lesson)
    or the course page (a free-preview lesson of a published course). LookupError or PermissionError otherwise."""
    from aira.suite.academy import service

    kind, ref_id = str(link.get("kind") or ""), str(link.get("ref_id") or "")
    if kind == "student":
        viewer = student_viewer(ref_id)
    elif kind == "course":
        viewer = course_viewer(ref_id)
    else:
        raise LookupError(NOT_FOUND)
    found = row(connection, lesson_file_id)
    check(sc, found["id"], viewer, expires, signature)
    lesson = store.lesson(connection, found["lesson_id"])
    if kind == "student":
        enrolment = store.enrolment(connection, ref_id)
        if enrolment["course_id"] != lesson["course_id"]:
            raise LookupError(NOT_FOUND)
        if not (service.can_learn(enrolment) or lesson["preview"]):
            raise PermissionError(
                "This enrolment was refunded."
                if enrolment["status"] == "refunded"
                else "Your lessons unlock once your payment is confirmed."
            )
    else:
        course = store.course(connection, lesson["course_id"])
        if (
            course["id"] != ref_id
            or course["status"] != "published"
            or not lesson["preview"]
        ):
            raise LookupError(NOT_FOUND)
    return found


# ---------------------------------------------------------------------------
# sending a file
# ---------------------------------------------------------------------------


def disposition(name: str, mime: str) -> str:
    """``inline`` with a file name that cannot break the header: ASCII letters, digits, dots, dashes and spaces
    (anything else becomes ``_``), the extension of what the file really is, and the original name UTF-8 encoded."""
    from aira.suite.videos import media

    extension = media.EXTENSIONS.get(mime, "")
    base = "".join(
        ch
        for ch in str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
        if ch.isprintable()
    )
    stem = base[: -len(base.rsplit(".", 1)[-1]) - 1] if "." in base else base
    plain = re.sub(r"[^A-Za-z0-9 ._-]+", "_", stem).strip(" ._")[:100] or "lesson-file"
    full = (stem.strip()[:100] or "lesson-file") + extension
    return f"inline; filename=\"{plain}{extension}\"; filename*=UTF-8''{quote(full, safe='')}"


def send(
    sc: sqlite3.Connection, found: Mapping[str, Any], range_header: str
) -> tuple[int, bytes, str, tuple[tuple[str, str], ...]]:
    """(status, body, content type, headers) for one request: the whole file or the asked range (206)."""
    from aira.suite.videos import media

    stored = media.file_row(sc, found["file_id"])
    status, body, headers = media.read_range(stored, range_header)
    return (
        status,
        body,
        stored["mime"],
        (
            *HEADERS,
            ("Content-Disposition", disposition(found["name"], stored["mime"])),
            *headers,
        ),
    )
