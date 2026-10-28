"""Academy rules: courses and their curriculum, enrolment (free, paid, codes, capacity, cohorts), progress,
server-graded quizzes, certificates, payments and the messages students receive."""

from __future__ import annotations

import re
import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from aira.suite import kit, public
from aira.suite.academy import partners, store
from aira.suite.academy.store import APP

MAX_OPTIONS = 6
MIN_OPTIONS = 2
MAX_QUESTIONS = 30
NUDGE_AFTER_DAYS = 7
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _code(prefix: str, groups: int = 1, size: int = 6) -> str:
    parts = [
        "".join(secrets.choice(_REF_ALPHABET) for _ in range(size))
        for _ in range(groups)
    ]
    return f"{prefix}-{'-'.join(parts)}"


# ---------------------------------------------------------------------------
# links students open (absolute addresses for email)
# ---------------------------------------------------------------------------


def absolute(path: str, via: Any = None) -> str:
    """A full address for a public page, for an email: ``via`` is the route's request or the visitor."""
    return kit.public_url(path, via)


def student_link(
    connection: sqlite3.Connection, enrolment: Mapping[str, Any]
) -> dict[str, Any]:
    course = store.course(connection, enrolment["course_id"])
    return public.create_link(
        connection, APP, "student", enrolment["id"], title=course["title"]
    )


def course_link(
    connection: sqlite3.Connection, course_id: str
) -> dict[str, Any] | None:
    return public.link_for(connection, APP, "course", course_id)


# ---------------------------------------------------------------------------
# courses
# ---------------------------------------------------------------------------


