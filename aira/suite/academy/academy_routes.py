"""Academy routes (``/api/academy/*``), its public pages (a course page, a student's own page, a certificate)
and its Business hub checklist and numbers. Loaded by :func:`aira.suite.load_apps`."""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.academy import service, store
from aira.suite.academy.store import APP

BASE = "/api/academy"
ID = "([0-9a-f]{32})"


def _open(request: Request) -> Any:
    connection = request.connect()
    store.ensure(connection)
    return connection


def _actor(request: Request) -> str:
    return str((request.user or {}).get("email") or "")


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        data = service.overview(connection)
        data["checklist"] = _checklist(connection)
        data["activity"] = kit.activity(connection, app=APP, limit=15)
    return 200, data


# ---------------------------------------------------------------------------
# courses and curriculum
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/courses")
def courses_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        rows = store.courses(connection, status=request.q("status"), q=request.q("q"))
        for row in rows:
            row["link"] = service.course_link(connection, row["id"])
    return 200, {"courses": rows}


@aira_route("POST", f"{BASE}/courses")
def courses_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        course = service.create_course(connection, request.payload)
    request.audit("academy.course_create", "course", course["id"])
    return 201, {"course": course}


@aira_route("GET", f"{BASE}/courses/{ID}")
def course_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.course_detail(connection, request.match.group(1))


@aira_route("PATCH", f"{BASE}/courses/{ID}")
def course_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        course = service.update_course(
            connection, request.match.group(1), request.payload
        )
    request.audit("academy.course_update", "course", course["id"])
    return 200, {"course": course}


@aira_route("DELETE", f"{BASE}/courses/{ID}")
def course_delete(request: Request) -> tuple[int, dict[str, Any]]:
    course_id = request.match.group(1)
    with _open(request) as connection:
        service.delete_course(connection, course_id)
    request.audit("academy.course_delete", "course", course_id)
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/courses/{ID}/status")
def course_status(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        course = service.set_status(
            connection, request.match.group(1), request.payload.get("status")
        )
    request.audit(f"academy.course_{course['status']}", "course", course["id"])
    return 200, {"course": course}


@aira_route("POST", f"{BASE}/courses/{ID}/link")
def course_link_create(request: Request) -> tuple[int, dict[str, Any]]:
    course_id = request.match.group(1)
    with _open(request) as connection:
        course = store.course(connection, course_id)
        if course["status"] != "published":
            raise ValueError("Publish the course before sharing its page.")
        link = public.create_link(
            connection, APP, "course", course_id, title=course["title"]
        )
    request.audit("academy.course_share", "course", course_id)
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/courses/{ID}/link")
def course_link_revoke(request: Request) -> tuple[int, dict[str, Any]]:
    course_id = request.match.group(1)
    with _open(request) as connection:
        store.course(connection, course_id)
        revoked = public.revoke(connection, APP, "course", course_id)
    request.audit("academy.course_unshare", "course", course_id)
    return 200, {"revoked": revoked}


@aira_route("POST", f"{BASE}/courses/{ID}/modules")
def module_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 201, {
            "module": service.add_module(
                connection, request.match.group(1), request.payload.get("title")
            )
        }


@aira_route("PATCH", f"{BASE}/modules/{ID}")
def module_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "module": service.rename_module(
                connection, request.match.group(1), request.payload.get("title")
            )
        }


