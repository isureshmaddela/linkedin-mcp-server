"""Academy tables and the plain reads and writes over them (rules live in :mod:`aira.suite.academy.service`)."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit

APP = "academy"
LEVELS = ("beginner", "intermediate", "advanced", "all")
COURSE_STATUSES = ("draft", "published", "archived")
ENROLMENT_STATUSES = ("pending_payment", "active", "completed", "refunded")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ac_courses (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        subtitle TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL DEFAULT 'all',
        price_cents INTEGER NOT NULL DEFAULT 0,
        cover_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        enrol_code TEXT NOT NULL DEFAULT '',
        capacity INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        published_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ac_modules (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        title TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ac_modules_course ON ac_modules (course_id, position)",
    """CREATE TABLE IF NOT EXISTS ac_lessons (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        module_id TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        video_url TEXT NOT NULL DEFAULT '',
        minutes INTEGER NOT NULL DEFAULT 0,
        preview INTEGER NOT NULL DEFAULT 0,
        pass_mark INTEGER NOT NULL DEFAULT 70,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ac_lessons_module ON ac_lessons (module_id, position)",
    "CREATE INDEX IF NOT EXISTS ac_lessons_course ON ac_lessons (course_id)",
    """CREATE TABLE IF NOT EXISTS ac_questions (
        id TEXT PRIMARY KEY,
        lesson_id TEXT NOT NULL,
        prompt TEXT NOT NULL,
        options TEXT NOT NULL,
        correct INTEGER NOT NULL,
        explanation TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ac_questions_lesson ON ac_questions (lesson_id, position)",
    """CREATE TABLE IF NOT EXISTS ac_cohorts (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        name TEXT NOT NULL,
        starts_on TEXT NOT NULL DEFAULT '',
        capacity INTEGER,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ac_enrolments (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        cohort_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        status TEXT NOT NULL,
        reference TEXT NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        paid_cents INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'public',
        notes TEXT NOT NULL DEFAULT '',
        certificate_code TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        activated_at TEXT,
        completed_at TEXT,
        refunded_at TEXT,
        last_activity_at TEXT,
        nudged_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ac_enrolments_course ON ac_enrolments (course_id, status)",
    "CREATE INDEX IF NOT EXISTS ac_enrolments_email ON ac_enrolments (email)",
    "CREATE INDEX IF NOT EXISTS ac_enrolments_certificate ON ac_enrolments (certificate_code)",
    """CREATE TABLE IF NOT EXISTS ac_progress (
        enrolment_id TEXT NOT NULL,
        lesson_id TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        PRIMARY KEY (enrolment_id, lesson_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ac_attempts (
        id TEXT PRIMARY KEY,
        enrolment_id TEXT NOT NULL,
        lesson_id TEXT NOT NULL,
        score INTEGER NOT NULL,
        passed INTEGER NOT NULL,
        answers TEXT NOT NULL,
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ac_attempts_enrolment ON ac_attempts (enrolment_id, lesson_id)",
    """CREATE TABLE IF NOT EXISTS ac_announcements (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        recipients INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
)


# Added later: students rate a course (one review each); the business may hide a review.
REVIEW_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ac_reviews (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        enrolment_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        rating INTEGER NOT NULL,
        comment TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'published',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ac_reviews_course ON ac_reviews (course_id, status, created_at)",
)
REVIEW_STATUSES = ("published", "hidden")


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "academy.v1", SCHEMA)
    kit.ensure(connection, "academy.reviews.v1", REVIEW_SCHEMA)


# ---------------------------------------------------------------------------
# courses
# ---------------------------------------------------------------------------


def course(connection: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ac_courses", course_id, "course")


def courses(
    connection: sqlite3.Connection, *, status: str = "", q: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("c.status = ?")
        params.append(status)
    if q:
        clauses.append("(c.title LIKE ? OR c.subtitle LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return kit.fetch_all(
        connection,
        f"""SELECT c.*,
            (SELECT COUNT(*) FROM ac_lessons l WHERE l.course_id = c.id) AS lessons,
            (SELECT COUNT(*) FROM ac_enrolments e WHERE e.course_id = c.id AND e.status IN ('active', 'completed')) AS students,
            (SELECT COUNT(*) FROM ac_enrolments e WHERE e.course_id = c.id AND e.status = 'pending_payment') AS pending,
            (SELECT COUNT(*) FROM ac_enrolments e WHERE e.course_id = c.id AND e.status = 'completed') AS completed,
            (SELECT ROUND(AVG(r.rating), 1) FROM ac_reviews r WHERE r.course_id = c.id AND r.status = 'published') AS rating,
            (SELECT COUNT(*) FROM ac_reviews r WHERE r.course_id = c.id AND r.status = 'published') AS reviews
        FROM ac_courses c {where} ORDER BY c.updated_at DESC""",  # noqa: S608 - clauses are fixed strings
        params,
    )


def modules(connection: sqlite3.Connection, course_id: str) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT * FROM ac_modules WHERE course_id = ? ORDER BY position, created_at",
        (course_id,),
    )


def lessons(connection: sqlite3.Connection, course_id: str) -> list[dict[str, Any]]:
    """Every lesson of a course in teaching order (module order, then lesson order)."""
    return kit.fetch_all(
        connection,
        """SELECT l.* FROM ac_lessons l JOIN ac_modules m ON m.id = l.module_id
        WHERE l.course_id = ? ORDER BY m.position, m.created_at, l.position, l.created_at""",
        (course_id,),
    )


def lesson(connection: sqlite3.Connection, lesson_id: str) -> dict[str, Any]:
    found = kit.require_row(connection, "ac_lessons", lesson_id, "lesson")
    found["preview"] = bool(found["preview"])
    return found


def questions(connection: sqlite3.Connection, lesson_id: str) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM ac_questions WHERE lesson_id = ? ORDER BY position",
        (lesson_id,),
    )
    for row in rows:
        row["options"] = kit.loads(row["options"], [])
    return rows


def quiz_lessons(connection: sqlite3.Connection, course_id: str) -> set[str]:
    return {
        row["lesson_id"]
        for row in kit.fetch_all(
            connection,
            "SELECT DISTINCT q.lesson_id FROM ac_questions q JOIN ac_lessons l ON l.id = q.lesson_id WHERE l.course_id = ?",
            (course_id,),
        )
    }


# ---------------------------------------------------------------------------
# enrolments and cohorts
# ---------------------------------------------------------------------------


def enrolment(connection: sqlite3.Connection, enrolment_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ac_enrolments", enrolment_id, "student")


def completed_lessons(connection: sqlite3.Connection, enrolment_id: str) -> set[str]:
    return {
        row["lesson_id"]
        for row in kit.fetch_all(
            connection,
            "SELECT lesson_id FROM ac_progress WHERE enrolment_id = ?",
            (enrolment_id,),
        )
    }


def passed_quizzes(connection: sqlite3.Connection, enrolment_id: str) -> set[str]:
    return {
        row["lesson_id"]
        for row in kit.fetch_all(
            connection,
            "SELECT DISTINCT lesson_id FROM ac_attempts WHERE enrolment_id = ? AND passed = 1",
            (enrolment_id,),
        )
    }


def best_scores(connection: sqlite3.Connection, enrolment_id: str) -> dict[str, int]:
    return {
        row["lesson_id"]: int(row["best"])
        for row in kit.fetch_all(
            connection,
            "SELECT lesson_id, MAX(score) AS best FROM ac_attempts WHERE enrolment_id = ? GROUP BY lesson_id",
            (enrolment_id,),
        )
    }


def cohort(connection: sqlite3.Connection, cohort_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ac_cohorts", cohort_id, "cohort")


def cohorts(
    connection: sqlite3.Connection, course_id: str = ""
) -> list[dict[str, Any]]:
    where, params = ("WHERE c.course_id = ?", [course_id]) if course_id else ("", [])
    return kit.fetch_all(
        connection,
        f"""SELECT c.*, co.title AS course_title,
            (SELECT COUNT(*) FROM ac_enrolments e WHERE e.cohort_id = c.id AND e.status != 'refunded') AS students
        FROM ac_cohorts c LEFT JOIN ac_courses co ON co.id = c.course_id {where}
        ORDER BY c.starts_on, c.created_at""",  # noqa: S608 - fixed clause
        params,
    )


def seats_taken(
    connection: sqlite3.Connection, course_id: str, cohort_id: str = ""
) -> int:
    if cohort_id:
        return kit.count(
            connection,
            "SELECT COUNT(*) FROM ac_enrolments WHERE cohort_id = ? AND status != 'refunded'",
            (cohort_id,),
        )
    return kit.count(
        connection,
        "SELECT COUNT(*) FROM ac_enrolments WHERE course_id = ? AND status != 'refunded'",
        (course_id,),
    )


def rating(connection: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    """The average of a course's published reviews (``None`` when it has none) and how many there are."""
    row = connection.execute(
        "SELECT AVG(rating), COUNT(*) FROM ac_reviews WHERE course_id = ? AND status = 'published'",
        (course_id,),
    ).fetchone()
    count = int(row[1] or 0)
    return {"average": round(float(row[0]), 1) if count else None, "count": count}