def _clean_course(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {
        **(current or {}),
        **{k: v for k, v in raw.items() if v is not None or k == "capacity"},
    }
    capacity = merged.get("capacity")
    return {
        "title": kit.text(
            merged.get("title"), "a course title", max_len=150, required=True
        ),
        "subtitle": kit.text(merged.get("subtitle"), "a subtitle", max_len=200),
        "description": kit.text(
            merged.get("description"), "a description", max_len=8000, multiline=True
        ),
        "level": kit.choice(merged.get("level"), "level", store.LEVELS, default="all"),
        "price_cents": kit.integer(
            merged.get("price_cents"),
            "the price in cents",
            default=0,
            minimum=0,
            maximum=10_000_000_00,
        ),
        "cover_url": kit.url(merged.get("cover_url"), "the cover image link"),
        "enrol_code": kit.text(
            merged.get("enrol_code"), "the enrolment code", max_len=40
        ),
        "capacity": None
        if capacity in (None, "", 0, "0")
        else kit.integer(capacity, "the capacity", minimum=1, maximum=100000),
    }


def create_course(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    store.ensure(connection)
    values = _clean_course(raw)
    values.update(
        id=kit.new_id(), status="draft", created_at=kit.now(), updated_at=kit.now()
    )
    connection.execute(
        """INSERT INTO ac_courses (id, title, subtitle, description, level, price_cents, cover_url, status, enrol_code, capacity, created_at, updated_at)
        VALUES (:id, :title, :subtitle, :description, :level, :price_cents, :cover_url, :status, :enrol_code, :capacity, :created_at, :updated_at)""",
        values,
    )
    kit.log(
        connection,
        APP,
        "course",
        f"Course created: {values['title']}",
        ref_type="course",
        ref_id=values["id"],
    )
    connection.commit()
    return store.course(connection, values["id"])


def update_course(
    connection: sqlite3.Connection, course_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.course(connection, course_id)
    values = _clean_course(raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "ac_courses", course_id, values)
    connection.commit()
    return store.course(connection, course_id)


def set_status(
    connection: sqlite3.Connection, course_id: str, status: Any
) -> dict[str, Any]:
    course = store.course(connection, course_id)
    status = kit.choice(status, "status", store.COURSE_STATUSES)
    values: dict[str, Any] = {"status": status, "updated_at": kit.now()}
    if status == "published":
        if not store.lessons(connection, course_id):
            raise ValueError("Add at least one lesson before publishing the course.")
        values["published_at"] = course["published_at"] or kit.now()
    kit.update_row(connection, "ac_courses", course_id, values)
    kit.log(
        connection,
        APP,
        status,
        f"Course {status}: {course['title']}",
        ref_type="course",
        ref_id=course_id,
    )
    connection.commit()
    if status == "published":
        # The page exists as soon as the course is on sale, so the link can be shared at once.
        public.create_link(connection, APP, "course", course_id, title=course["title"])
    return store.course(connection, course_id)


def delete_course(connection: sqlite3.Connection, course_id: str) -> None:
    course = store.course(connection, course_id)
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM ac_enrolments WHERE course_id = ?",
        (course_id,),
    ):
        raise ValueError(
            "This course has students. Archive it instead, so their access and records stay."
        )
    for lesson in store.lessons(connection, course_id):
        connection.execute(
            "DELETE FROM ac_questions WHERE lesson_id = ?", (lesson["id"],)
        )
    connection.execute("DELETE FROM ac_lessons WHERE course_id = ?", (course_id,))
    connection.execute("DELETE FROM ac_modules WHERE course_id = ?", (course_id,))
    connection.execute("DELETE FROM ac_cohorts WHERE course_id = ?", (course_id,))
    connection.execute("DELETE FROM ac_reviews WHERE course_id = ?", (course_id,))
    connection.execute("DELETE FROM ac_courses WHERE id = ?", (course_id,))
    public.revoke(connection, APP, "course", course_id)
    kit.log(
        connection,
        APP,
        "deleted",
        f"Course deleted: {course['title']}",
        ref_type="course",
        ref_id=course_id,
    )
    connection.commit()


def curriculum(
    connection: sqlite3.Connection, course_id: str, *, answers: bool = True
) -> list[dict[str, Any]]:
    """Modules with their lessons in order; ``answers`` adds each quiz with its correct options (owner only)."""
    lessons_by_module: dict[str, list[dict[str, Any]]] = {}
    for lesson in store.lessons(connection, course_id):
        lesson["preview"] = bool(lesson["preview"])
        quiz = store.questions(connection, lesson["id"])
        lesson["question_count"] = len(quiz)
        if answers:
            lesson["questions"] = quiz
        lessons_by_module.setdefault(lesson["module_id"], []).append(lesson)
    out = []
    for module in store.modules(connection, course_id):
        module["lessons"] = lessons_by_module.get(module["id"], [])
        out.append(module)
    return out


def course_detail(connection: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    course = store.course(connection, course_id)
    modules = curriculum(connection, course_id)
    lessons = [lesson for module in modules for lesson in module["lessons"]]
    return {
        "course": course,
        "modules": modules,
        "stats": {
            "lessons": len(lessons),
            "minutes": sum(int(lesson["minutes"] or 0) for lesson in lessons),
            "quizzes": sum(1 for lesson in lessons if lesson["question_count"]),
            "students": kit.count(
                connection,
                "SELECT COUNT(*) FROM ac_enrolments WHERE course_id = ? AND status IN ('active', 'completed')",
                (course_id,),
            ),
            "seats_taken": store.seats_taken(connection, course_id),
        },
        "cohorts": store.cohorts(connection, course_id),
        "link": course_link(connection, course_id),
        "rating": store.rating(connection, course_id),
    }


# ---------------------------------------------------------------------------
# modules, lessons, quizzes
# ---------------------------------------------------------------------------


def _next_position(
    connection: sqlite3.Connection, table: str, column: str, value: str
) -> int:
    row = connection.execute(
        f"SELECT COALESCE(MAX(position), -1) + 1 FROM {table} WHERE {column} = ?",  # noqa: S608 - internal names
        (value,),
    ).fetchone()
    return int(row[0])


def add_module(
    connection: sqlite3.Connection, course_id: str, title: Any
) -> dict[str, Any]:
    store.course(connection, course_id)
    module_id = kit.new_id()
    connection.execute(
        "INSERT INTO ac_modules (id, course_id, title, position, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            module_id,
            course_id,
            kit.text(title, "a module title", max_len=150, required=True),
            _next_position(connection, "ac_modules", "course_id", course_id),
            kit.now(),
        ),
    )
    _touch_course(connection, course_id)
    connection.commit()
    return kit.require_row(connection, "ac_modules", module_id, "module")


def rename_module(
    connection: sqlite3.Connection, module_id: str, title: Any
) -> dict[str, Any]:
    module = kit.require_row(connection, "ac_modules", module_id, "module")
    kit.update_row(
        connection,
        "ac_modules",
        module_id,
        {"title": kit.text(title, "a module title", max_len=150, required=True)},
    )
    _touch_course(connection, module["course_id"])
    connection.commit()
    return kit.require_row(connection, "ac_modules", module_id, "module")


def delete_module(connection: sqlite3.Connection, module_id: str) -> None:
    module = kit.require_row(connection, "ac_modules", module_id, "module")
    for lesson in kit.fetch_all(
        connection, "SELECT id FROM ac_lessons WHERE module_id = ?", (module_id,)
    ):
        _delete_lesson_rows(connection, lesson["id"])
    connection.execute("DELETE FROM ac_modules WHERE id = ?", (module_id,))
    _touch_course(connection, module["course_id"])
    connection.commit()


def _clean_lesson(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **{k: v for k, v in raw.items() if v is not None}}
    return {
        "title": kit.text(
            merged.get("title"), "a lesson title", max_len=150, required=True
        ),
        "body": kit.text(
            merged.get("body"), "the lesson text", max_len=60000, multiline=True
        ),
        "video_url": kit.url(merged.get("video_url"), "the video link"),
        "minutes": kit.integer(
            merged.get("minutes"), "the minutes", default=0, minimum=0, maximum=1440
        ),
        "preview": 1 if kit.flag(merged.get("preview")) else 0,
        "pass_mark": kit.integer(
            merged.get("pass_mark"), "the pass mark", default=70, minimum=1, maximum=100
        ),
    }


def add_lesson(
    connection: sqlite3.Connection, module_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    module = kit.require_row(connection, "ac_modules", module_id, "module")
    values = _clean_lesson(raw)
    values.update(
        id=kit.new_id(),
        course_id=module["course_id"],
        module_id=module_id,
        position=_next_position(connection, "ac_lessons", "module_id", module_id),
        created_at=kit.now(),
        updated_at=kit.now(),
    )
    connection.execute(
        """INSERT INTO ac_lessons (id, course_id, module_id, title, body, video_url, minutes, preview, pass_mark, position, created_at, updated_at)
        VALUES (:id, :course_id, :module_id, :title, :body, :video_url, :minutes, :preview, :pass_mark, :position, :created_at, :updated_at)""",
        values,
    )
    _touch_course(connection, module["course_id"])
    connection.commit()
    return store.lesson(connection, values["id"])


def update_lesson(
    connection: sqlite3.Connection, lesson_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.lesson(connection, lesson_id)
    values = _clean_lesson(raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "ac_lessons", lesson_id, values)
    _touch_course(connection, current["course_id"])
    connection.commit()
    return store.lesson(connection, lesson_id)


def _delete_lesson_rows(connection: sqlite3.Connection, lesson_id: str) -> None:
    connection.execute("DELETE FROM ac_questions WHERE lesson_id = ?", (lesson_id,))
    connection.execute("DELETE FROM ac_progress WHERE lesson_id = ?", (lesson_id,))
    connection.execute("DELETE FROM ac_lessons WHERE id = ?", (lesson_id,))


def delete_lesson(connection: sqlite3.Connection, lesson_id: str) -> None:
    lesson = store.lesson(connection, lesson_id)
    _delete_lesson_rows(connection, lesson_id)
    _touch_course(connection, lesson["course_id"])
    connection.commit()


def reorder(
    connection: sqlite3.Connection,
    course_id: str,
    module_ids: Sequence[str] | None,
    lessons: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    """Put modules (and lessons, possibly moved between modules of the course) in the given order."""
    store.course(connection, course_id)
    own_modules = {module["id"] for module in store.modules(connection, course_id)}
    if module_ids is not None:
        ids = [str(item) for item in module_ids]
        if set(ids) != own_modules or len(ids) != len(own_modules):
            raise ValueError("List every module of the course exactly once.")
        for position, module_id in enumerate(ids):
            connection.execute(
                "UPDATE ac_modules SET position = ? WHERE id = ?", (position, module_id)
            )
    if lessons is not None:
        own_lessons = {lesson["id"] for lesson in store.lessons(connection, course_id)}
        seen: list[str] = []
        for module_id, lesson_ids in lessons.items():
            if module_id not in own_modules:
                raise ValueError(
                    "A lesson can only move to a module of the same course."
                )
            for position, lesson_id in enumerate(str(item) for item in lesson_ids):
                if lesson_id not in own_lessons:
                    raise ValueError("That lesson is not part of this course.")
                seen.append(lesson_id)
                connection.execute(
                    "UPDATE ac_lessons SET module_id = ?, position = ? WHERE id = ?",
                    (module_id, position, lesson_id),
                )
        if len(seen) != len(set(seen)):
            raise ValueError("A lesson can only be listed once.")
    _touch_course(connection, course_id)
    connection.commit()
    return curriculum(connection, course_id)


def _clean_question(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("Each question needs a prompt and options.")
    options = raw.get("options")
    if not isinstance(options, list):
        raise ValueError("Each question needs a list of options.")
    cleaned = [kit.text(option, "an option", max_len=300) for option in options]
    cleaned = [option for option in cleaned if option]
    if not MIN_OPTIONS <= len(cleaned) <= MAX_OPTIONS:
        raise ValueError(f"A question needs {MIN_OPTIONS} to {MAX_OPTIONS} options.")
    correct = kit.integer(
        raw.get("correct"), "the correct option", minimum=0, maximum=len(cleaned) - 1
    )
    return {
        "prompt": kit.text(raw.get("prompt"), "a question", max_len=600, required=True),
        "options": cleaned,
        "correct": correct,
        "explanation": kit.text(
            raw.get("explanation"), "an explanation", max_len=1000, multiline=True
        ),
    }


def set_quiz(
    connection: sqlite3.Connection,
    lesson_id: str,
    questions: Any,
    pass_mark: Any = None,
) -> dict[str, Any]:
    """Replace a lesson's quiz (an empty list removes it)."""
    lesson = store.lesson(connection, lesson_id)
    if not isinstance(questions, list):
        raise ValueError("Send the questions as a list.")
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"A quiz can have at most {MAX_QUESTIONS} questions.")
    cleaned = [_clean_question(question) for question in questions]
    connection.execute("DELETE FROM ac_questions WHERE lesson_id = ?", (lesson_id,))
    for position, question in enumerate(cleaned):
        connection.execute(
            "INSERT INTO ac_questions (id, lesson_id, prompt, options, correct, explanation, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                kit.new_id(),
                lesson_id,
                question["prompt"],
                kit.dumps(question["options"]),
                question["correct"],
                question["explanation"],
                position,
            ),
        )
    if pass_mark is not None and pass_mark != "":
        connection.execute(
            "UPDATE ac_lessons SET pass_mark = ? WHERE id = ?",
            (
                kit.integer(pass_mark, "the pass mark", minimum=1, maximum=100),
                lesson_id,
            ),
        )
    _touch_course(connection, lesson["course_id"])
    connection.commit()
    return {
        "lesson": store.lesson(connection, lesson_id),
        "questions": store.questions(connection, lesson_id),
    }


def _touch_course(connection: sqlite3.Connection, course_id: str) -> None:
    connection.execute(
        "UPDATE ac_courses SET updated_at = ? WHERE id = ?", (kit.now(), course_id)
    )


# ---------------------------------------------------------------------------
# writing help (the model, with templates that are good on their own)
# ---------------------------------------------------------------------------


def _outline_template(topic: str, level: str) -> list[dict[str, Any]]:
    return [
        {
            "title": f"Getting started with {topic}",
            "lessons": [
                {
                    "title": f"What {topic} is and why it matters",
                    "summary": f"The big picture of {topic}, the words you will hear and what you will be able to do by the end.",
                },
                {
                    "title": "Setting up",
                    "summary": "What you need, how to prepare and the first small win.",
                },
            ],
        },
        {
            "title": "Core skills",
            "lessons": [
                {
                    "title": f"The fundamentals of {topic}",
                    "summary": "The ideas everything else builds on, with worked examples.",
                },
                {
                    "title": "Practice: your first project",
                    "summary": "A guided exercise that puts the fundamentals to work.",
                },
                {
                    "title": "Common mistakes and how to avoid them",
                    "summary": "What beginners get wrong and the habits that prevent it.",
                },
            ],
        },
        {
            "title": "Going further" if level != "beginner" else "Putting it together",
            "lessons": [
                {
                    "title": f"{topic} in the real world",
                    "summary": "Case studies and how professionals apply it.",
                },
                {
                    "title": "Your next steps",
                    "summary": "A plan to keep improving, resources and a final checklist.",
                },
            ],
        },
    ]


def _clean_outline(raw: Any) -> list[dict[str, Any]]:
    modules = []
    for module in raw if isinstance(raw, list) else []:
        if not isinstance(module, Mapping):
            continue
        title = kit.text(module.get("title"), "a module title", max_len=150)
        lessons = []
        for lesson in (
            module.get("lessons") if isinstance(module.get("lessons"), list) else []
        ):
            if isinstance(lesson, Mapping):
                lesson_title = kit.text(
                    lesson.get("title"), "a lesson title", max_len=150
                )
                if lesson_title:
                    lessons.append(
                        {
                            "title": lesson_title,
                            "summary": kit.text(
                                lesson.get("summary"),
                                "a summary",
                                max_len=1000,
                                multiline=True,
                            ),
                        }
                    )
            elif isinstance(lesson, str) and lesson.strip():
                lessons.append(
                    {
                        "title": kit.text(lesson, "a lesson title", max_len=150),
                        "summary": "",
                    }
                )
        if title and lessons:
            modules.append({"title": title, "lessons": lessons[:12]})
    return modules[:12]


def outline_draft(topic: Any, level: Any = "all") -> dict[str, Any]:
    topic_text = kit.text(topic, "a topic", max_len=200, required=True)
    level_text = kit.choice(level, "level", store.LEVELS, default="all")
    template = _outline_template(topic_text, level_text)
    prompt = (
        'You design online courses. Return JSON {"modules": [{"title": str, "lessons": [{"title": str, "summary": str}]}]} '
        f"for a course on: {topic_text}. Level: {level_text}. 3 to 6 modules, 2 to 5 lessons each, practical and in a logical order. "
        "Summaries are one or two sentences. No markdown."
    )
    drafted = kit.draft(prompt, {"modules": template})
    modules = _clean_outline(drafted.get("modules"))
    if not modules:
        return {"modules": template, "source": "template"}
    return {"modules": modules, "source": drafted["source"]}


def apply_outline(
    connection: sqlite3.Connection, course_id: str, modules: Any
) -> list[dict[str, Any]]:
    cleaned = _clean_outline(modules)
    if not cleaned:
        raise ValueError("The outline has no modules with lessons.")
    for module in cleaned:
        created = add_module(connection, course_id, module["title"])
        for lesson in module["lessons"]:
            add_lesson(
                connection,
                created["id"],
                {"title": lesson["title"], "body": lesson["summary"]},
            )
    return curriculum(connection, course_id)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split()))
    return [
        part.strip() for part in parts if len(part.split()) >= 6 and len(part) <= 280
    ]


def _quiz_template(lesson: Mapping[str, Any]) -> list[dict[str, Any]]:
    """True-or-false questions from the lesson's own sentences (the owner edits them), or a reflection question."""
    questions = [
        {
            "prompt": f"True or false: {sentence}",
            "options": ["True", "False"],
            "correct": 0,
            "explanation": "This is stated in the lesson.",
        }
        for sentence in _sentences(str(lesson.get("body") or ""))[:4]
    ]
    if not questions:
        questions.append(
            {
                "prompt": f"Have you completed the exercise in “{lesson.get('title', 'this lesson')}”?",
                "options": ["Yes, I have done it", "Not yet"],
                "correct": 0,
                "explanation": "Finish the exercise before moving on.",
            }
        )
    return questions


def quiz_draft(connection: sqlite3.Connection, lesson_id: str) -> dict[str, Any]:
    lesson = store.lesson(connection, lesson_id)
    template = _quiz_template(lesson)
    prompt = (
        'You write multiple-choice quizzes for an online lesson. Return JSON {"questions": [{"prompt": str, "options": [str], '
        '"correct": int (index of the right option), "explanation": str}]} with 3 to 5 questions, 3 or 4 options each, one right answer. '
        f"Lesson title: {lesson['title']}\nLesson text:\n{str(lesson['body'])[:6000]}"
    )
    drafted = kit.draft(prompt, {"questions": template})
    cleaned = []
    for question in drafted.get("questions") or []:
        try:
            cleaned.append(_clean_question(question))
        except ValueError:
            continue
    if not cleaned:
        return {"questions": template, "source": "template"}
    return {"questions": cleaned[:MAX_QUESTIONS], "source": drafted["source"]}


# ---------------------------------------------------------------------------
# cohorts
# ---------------------------------------------------------------------------


def _clean_cohort(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {
        **(current or {}),
        **{k: v for k, v in raw.items() if v is not None or k == "capacity"},
    }
    capacity = merged.get("capacity")
    return {
        "name": kit.text(
            merged.get("name"), "a cohort name", max_len=120, required=True
        ),
        "starts_on": kit.day(merged.get("starts_on"), "the start date"),
        "capacity": None
        if capacity in (None, "", 0, "0")
        else kit.integer(capacity, "the capacity", minimum=1, maximum=100000),
    }


def create_cohort(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    store.ensure(connection)
    course = store.course(connection, str(raw.get("course_id") or ""))
    values = _clean_cohort(raw)
    values.update(id=kit.new_id(), course_id=course["id"], created_at=kit.now())
    connection.execute(
        "INSERT INTO ac_cohorts (id, course_id, name, starts_on, capacity, created_at) VALUES (:id, :course_id, :name, :starts_on, :capacity, :created_at)",
        values,
    )
    connection.commit()
    return store.cohort(connection, values["id"])


def update_cohort(
    connection: sqlite3.Connection, cohort_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.cohort(connection, cohort_id)
    kit.update_row(connection, "ac_cohorts", cohort_id, _clean_cohort(raw, current))
    connection.commit()
    return store.cohort(connection, cohort_id)


def delete_cohort(connection: sqlite3.Connection, cohort_id: str) -> None:
    store.cohort(connection, cohort_id)
    connection.execute(
        "UPDATE ac_enrolments SET cohort_id = '' WHERE cohort_id = ?", (cohort_id,)
    )
    connection.execute("DELETE FROM ac_cohorts WHERE id = ?", (cohort_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# enrolment and payment
# ---------------------------------------------------------------------------


class AlreadyEnrolled(Exception):
    """The email already has a live enrolment in the course (its link was emailed again)."""

    def __init__(self, enrolment: dict[str, Any]) -> None:
        super().__init__("Already enrolled.")
        self.enrolment = enrolment


def _business_name(connection: sqlite3.Connection) -> str:
    return kit.business(connection)["name"] or "Your instructor"


def _enrolment_email(
    connection: sqlite3.Connection,
    enrolment: Mapping[str, Any],
    course: Mapping[str, Any],
    link: str,
) -> None:
    profile = kit.business(connection)
    lines = [f"Hi {enrolment['name']},", "", f"You're enrolled in {course['title']}."]
    if enrolment["status"] == "pending_payment":
        lines += [
            "",
            f"To start learning, pay {kit.fmt_money(enrolment['price_cents'], profile['currency'])} and use reference {enrolment['reference']}.",
        ]
        if profile["payment_link"]:
            lines.append(f"Pay here: {profile['payment_link']}")
        if profile["payment_instructions"]:
            lines.append(profile["payment_instructions"])
        lines.append("Your lessons unlock as soon as the payment is confirmed.")
    lines += [
        "",
        "Your course page (keep this link, it is your access):",
        link,
        "",
        _business_name(connection),
    ]
    kit.queue_email(
        connection,
        APP,
        "enrolment",
        to=enrolment["email"],
        subject=f"Welcome to {course['title']}",
        body="\n".join(lines),
        ref_type="enrolment",
        ref_id=enrolment["id"],
    )


def enrol(
    connection: sqlite3.Connection,
    course_id: str,
    raw: Mapping[str, Any],
    *,
    source: str = "public",
    mark_paid_now: bool = False,
    via: Any = None,
) -> dict[str, Any]:
    """Enrol a student. A public enrolment needs a published course, the right code and a free seat. Free
    courses start at once; paid ones wait for payment. The seat check and the insert happen in one write
    transaction, so two students can never take the same last seat. ``ref`` is a partner's code (the student
    came through their link). Returns the enrolment and the student's link."""
    store.ensure(connection)
    course = store.course(connection, course_id)
    if course["status"] == "archived" or (
        source == "public" and course["status"] != "published"
    ):
        raise ValueError("This course is not open for enrolment.")
    name = kit.text(raw.get("name"), "your name", max_len=120, required=True)
    email = kit.email(raw.get("email"), "your email", required=True)
    if source == "public" and course["enrol_code"]:
        given = kit.text(raw.get("code"), "the enrolment code", max_len=40)
        if given.strip().lower() != course["enrol_code"].strip().lower():
            raise ValueError(
                "That enrolment code is not right. Check it with your instructor."
            )
    cohort_id = str(raw.get("cohort_id") or "")
    if cohort_id:
        cohort = store.cohort(connection, cohort_id)
        if cohort["course_id"] != course_id:
            raise ValueError("That cohort belongs to another course.")
        if (
            source == "public"
            and cohort["starts_on"]
            and cohort["starts_on"] < kit.today()
        ):
            raise ValueError(
                f"The {cohort['name']} cohort has already started. Choose another one."
            )
    notes = (
        kit.text(raw.get("notes"), "notes", max_len=2000, multiline=True)
        if source == "manual"
        else ""
    )
    if source == "manual":
        partners.check_code(connection, raw.get("ref"), email)
    free = int(course["price_cents"]) == 0
    now = kit.now()
    enrolment = {
        "id": kit.new_id(),
        "course_id": course_id,
        "cohort_id": cohort_id,
        "name": name,
        "email": email,
        "status": "active" if free else "pending_payment",
        "reference": _code("AC"),
        "price_cents": int(course["price_cents"]),
        "source": "manual" if source == "manual" else "public",
        "notes": notes,
        "created_at": now,
        "activated_at": now if free else None,
        "last_activity_at": now,
    }
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = kit.fetch_one(
            connection,
            "SELECT * FROM ac_enrolments WHERE course_id = ? AND email = ? AND status != 'refunded'",
            (course_id, email),
        )
        if not existing:
            if course["capacity"] and store.seats_taken(connection, course_id) >= int(
                course["capacity"]
            ):
                raise ValueError("This course is full.")
            if cohort_id:
                cohort = store.cohort(connection, cohort_id)
                if cohort["capacity"] and store.seats_taken(
                    connection, course_id, cohort_id
                ) >= int(cohort["capacity"]):
                    raise ValueError(f"The {cohort['name']} cohort is full.")
            connection.execute(
                """INSERT INTO ac_enrolments (id, course_id, cohort_id, name, email, status, reference, price_cents, source, notes, created_at, activated_at, last_activity_at)
                VALUES (:id, :course_id, :cohort_id, :name, :email, :status, :reference, :price_cents, :source, :notes, :created_at, :activated_at, :last_activity_at)""",
                enrolment,
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    if existing:
        if source == "public":
            _enrolment_email(
                connection,
                existing,
                course,
                absolute(student_link(connection, existing)["path"], via),
            )
            connection.commit()
            raise AlreadyEnrolled(existing)
        raise ValueError(f"{email} is already enrolled in this course.")
    kit.log(
        connection,
        APP,
        "enrolled",
        f"{name} enrolled in {course['title']}",
        detail="free" if free else "waiting for payment",
        ref_type="enrolment",
        ref_id=enrolment["id"],
        public=source == "public",
    )
    partners.record(
        connection,
        raw.get("ref"),
        source_type="enrolment",
        source_id=enrolment["id"],
        item_type="course",
        item_id=course_id,
        label=course["title"],
        customer=name,
        customer_email=email,
        amount_cents=enrolment["price_cents"],
    )
    link = student_link(connection, enrolment)
    _enrolment_email(connection, enrolment, course, absolute(link["path"], via))
    connection.commit()
    if mark_paid_now and not free:
        mark_paid(connection, enrolment["id"], via=via)
    return {"enrolment": store.enrolment(connection, enrolment["id"]), "link": link}


def mark_paid(
    connection: sqlite3.Connection,
    enrolment_id: str,
    amount_cents: Any = None,
    *,
    via: Any = None,
) -> dict[str, Any]:
    """The business confirms the payment: the lessons unlock and the money is recorded once."""
    enrolment = store.enrolment(connection, enrolment_id)
    if enrolment["status"] == "refunded":
        raise ValueError(
            "This enrolment was refunded. Enrol the student again instead."
        )
    course = store.course(connection, enrolment["course_id"])
    amount = kit.integer(
        amount_cents,
        "the amount paid in cents",
        default=int(enrolment["price_cents"]),
        minimum=0,
        maximum=10_000_000_00,
    )
    if amount <= 0:
        raise ValueError("This course is free; there is nothing to mark paid.")
    already = (
        int(enrolment["paid_cents"] or 0) == amount
        and enrolment["status"] != "pending_payment"
    )
    values: dict[str, Any] = {"paid_cents": amount}
    if enrolment["status"] == "pending_payment":
        values.update(
            status="active", activated_at=kit.now(), last_activity_at=kit.now()
        )
    kit.update_row(connection, "ac_enrolments", enrolment_id, values)
    kit.record_revenue(
        connection,
        APP,
        source_type="enrolment",
        source_id=enrolment_id,
        amount_cents=amount,
        label=course["title"],
        customer=enrolment["name"],
    )
    partners.earn(connection, "enrolment", enrolment_id, amount)
    if not already:
        kit.log(
            connection,
            APP,
            "paid",
            f"{enrolment['name']} paid for {course['title']}",
            ref_type="enrolment",
            ref_id=enrolment_id,
            amount_cents=amount,
        )
        profile = kit.business(connection)
        link = absolute(student_link(connection, enrolment)["path"], via)
        kit.queue_email(
            connection,
            APP,
            "payment_receipt",
            to=enrolment["email"],
            subject=f"Payment received: {course['title']}",
            body="\n".join(
                [
                    f"Hi {enrolment['name']},",
                    "",
                    f"We received {kit.fmt_money(amount, profile['currency'])} for {course['title']} (reference {enrolment['reference']}).",
                    "Your lessons are unlocked:",
                    link,
                    "",
                    _business_name(connection),
                ]
            ),
            ref_type="enrolment",
            ref_id=enrolment_id,
        )
    connection.commit()
    return store.enrolment(connection, enrolment_id)


def refund(connection: sqlite3.Connection, enrolment_id: str) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    if enrolment["status"] == "refunded":
        return enrolment
    kit.update_row(
        connection,
        "ac_enrolments",
        enrolment_id,
        {"status": "refunded", "refunded_at": kit.now()},
    )
    kit.void_revenue(connection, APP, "enrolment", enrolment_id)
    partners.void(connection, "enrolment", enrolment_id)
    # A refunded student's rating no longer counts on the course page (the business can show it again).
    connection.execute(
        "UPDATE ac_reviews SET status = 'hidden', updated_at = ? WHERE enrolment_id = ? AND status = 'published'",
        (kit.now(), enrolment_id),
    )
    kit.log(
        connection,
        APP,
        "refunded",
        f"{enrolment['name']} was refunded",
        ref_type="enrolment",
        ref_id=enrolment_id,
        amount_cents=-int(enrolment["paid_cents"] or 0),
    )
    connection.commit()
    return store.enrolment(connection, enrolment_id)


def update_enrolment(
    connection: sqlite3.Connection, enrolment_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    values: dict[str, Any] = {}
    if "name" in raw:
        values["name"] = kit.text(raw["name"], "a name", max_len=120, required=True)
    if "notes" in raw:
        values["notes"] = kit.text(raw["notes"], "notes", max_len=2000, multiline=True)
    if "cohort_id" in raw:
        cohort_id = str(raw["cohort_id"] or "")
        if cohort_id:
            cohort = store.cohort(connection, cohort_id)
            if cohort["course_id"] != enrolment["course_id"]:
                raise ValueError("That cohort belongs to another course.")
            if (
                cohort_id != enrolment["cohort_id"]
                and cohort["capacity"]
                and store.seats_taken(connection, enrolment["course_id"], cohort_id)
                >= int(cohort["capacity"])
            ):
                raise ValueError(f"The {cohort['name']} cohort is full.")
        values["cohort_id"] = cohort_id
    kit.update_row(connection, "ac_enrolments", enrolment_id, values)
    connection.commit()
    return store.enrolment(connection, enrolment_id)


def resend(
    connection: sqlite3.Connection, enrolment_id: str, *, via: Any = None
) -> None:
    """Queue the welcome email (with the student's link) again."""
    enrolment = store.enrolment(connection, enrolment_id)
    course = store.course(connection, enrolment["course_id"])
    link = absolute(student_link(connection, enrolment)["path"], via)
    _enrolment_email(connection, enrolment, course, link)
    connection.commit()


# ---------------------------------------------------------------------------
# learning: progress, quizzes, completion
# ---------------------------------------------------------------------------


def can_learn(enrolment: Mapping[str, Any]) -> bool:
    return enrolment["status"] in ("active", "completed")


def progress(
    connection: sqlite3.Connection, enrolment: Mapping[str, Any]
) -> dict[str, Any]:
    lessons = store.lessons(connection, enrolment["course_id"])
    lesson_ids = {lesson["id"] for lesson in lessons}
    done = store.completed_lessons(connection, enrolment["id"]) & lesson_ids
    quizzes = store.quiz_lessons(connection, enrolment["course_id"]) & lesson_ids
    passed = store.passed_quizzes(connection, enrolment["id"]) & quizzes
    total = len(lesson_ids)
    return {
        "done": len(done),
        "total": total,
        "percent": round(len(done) * 100 / total) if total else 0,
        "quizzes_total": len(quizzes),
        "quizzes_passed": len(passed),
        "complete": bool(total) and len(done) == total and len(passed) == len(quizzes),
    }


def _touch(connection: sqlite3.Connection, enrolment_id: str) -> None:
    connection.execute(
        "UPDATE ac_enrolments SET last_activity_at = ? WHERE id = ?",
        (kit.now(), enrolment_id),
    )


def _lesson_of_course(
    connection: sqlite3.Connection, enrolment: Mapping[str, Any], lesson_id: str
) -> dict[str, Any]:
    lesson = store.lesson(connection, str(lesson_id or ""))
    if lesson["course_id"] != enrolment["course_id"]:
        raise LookupError("That lesson was not found.")
    return lesson


def _require_learning(enrolment: Mapping[str, Any]) -> None:
    if enrolment["status"] == "refunded":
        raise PermissionError("This enrolment was refunded.")
    if not can_learn(enrolment):
        raise PermissionError("Your lessons unlock once your payment is confirmed.")


def complete_lesson(
    connection: sqlite3.Connection,
    enrolment_id: str,
    lesson_id: str,
    *,
    via: Any = None,
) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    _require_learning(enrolment)
    lesson = _lesson_of_course(connection, enrolment, lesson_id)
    connection.execute(
        "INSERT OR IGNORE INTO ac_progress (enrolment_id, lesson_id, completed_at) VALUES (?, ?, ?)",
        (enrolment_id, lesson["id"], kit.now()),
    )
    _touch(connection, enrolment_id)
    connection.commit()
    return _after_progress(connection, enrolment_id, via)


def submit_quiz(
    connection: sqlite3.Connection,
    enrolment_id: str,
    lesson_id: str,
    answers: Any,
    *,
    via: Any = None,
) -> dict[str, Any]:
    """Grade on the server. Which answers were right is returned; the right options and the explanations only
    once the quiz is passed, so a retake is a real retake."""
    enrolment = store.enrolment(connection, enrolment_id)
    _require_learning(enrolment)
    lesson = _lesson_of_course(connection, enrolment, lesson_id)
    questions = store.questions(connection, lesson["id"])
    if not questions:
        raise ValueError("This lesson has no quiz.")
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise ValueError(f"Answer all {len(questions)} questions.")
    chosen = []
    for index, (answer, question) in enumerate(zip(answers, questions, strict=True)):
        chosen.append(
            kit.integer(
                answer,
                f"the answer to question {index + 1}",
                minimum=0,
                maximum=len(question["options"]) - 1,
            )
        )
    right = [
        chosen[i] == int(question["correct"]) for i, question in enumerate(questions)
    ]
    score = round(sum(right) * 100 / len(questions))
    passed = score >= int(lesson["pass_mark"])
    connection.execute(
        "INSERT INTO ac_attempts (id, enrolment_id, lesson_id, score, passed, answers, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            kit.new_id(),
            enrolment_id,
            lesson["id"],
            score,
            1 if passed else 0,
            kit.dumps(chosen),
            kit.now(),
        ),
    )
    if passed:
        connection.execute(
            "INSERT OR IGNORE INTO ac_progress (enrolment_id, lesson_id, completed_at) VALUES (?, ?, ?)",
            (enrolment_id, lesson["id"], kit.now()),
        )
    _touch(connection, enrolment_id)
    connection.commit()
    results = []
    for i, question in enumerate(questions):
        item: dict[str, Any] = {"correct": right[i]}
        if passed:
            item.update(
                answer=int(question["correct"]), explanation=question["explanation"]
            )
        results.append(item)
    state = _after_progress(connection, enrolment_id, via)
    return {
        "score": score,
        "passed": passed,
        "pass_mark": int(lesson["pass_mark"]),
        "results": results,
        **state,
    }


def _after_progress(
    connection: sqlite3.Connection, enrolment_id: str, via: Any = None
) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    state = progress(connection, enrolment)
    certificate = None
    if state["complete"] and enrolment["status"] == "active":
        certificate = issue_certificate(connection, enrolment_id, via=via)
    elif enrolment["status"] == "completed":
        certificate = public.link_for(connection, APP, "certificate", enrolment_id)
    return {
        "progress": state,
        "status": store.enrolment(connection, enrolment_id)["status"],
        "certificate": certificate,
    }


def issue_certificate(
    connection: sqlite3.Connection, enrolment_id: str, *, via: Any = None
) -> dict[str, Any]:
    """Complete the enrolment, give it a certificate code and a verification link, and tell the student."""
    enrolment = store.enrolment(connection, enrolment_id)
    if not progress(connection, enrolment)["complete"]:
        raise ValueError("The course is not complete yet.")
    course = store.course(connection, enrolment["course_id"])
    code = enrolment["certificate_code"] or _code("CERT", groups=2, size=4)
    kit.update_row(
        connection,
        "ac_enrolments",
        enrolment_id,
        {
            "status": "completed",
            "completed_at": enrolment["completed_at"] or kit.now(),
            "certificate_code": code,
        },
    )
    link = public.create_link(
        connection,
        APP,
        "certificate",
        enrolment_id,
        title=f"Certificate: {course['title']}",
    )
    if enrolment["status"] != "completed":
        kit.log(
            connection,
            APP,
            "completed",
            f"{enrolment['name']} completed {course['title']}",
            ref_type="enrolment",
            ref_id=enrolment_id,
            public=True,
        )
        kit.queue_email(
            connection,
            APP,
            "course_completed",
            to=enrolment["email"],
            subject=f"Congratulations, you completed {course['title']}",
            body="\n".join(
                [
                    f"Hi {enrolment['name']},",
                    "",
                    f"You completed {course['title']}. Well done!",
                    f"Your certificate (code {code}) is here, ready to print or share:",
                    absolute(link["path"], via),
                    "",
                    _business_name(connection),
                ]
            ),
            ref_type="enrolment",
            ref_id=enrolment_id,
        )
    connection.commit()
    return link


def certificate(connection: sqlite3.Connection, enrolment_id: str) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    if enrolment["status"] != "completed" or not enrolment["certificate_code"]:
        raise LookupError("This certificate is not available.")
    course = store.course(connection, enrolment["course_id"])
    lessons = store.lessons(connection, course["id"])
    return {
        "name": enrolment["name"],
        "course": course["title"],
        "level": course["level"],
        "completed_at": enrolment["completed_at"],
        "code": enrolment["certificate_code"],
        "issuer": _business_name(connection),
        "lessons": len(lessons),
        "minutes": sum(int(lesson["minutes"] or 0) for lesson in lessons),
        "valid": True,
    }


def verify_code(connection: sqlite3.Connection, code: Any) -> dict[str, Any] | None:
    """Find a certificate by its code (for the business, when someone asks whether a certificate is real)."""
    cleaned = kit.text(code, "a certificate code", max_len=40).upper()
    if not cleaned:
        return None
    row = kit.fetch_one(
        connection,
        "SELECT id FROM ac_enrolments WHERE certificate_code = ? AND status = 'completed'",
        (cleaned,),
    )
    return certificate(connection, row["id"]) if row else None


# ---------------------------------------------------------------------------
# what students see on their own page
# ---------------------------------------------------------------------------


def student_view(connection: sqlite3.Connection, enrolment_id: str) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    course = store.course(connection, enrolment["course_id"])
    learning = can_learn(enrolment)
    done = store.completed_lessons(connection, enrolment_id)
    passed = store.passed_quizzes(connection, enrolment_id)
    best = store.best_scores(connection, enrolment_id)
    modules = []
    for module in curriculum(connection, course["id"], answers=False):
        lessons = []
        for lesson in module["lessons"]:
            unlocked = learning or lesson["preview"]
            item: dict[str, Any] = {
                "id": lesson["id"],
                "title": lesson["title"],
                "minutes": lesson["minutes"],
                "preview": lesson["preview"],
                "locked": not unlocked,
                "completed": lesson["id"] in done,
                "has_quiz": bool(lesson["question_count"]),
                "quiz_passed": lesson["id"] in passed,
                "best_score": best.get(lesson["id"]),
                "pass_mark": lesson["pass_mark"],
            }
            if unlocked:
                item["body"] = lesson["body"]
                item["video_url"] = lesson["video_url"]
                if lesson["question_count"] and learning:
                    item["questions"] = [
                        {"prompt": question["prompt"], "options": question["options"]}
                        for question in store.questions(connection, lesson["id"])
                    ]
            lessons.append(item)
        modules.append(
            {"id": module["id"], "title": module["title"], "lessons": lessons}
        )
    cohort = (
        store.cohort(connection, enrolment["cohort_id"])
        if enrolment["cohort_id"]
        else None
    )
    review = kit.fetch_one(
        connection,
        "SELECT rating, comment, status FROM ac_reviews WHERE enrolment_id = ?",
        (enrolment_id,),
    )
    return {
        "student": {
            "name": enrolment["name"],
            "status": enrolment["status"],
            "reference": enrolment["reference"],
        },
        "review": review,
        "can_review": can_review(connection, enrolment),
        "course": {
            "title": course["title"],
            "subtitle": course["subtitle"],
            "cover_url": course["cover_url"],
            "level": course["level"],
        },
        "cohort": {"name": cohort["name"], "starts_on": cohort["starts_on"]}
        if cohort
        else None,
        "amount_due_cents": int(enrolment["price_cents"])
        if enrolment["status"] == "pending_payment"
        else 0,
        "progress": progress(connection, enrolment),
        "modules": modules,
        "certificate": public.link_for(connection, APP, "certificate", enrolment_id)
        if enrolment["status"] == "completed"
        else None,
    }


def course_page(connection: sqlite3.Connection, course_id: str) -> dict[str, Any]:
    course = store.course(connection, course_id)
    if course["status"] != "published":
        raise LookupError("This course is not available.")
    modules = []
    minutes = 0
    for module in curriculum(connection, course_id, answers=False):
        lessons = []
        for lesson in module["lessons"]:
            minutes += int(lesson["minutes"] or 0)
            item = {
                "title": lesson["title"],
                "minutes": lesson["minutes"],
                "preview": lesson["preview"],
                "has_quiz": bool(lesson["question_count"]),
            }
            if lesson["preview"]:
                item.update(body=lesson["body"], video_url=lesson["video_url"])
            lessons.append(item)
        modules.append({"title": module["title"], "lessons": lessons})
    seats_left = None
    if course["capacity"]:
        seats_left = max(
            0, int(course["capacity"]) - store.seats_taken(connection, course_id)
        )
    cohorts = []
    for cohort in store.cohorts(connection, course_id):
        if cohort["starts_on"] and cohort["starts_on"] < kit.today():
            continue
        left = (
            None
            if not cohort["capacity"]
            else max(0, int(cohort["capacity"]) - int(cohort["students"]))
        )
        cohorts.append(
            {
                "id": cohort["id"],
                "name": cohort["name"],
                "starts_on": cohort["starts_on"],
                "seats_left": left,
            }
        )
    return {
        "course": {
            "title": course["title"],
            "subtitle": course["subtitle"],
            "description": course["description"],
            "level": course["level"],
            "price_cents": course["price_cents"],
            "cover_url": course["cover_url"],
        },
        "modules": modules,
        "lessons": sum(len(module["lessons"]) for module in modules),
        "minutes": minutes,
        "students": kit.count(
            connection,
            "SELECT COUNT(*) FROM ac_enrolments WHERE course_id = ? AND status IN ('active', 'completed')",
            (course_id,),
        ),
        "seats_left": seats_left,
        "requires_code": bool(course["enrol_code"]),
        "cohorts": cohorts,
        "rating": store.rating(connection, course_id),
        "reviews": public_reviews(connection, course_id),
    }


# ---------------------------------------------------------------------------
# reviews: students rate the course; the business may hide one
# ---------------------------------------------------------------------------


def _short_name(name: str) -> str:
    """ "Ana Maria Lopez" -> "Ana L.": enough to be real, not enough to find the person."""
    parts = str(name or "").split()
    if not parts:
        return "A student"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0].upper()}."


def can_review(connection: sqlite3.Connection, enrolment: Mapping[str, Any]) -> bool:
    """A student may rate a course once they have learned something: a lesson done, or the course completed."""
    if enrolment["status"] == "completed":
        return True
    return enrolment["status"] == "active" and bool(
        store.completed_lessons(connection, enrolment["id"])
    )


def submit_review(
    connection: sqlite3.Connection, enrolment_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """A student's rating (1 to 5) and comment; sending again updates it (one review per student)."""
    enrolment = store.enrolment(connection, enrolment_id)
    if enrolment["status"] in ("refunded", "pending_payment"):
        raise PermissionError("Only students who are learning can review the course.")
    if not can_review(connection, enrolment):
        raise ValueError("Finish a lesson first, then tell others what you think.")
    rating = kit.integer(
        raw.get("rating"), "a rating from 1 to 5", minimum=1, maximum=5
    )
    comment = kit.text(raw.get("comment"), "your review", max_len=1500, multiline=True)
    now = kit.now()
    existing = kit.fetch_one(
        connection, "SELECT * FROM ac_reviews WHERE enrolment_id = ?", (enrolment_id,)
    )
    if existing:
        kit.update_row(
            connection,
            "ac_reviews",
            existing["id"],
            {"rating": rating, "comment": comment, "updated_at": now},
        )
        review_id = existing["id"]
    else:
        review_id = kit.new_id()
        connection.execute(
            """INSERT INTO ac_reviews (id, course_id, enrolment_id, name, rating, comment, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?)""",
            (
                review_id,
                enrolment["course_id"],
                enrolment_id,
                _short_name(enrolment["name"]),
                rating,
                comment,
                now,
                now,
            ),
        )
        course = store.course(connection, enrolment["course_id"])
        kit.log(
            connection,
            APP,
            "review",
            f"{enrolment['name']} rated {course['title']} {rating} out of 5",
            detail=comment[:140],
            ref_type="review",
            ref_id=review_id,
            public=True,
        )
    _touch(connection, enrolment_id)
    connection.commit()
    return kit.require_row(connection, "ac_reviews", review_id, "review")


def public_reviews(
    connection: sqlite3.Connection, course_id: str, limit: int = 12
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        """SELECT name, rating, comment, created_at FROM ac_reviews WHERE course_id = ? AND status = 'published'
        ORDER BY (comment != '') DESC, created_at DESC LIMIT ?""",
        (course_id, limit),
    )


def reviews(
    connection: sqlite3.Connection, *, course_id: str = "", status: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if course_id:
        clauses.append("r.course_id = ?")
        params.append(course_id)
    if status:
        clauses.append("r.status = ?")
        params.append(kit.choice(status, "the status", store.REVIEW_STATUSES))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return kit.fetch_all(
        connection,
        f"""SELECT r.*, c.title AS course_title, e.name AS student_name, e.email AS student_email
        FROM ac_reviews r JOIN ac_courses c ON c.id = r.course_id LEFT JOIN ac_enrolments e ON e.id = r.enrolment_id
        {where} ORDER BY r.created_at DESC LIMIT 1000""",  # noqa: S608 - fixed clauses
        params,
    )


def set_review_status(
    connection: sqlite3.Connection, review_id: str, status: Any
) -> dict[str, Any]:
    kit.require_row(connection, "ac_reviews", review_id, "review")
    kit.update_row(
        connection,
        "ac_reviews",
        review_id,
        {
            "status": kit.choice(status, "the status", store.REVIEW_STATUSES),
            "updated_at": kit.now(),
        },
    )
    connection.commit()
    return kit.require_row(connection, "ac_reviews", review_id, "review")


# ---------------------------------------------------------------------------
# messages to students
# ---------------------------------------------------------------------------


def announce(
    connection: sqlite3.Connection, raw: Mapping[str, Any], *, via: Any = None
) -> dict[str, Any]:
    store.ensure(connection)
    course = store.course(connection, str(raw.get("course_id") or ""))
    subject = kit.text(raw.get("subject"), "a subject", max_len=200, required=True)
    body = kit.text(
        raw.get("body"), "a message", max_len=10000, required=True, multiline=True
    )
    cohort_id = str(raw.get("cohort_id") or "")
    sql = "SELECT * FROM ac_enrolments WHERE course_id = ? AND status IN ('active', 'completed')"
    params: list[Any] = [course["id"]]
    if cohort_id:
        sql += " AND cohort_id = ?"
        params.append(cohort_id)
    students = kit.fetch_all(connection, sql, params)
    for student in students:
        link = absolute(student_link(connection, student)["path"], via)
        kit.queue_email(
            connection,
            APP,
            "announcement",
            to=student["email"],
            subject=subject,
            body=f"Hi {student['name']},\n\n{body}\n\nYour course: {link}\n\n{_business_name(connection)}",
            ref_type="enrolment",
            ref_id=student["id"],
        )
    announcement = {
        "id": kit.new_id(),
        "course_id": course["id"],
        "subject": subject,
        "body": body,
        "recipients": len(students),
        "created_at": kit.now(),
    }
    connection.execute(
        "INSERT INTO ac_announcements (id, course_id, subject, body, recipients, created_at) VALUES (:id, :course_id, :subject, :body, :recipients, :created_at)",
        announcement,
    )
    kit.log(
        connection,
        APP,
        "announcement",
        f"Announcement to {len(students)} students: {subject}",
        ref_type="course",
        ref_id=course["id"],
    )
    connection.commit()
    return announcement


def announcements(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        """SELECT a.*, c.title AS course_title FROM ac_announcements a LEFT JOIN ac_courses c ON c.id = a.course_id
        ORDER BY a.created_at DESC LIMIT 200""",
    )


def inactive(
    connection: sqlite3.Connection,
    *,
    days: int = NUDGE_AFTER_DAYS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Active students with no activity for ``days`` who were not nudged in that time."""
    cutoff = kit.iso((now or kit.utcnow()) - timedelta(days=days))
    return kit.fetch_all(
        connection,
        """SELECT e.*, c.title AS course_title FROM ac_enrolments e JOIN ac_courses c ON c.id = e.course_id
        WHERE e.status = 'active' AND COALESCE(e.last_activity_at, e.activated_at, e.created_at) < ?
        AND (e.nudged_at IS NULL OR e.nudged_at < ?) ORDER BY e.last_activity_at""",
        (cutoff, cutoff),
    )


def nudge_inactive(
    connection: sqlite3.Connection,
    *,
    days: int = NUDGE_AFTER_DAYS,
    now: datetime | None = None,
    via: Any = None,
) -> dict[str, Any]:
    students = inactive(connection, days=days, now=now)
    for student in students:
        state = progress(connection, student)
        link = absolute(student_link(connection, student)["path"], via)
        kit.queue_email(
            connection,
            APP,
            "nudge",
            to=student["email"],
            subject=f"Pick up where you left off in {student['course_title']}",
            body="\n".join(
                [
                    f"Hi {student['name']},",
                    "",
                    f"You're {state['percent']}% through {student['course_title']} ({state['done']} of {state['total']} lessons).",
                    "A few minutes today keeps the momentum going:",
                    link,
                    "",
                    _business_name(connection),
                ]
            ),
            ref_type="enrolment",
            ref_id=student["id"],
        )
        connection.execute(
            "UPDATE ac_enrolments SET nudged_at = ? WHERE id = ?",
            (kit.iso(now or kit.utcnow()), student["id"]),
        )
    if students:
        kit.log(
            connection,
            APP,
            "nudge",
            f"Reminders queued for {len(students)} inactive students",
        )
    connection.commit()
    return {"queued": len(students)}


# ---------------------------------------------------------------------------
# lists and numbers
# ---------------------------------------------------------------------------


def students(
    connection: sqlite3.Connection,
    *,
    course_id: str = "",
    status: str = "",
    cohort_id: str = "",
    q: str = "",
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if course_id:
        clauses.append("e.course_id = ?")
        params.append(course_id)
    if status:
        clauses.append("e.status = ?")
        params.append(kit.choice(status, "status", store.ENROLMENT_STATUSES))
    if cohort_id:
        clauses.append("e.cohort_id = ?")
        params.append(cohort_id)
    if q:
        clauses.append("(e.name LIKE ? OR e.email LIKE ? OR e.reference LIKE ?)")
        params += [f"%{q}%"] * 3
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"""SELECT e.*, c.title AS course_title, co.name AS cohort_name FROM ac_enrolments e
        JOIN ac_courses c ON c.id = e.course_id LEFT JOIN ac_cohorts co ON co.id = e.cohort_id
        {where} ORDER BY e.created_at DESC LIMIT 2000""",  # noqa: S608 - fixed clauses
        params,
    )
    for row in rows:
        row["progress"] = progress(connection, row)["percent"]
    return rows


def student_detail(connection: sqlite3.Connection, enrolment_id: str) -> dict[str, Any]:
    enrolment = store.enrolment(connection, enrolment_id)
    course = store.course(connection, enrolment["course_id"])
    attempts = kit.fetch_all(
        connection,
        """SELECT a.score, a.passed, a.at, l.title AS lesson_title FROM ac_attempts a JOIN ac_lessons l ON l.id = a.lesson_id
        WHERE a.enrolment_id = ? ORDER BY a.at DESC LIMIT 100""",
        (enrolment_id,),
    )
    for attempt in attempts:
        attempt["passed"] = bool(attempt["passed"])
    return {
        "student": enrolment,
        "course": {
            "id": course["id"],
            "title": course["title"],
            "price_cents": course["price_cents"],
        },
        "progress": progress(connection, enrolment),
        "attempts": attempts,
        "link": public.link_for(connection, APP, "student", enrolment_id),
        "certificate": public.link_for(connection, APP, "certificate", enrolment_id),
        "cohorts": store.cohorts(connection, course["id"]),
    }


def _overall_rating(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT AVG(rating), COUNT(*) FROM ac_reviews WHERE status = 'published'"
    ).fetchone()
    count = int(row[1] or 0)
    return {"average": round(float(row[0]), 1) if count else None, "count": count}


def overview(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> dict[str, Any]:
    store.ensure(connection)
    week_ago = kit.iso((now or kit.utcnow()) - timedelta(days=7))
    learning = kit.count(
        connection,
        "SELECT COUNT(*) FROM ac_enrolments WHERE status IN ('active', 'completed')",
    )
    completed = kit.count(
        connection, "SELECT COUNT(*) FROM ac_enrolments WHERE status = 'completed'"
    )
    revenue = kit.revenue(connection, app=APP, moment=now)
    top = kit.fetch_all(
        connection,
        """SELECT c.id, c.title, c.status, c.price_cents,
            COUNT(e.id) AS students,
            SUM(CASE WHEN e.status = 'completed' THEN 1 ELSE 0 END) AS completed
        FROM ac_courses c LEFT JOIN ac_enrolments e ON e.course_id = c.id AND e.status IN ('active', 'completed')
        GROUP BY c.id ORDER BY students DESC, c.updated_at DESC LIMIT 5""",
    )
    return {
        "students": learning,
        "active_this_week": kit.count(
            connection,
            "SELECT COUNT(*) FROM ac_enrolments WHERE status IN ('active', 'completed') AND last_activity_at >= ?",
            (week_ago,),
        ),
        "completion_rate": round(completed * 100 / learning) if learning else 0,
        "completed": completed,
        "pending_payments": kit.count(
            connection,
            "SELECT COUNT(*) FROM ac_enrolments WHERE status = 'pending_payment'",
        ),
        "revenue_this_month_cents": revenue["this_month_cents"],
        "revenue_total_cents": revenue["total_cents"],
        "courses": kit.count(connection, "SELECT COUNT(*) FROM ac_courses"),
        "published": kit.count(
            connection, "SELECT COUNT(*) FROM ac_courses WHERE status = 'published'"
        ),
        "inactive": len(inactive(connection, now=now)),
        "rating": _overall_rating(connection),
        "top_courses": top,
        "currency": kit.business(connection)["currency"],
    }