@aira_route("DELETE", f"{BASE}/modules/{ID}")
def module_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        service.delete_module(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/courses/{ID}/reorder")
def course_reorder(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    modules = payload.get("modules")
    lessons = payload.get("lessons")
    if modules is not None and not isinstance(modules, list):
        raise ValueError("modules must be a list of module ids.")
    if lessons is not None and not isinstance(lessons, dict):
        raise ValueError("lessons must map each module id to its lesson ids.")
    with _open(request) as connection:
        return 200, {
            "modules": service.reorder(
                connection, request.match.group(1), modules, lessons
            )
        }


@aira_route("POST", f"{BASE}/modules/{ID}/lessons")
def lesson_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 201, {
            "lesson": service.add_lesson(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("PATCH", f"{BASE}/lessons/{ID}")
def lesson_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "lesson": service.update_lesson(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/lessons/{ID}")
def lesson_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        service.delete_lesson(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/lessons/{ID}/quiz")
def lesson_quiz(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.set_quiz(
            connection,
            request.match.group(1),
            request.payload.get("questions"),
            request.payload.get("pass_mark"),
        )


@aira_route("POST", f"{BASE}/lessons/{ID}/quiz-draft")
def quiz_draft(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.quiz_draft(connection, request.match.group(1))


@aira_route("POST", f"{BASE}/outline-draft")
def outline_draft(request: Request) -> tuple[int, dict[str, Any]]:
    return 200, service.outline_draft(
        request.payload.get("topic"), request.payload.get("level") or "all"
    )


@aira_route("POST", f"{BASE}/courses/{ID}/outline")
def outline_apply(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "modules": service.apply_outline(
                connection, request.match.group(1), request.payload.get("modules")
            )
        }


# ---------------------------------------------------------------------------
# students
# ---------------------------------------------------------------------------

STUDENT_COLUMNS = (
    "name",
    "email",
    "course_title",
    "cohort_name",
    "status",
    "progress",
    "reference",
    "price_cents",
    "paid_cents",
    "certificate_code",
    "created_at",
    "activated_at",
    "completed_at",
    "last_activity_at",
)


@aira_route("GET", f"{BASE}/students")
def students_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        rows = service.students(
            connection,
            course_id=request.q("course_id"),
            status=request.q("status"),
            cohort_id=request.q("cohort_id"),
            q=request.q("q"),
        )
    if request.q("format") == "csv":
        return Raw(
            200,
            kit.csv_bytes(rows, STUDENT_COLUMNS),
            "text/csv; charset=utf-8",
            filename="students.csv",
        )
    return 200, {"students": rows}


@aira_route("POST", f"{BASE}/students")
def students_create(request: Request) -> tuple[int, dict[str, Any]]:
    """Enrol a student by hand (no code needed; ``paid`` marks a paid course paid at once)."""
    with _open(request) as connection:
        result = service.enrol(
            connection,
            str(request.payload.get("course_id") or ""),
            request.payload,
            source="manual",
            mark_paid_now=kit.flag(request.payload.get("paid")),
            via=request,
        )
    request.audit("academy.student_enrol", "enrolment", result["enrolment"]["id"])
    return 201, result


@aira_route("GET", f"{BASE}/students/{ID}")
def student_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.student_detail(connection, request.match.group(1))


@aira_route("PATCH", f"{BASE}/students/{ID}")
def student_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "student": service.update_enrolment(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/students/{ID}/paid")
def student_paid(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        student = service.mark_paid(
            connection,
            request.match.group(1),
            request.payload.get("amount_cents"),
            via=request,
        )
    request.audit(
        "academy.student_paid",
        "enrolment",
        student["id"],
        {"amount_cents": student["paid_cents"]},
    )
    return 200, {"student": student}


@aira_route("POST", f"{BASE}/students/{ID}/refund")
def student_refund(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        student = service.refund(connection, request.match.group(1))
    request.audit("academy.student_refund", "enrolment", student["id"])
    return 200, {"student": student}


@aira_route("POST", f"{BASE}/students/{ID}/link")
def student_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        link = service.student_link(
            connection, store.enrolment(connection, request.match.group(1))
        )
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/students/{ID}/link")
def student_link_revoke(request: Request) -> tuple[int, dict[str, Any]]:
    """Turn off a student's link (shared by mistake); a new one is made the next time it is needed."""
    enrolment_id = request.match.group(1)
    with _open(request) as connection:
        store.enrolment(connection, enrolment_id)
        revoked = public.revoke(connection, APP, "student", enrolment_id)
    request.audit("academy.student_link_revoke", "enrolment", enrolment_id)
    return 200, {"revoked": revoked}


@aira_route("POST", f"{BASE}/students/{ID}/resend")
def student_resend(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        service.resend(connection, request.match.group(1), via=request)
    return 200, {"queued": True}


@aira_route("GET", f"{BASE}/certificates")
def certificate_lookup(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        found = service.verify_code(connection, request.q("code"))
    if not found:
        raise LookupError("No certificate has that code.")
    return 200, {"certificate": found}


@aira_route("POST", f"{BASE}/nudges")
def nudges(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.nudge_inactive(connection, via=request)


# ---------------------------------------------------------------------------
# cohorts and announcements
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/cohorts")
def cohorts_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {"cohorts": store.cohorts(connection, request.q("course_id"))}


@aira_route("POST", f"{BASE}/cohorts")
def cohorts_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 201, {"cohort": service.create_cohort(connection, request.payload)}


@aira_route("PATCH", f"{BASE}/cohorts/{ID}")
def cohort_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "cohort": service.update_cohort(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/cohorts/{ID}")
def cohort_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        service.delete_cohort(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/announcements")
def announcements_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {"announcements": service.announcements(connection)}


@aira_route("POST", f"{BASE}/announcements")
def announcements_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        announcement = service.announce(connection, request.payload, via=request)
    request.audit(
        "academy.announce",
        "course",
        announcement["course_id"],
        {"recipients": announcement["recipients"]},
    )
    return 201, {"announcement": announcement}


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, "course")
def _course_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.course_page(connection, link["ref_id"])


@public.action(APP, "course", "enrol")
def _course_enrol(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    service.course_page(connection, link["ref_id"])  # 404 once unpublished
    fields = {key: payload.get(key) for key in ("name", "email", "code", "cohort_id")}
    try:
        result = service.enrol(
            connection, link["ref_id"], fields, source="public", via=visitor
        )
    except service.AlreadyEnrolled:
        return 200, {
            "already": True,
            "message": "You're already enrolled. We've sent your course link to your email again.",
        }
    enrolment = result["enrolment"]
    return 201, {
        "enrolment": {
            "status": enrolment["status"],
            "reference": enrolment["reference"],
            "amount_due_cents": enrolment["price_cents"]
            if enrolment["status"] == "pending_payment"
            else 0,
        },
        "student_path": result["link"]["path"],
    }


@public.page(APP, "student")
def _student_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.student_view(connection, link["ref_id"])


@public.action(APP, "student", "complete")
def _student_complete(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, service.complete_lesson(
        connection, link["ref_id"], str(payload.get("lesson_id") or ""), via=visitor
    )


@public.action(APP, "student", "quiz")
def _student_quiz(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 200, service.submit_quiz(
        connection,
        link["ref_id"],
        str(payload.get("lesson_id") or ""),
        payload.get("answers"),
        via=visitor,
    )


@public.page(APP, "certificate")
def _certificate_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.certificate(connection, link["ref_id"])


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    store.ensure(connection)
    profile = kit.business(connection)
    shared = kit.count(
        connection,
        "SELECT COUNT(*) FROM suite_public_links WHERE app = ? AND kind = 'course' AND revoked_at IS NULL",
        (APP,),
    )
    return [
        kit.step(
            "profile",
            "Add your business details",
            bool(profile["name"]),
            "Business hub > Business profile",
        ),
        kit.step(
            "course",
            "Create your first course",
            kit.count(connection, "SELECT COUNT(*) FROM ac_courses") > 0,
            "Title, price and a short description",
        ),
        kit.step(
            "lesson",
            "Add a lesson",
            kit.count(connection, "SELECT COUNT(*) FROM ac_lessons") > 0,
            "Or let Aira draft the outline",
        ),
        kit.step(
            "publish",
            "Publish a course",
            kit.count(
                connection, "SELECT COUNT(*) FROM ac_courses WHERE status = 'published'"
            )
            > 0,
            "Students can enrol once it is published",
        ),
        kit.step(
            "share",
            "Share the course page",
            shared > 0,
            "Post the link where your students are",
        ),
        kit.step(
            "student",
            "Get your first student",
            kit.count(connection, "SELECT COUNT(*) FROM ac_enrolments") > 0,
            "",
        ),
    ]


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    data = service.overview(connection)
    return {
        "cards": [
            {"label": "Students", "value": data["students"]},
            {"label": "Active this week", "value": data["active_this_week"]},
            {"label": "Completion", "value": f"{data['completion_rate']}%"},
            {
                "label": "Waiting for payment",
                "value": data["pending_payments"],
                "tone": "warn" if data["pending_payments"] else "",
            },
        ]
    }
