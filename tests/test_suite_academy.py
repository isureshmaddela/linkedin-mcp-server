"""Academy (aira/suite/academy): courses, enrolment, server-graded quizzes, certificates, payments and messages,
in unit tests, through the single-user server and through the hosted gateway."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.academy import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

QUIZ = [
    {
        "prompt": "2 + 2?",
        "options": ["3", "4", "5"],
        "correct": 1,
        "explanation": "Two pairs.",
    },
    {
        "prompt": "Capital of France?",
        "options": ["Paris", "Rome"],
        "correct": 0,
        "explanation": "Paris.",
    },
]


@pytest.fixture(autouse=True)
def no_model():
    """Templates only: the machine's own writing model must not answer in tests."""
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
    public.set_index(local._public_link_index)


def build_course(db, *, price=0, quiz=True, **extra):
    course = service.create_course(
        db, {"title": "Python Basics", "price_cents": price, **extra}
    )
    module = service.add_module(db, course["id"], "Start")
    first = service.add_lesson(
        db,
        module["id"],
        {"title": "Hello", "body": "Print things.", "preview": True, "minutes": 5},
    )
    second = service.add_lesson(
        db, module["id"], {"title": "Maths", "body": "Add numbers.", "minutes": 10}
    )
    if quiz:
        service.set_quiz(db, second["id"], QUIZ, pass_mark=100)
    service.set_status(db, course["id"], "published")
    return course, first, second


def outbox_kinds(db):
    return [message["kind"] for message in kit.outbox(db, app="academy")]


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_publishing_needs_a_lesson_and_the_curriculum_keeps_its_order(db):
    course = service.create_course(db, {"title": "Empty", "price_cents": 0})
    with pytest.raises(ValueError, match="at least one lesson"):
        service.set_status(db, course["id"], "published")
    a = service.add_module(db, course["id"], "A")
    b = service.add_module(db, course["id"], "B")
    one = service.add_lesson(db, a["id"], {"title": "One"})
    two = service.add_lesson(db, a["id"], {"title": "Two"})
    modules = service.reorder(
        db,
        course["id"],
        [b["id"], a["id"]],
        {b["id"]: [two["id"]], a["id"]: [one["id"]]},
    )
    assert [m["title"] for m in modules] == ["B", "A"]
    assert [lesson["title"] for lesson in modules[0]["lessons"]] == ["Two"]
    with pytest.raises(ValueError):
        service.reorder(db, course["id"], [a["id"]], None)
    assert service.set_status(db, course["id"], "published")["status"] == "published"
    with pytest.raises(ValueError):
        service.create_course(db, {"title": "Bad", "cover_url": "ftp://x"})


def test_enrolment_free_paid_codes_capacity_and_cohorts(db):
    free, _, _ = build_course(db)
    result = service.enrol(db, free["id"], {"name": "Ana", "email": "ana@example.com"})
    assert result["enrolment"]["status"] == "active" and result["link"][
        "path"
    ].startswith("/p/")
    # The same email again: no second enrolment, the link is emailed again.
    with pytest.raises(service.AlreadyEnrolled):
        service.enrol(db, free["id"], {"name": "Ana", "email": "ANA@example.com"})
    assert outbox_kinds(db).count("enrolment") == 2

    paid, _, _ = build_course(db, price=4900, enrol_code="SPRING", capacity=2)
    with pytest.raises(ValueError, match="enrolment code"):
        service.enrol(
            db, paid["id"], {"name": "Ben", "email": "ben@example.com", "code": "nope"}
        )
    pending = service.enrol(
        db, paid["id"], {"name": "Ben", "email": "ben@example.com", "code": "spring"}
    )["enrolment"]
    assert pending["status"] == "pending_payment" and pending["reference"].startswith(
        "AC-"
    )
    service.enrol(
        db, paid["id"], {"name": "Cy", "email": "cy@example.com", "code": "SPRING"}
    )
    with pytest.raises(ValueError, match="full"):
        service.enrol(
            db, paid["id"], {"name": "Di", "email": "di@example.com", "code": "SPRING"}
        )

    cohort = service.create_cohort(
        db, {"course_id": free["id"], "name": "October", "capacity": 1}
    )
    service.enrol(
        db,
        free["id"],
        {"name": "Ed", "email": "ed@example.com", "cohort_id": cohort["id"]},
    )
    with pytest.raises(ValueError, match="October"):
        service.enrol(
            db,
            free["id"],
            {"name": "Fi", "email": "fi@example.com", "cohort_id": cohort["id"]},
        )
    draft = service.create_course(db, {"title": "Draft"})
    with pytest.raises(ValueError, match="not open"):
        service.enrol(db, draft["id"], {"name": "Gu", "email": "gu@example.com"})


def test_lessons_unlock_on_payment_and_quizzes_are_graded_on_the_server(db):
    course, first, second = build_course(db, price=2500)
    enrolment = service.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com"}
    )["enrolment"]
    view = service.student_view(db, enrolment["id"])
    lessons = view["modules"][0]["lessons"]
    assert lessons[0]["locked"] is False and "body" in lessons[0]  # the free preview
    assert (
        lessons[1]["locked"] is True
        and "body" not in lessons[1]
        and "questions" not in lessons[1]
    )
    assert view["amount_due_cents"] == 2500
    with pytest.raises(PermissionError):
        service.complete_lesson(db, enrolment["id"], first["id"])

    service.mark_paid(db, enrolment["id"])
    view = service.student_view(db, enrolment["id"])
    questions = view["modules"][0]["lessons"][1]["questions"]
    assert questions[0] == {
        "prompt": "2 + 2?",
        "options": ["3", "4", "5"],
    }  # never the right option
    assert "correct" not in json.dumps(view)

    state = service.complete_lesson(db, enrolment["id"], first["id"])
    assert state["progress"]["done"] == 1 and state["status"] == "active"
    wrong = service.submit_quiz(db, enrolment["id"], second["id"], [1, 1])
    assert wrong["passed"] is False and wrong["score"] == 50
    assert wrong["results"] == [
        {"correct": True},
        {"correct": False},
    ]  # no answers or explanations yet
    with pytest.raises(ValueError, match="Answer all"):
        service.submit_quiz(db, enrolment["id"], second["id"], [1])
    with pytest.raises(ValueError):
        service.submit_quiz(db, enrolment["id"], second["id"], [9, 0])
    right = service.submit_quiz(db, enrolment["id"], second["id"], [1, 0])
    assert right["passed"] is True and right["results"][1]["answer"] == 0
    assert right["status"] == "completed" and right["certificate"]["path"].startswith(
        "/p/"
    )

    certificate = service.certificate(db, enrolment["id"])
    assert certificate["name"] == "Ana" and certificate["code"].startswith("CERT-")
    assert (
        service.verify_code(db, certificate["code"].lower())["course"]
        == "Python Basics"
    )
    assert service.verify_code(db, "CERT-NOPE") is None
    assert {"enrolment", "payment_receipt", "course_completed"} <= set(outbox_kinds(db))


def test_completion_needs_every_lesson_and_every_quiz(db):
    course, first, second = build_course(db)
    enrolment = service.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com"}
    )["enrolment"]
    service.complete_lesson(db, enrolment["id"], first["id"])
    state = service.complete_lesson(db, enrolment["id"], second["id"])
    assert (
        state["progress"]["done"] == 2 and state["progress"]["complete"] is False
    )  # the quiz is not passed
    with pytest.raises(LookupError):
        service.certificate(db, enrolment["id"])
    other, _, _ = build_course(db)
    with pytest.raises(LookupError):
        service.complete_lesson(
            db,
            enrolment["id"],
            service.curriculum(db, other["id"])[0]["lessons"][0]["id"],
        )


def test_payment_is_recorded_once_and_a_refund_voids_it(db):
    course, _, _ = build_course(db, price=4900)
    enrolment = service.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com"}
    )["enrolment"]
    service.mark_paid(db, enrolment["id"])
    service.mark_paid(db, enrolment["id"])
    assert kit.revenue(db, app="academy")["total_cents"] == 4900
    assert outbox_kinds(db).count("payment_receipt") == 1
    assert service.refund(db, enrolment["id"])["status"] == "refunded"
    assert kit.revenue(db, app="academy")["total_cents"] == 0
    with pytest.raises(ValueError):
        service.mark_paid(db, enrolment["id"])
    with pytest.raises(PermissionError):
        service.complete_lesson(
            db,
            enrolment["id"],
            service.curriculum(db, course["id"])[0]["lessons"][0]["id"],
        )
    free, _, _ = build_course(db)
    free_enrolment = service.enrol(
        db, free["id"], {"name": "Bo", "email": "bo@example.com"}
    )["enrolment"]
    with pytest.raises(ValueError, match="free"):
        service.mark_paid(db, free_enrolment["id"])


def test_inactive_students_are_nudged_once_a_week(db):
    course, _, _ = build_course(db)
    enrolment = service.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com"}
    )["enrolment"]
    assert service.nudge_inactive(db)["queued"] == 0
    old = kit.iso(kit.utcnow() - timedelta(days=10))
    db.execute(
        "UPDATE ac_enrolments SET last_activity_at = ? WHERE id = ?",
        (old, enrolment["id"]),
    )
    assert service.nudge_inactive(db)["queued"] == 1
    assert service.nudge_inactive(db)["queued"] == 0
    assert (
        service.nudge_inactive(db, now=kit.utcnow() + timedelta(days=8))["queued"] == 1
    )
    assert outbox_kinds(db).count("nudge") == 2


def test_drafts_fall_back_to_templates_and_quizzes_are_validated(db):
    outline = service.outline_draft("Watercolour painting", "beginner")
    assert outline["source"] == "template" and len(outline["modules"]) >= 3
    course = service.create_course(db, {"title": "Watercolour"})
    modules = service.apply_outline(db, course["id"], outline["modules"])
    lesson = modules[0]["lessons"][0]
    assert lesson["body"]
    service.update_lesson(
        db,
        lesson["id"],
        {
            "body": "Water dilutes the pigment on paper. Lighter washes need more water than pigment."
        },
    )
    quiz = service.quiz_draft(db, lesson["id"])
    assert quiz["source"] == "template" and quiz["questions"][0]["options"] == [
        "True",
        "False",
    ]
    service.set_quiz(db, lesson["id"], quiz["questions"])
    with pytest.raises(ValueError, match="2 to 6"):
        service.set_quiz(
            db, lesson["id"], [{"prompt": "x", "options": ["only"], "correct": 0}]
        )
    with pytest.raises(ValueError):
        service.set_quiz(
            db, lesson["id"], [{"prompt": "x", "options": ["a", "b"], "correct": 3}]
        )
    with pytest.raises(ValueError):
        service.apply_outline(db, course["id"], [])


def test_the_course_page_shows_previews_only_and_announcements_reach_students(db):
    course, _, _ = build_course(db, price=1000)
    page = service.course_page(db, course["id"])
    lessons = page["modules"][0]["lessons"]
    assert (
        "body" in lessons[0]
        and "body" not in lessons[1]
        and lessons[1]["has_quiz"] is True
    )
    assert page["lessons"] == 2 and page["minutes"] == 15
    enrolment = service.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com"}
    )["enrolment"]
    assert (
        service.announce(
            db,
            {"course_id": course["id"], "subject": "Hi", "body": "Live class Friday"},
        )["recipients"]
        == 0
    )
    service.mark_paid(db, enrolment["id"])
    assert (
        service.announce(
            db,
            {"course_id": course["id"], "subject": "Hi", "body": "Live class Friday"},
        )["recipients"]
        == 1
    )
    assert "announcement" in outbox_kinds(db)
    with pytest.raises(ValueError):
        service.delete_course(db, course["id"])
    service.set_status(db, course["id"], "archived")
    with pytest.raises(LookupError):
        service.course_page(db, course["id"])


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def anonymous(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw.startswith(b"{") else raw
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw.startswith(b"{") else raw


def test_a_course_from_first_lesson_to_certificate_in_the_local_build(
    env, api, local_index
):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Code School", "payment_link": "https://pay.example.com/codeschool"},
    )
    course = api.post(
        "/api/academy/courses",
        {"title": "Python Basics", "price_cents": 4900},
        expect=201,
    )["course"]
    module = api.post(
        f"/api/academy/courses/{course['id']}/modules", {"title": "Start"}, expect=201
    )["module"]
    first = api.post(
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "Hello", "body": "Print.", "preview": True},
        expect=201,
    )["lesson"]
    second = api.post(
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "Maths", "body": "Add."},
        expect=201,
    )["lesson"]
    api.post(
        f"/api/academy/lessons/{second['id']}/quiz",
        {"questions": QUIZ, "pass_mark": 50},
    )
    status, body = api.call("POST", f"/api/academy/courses/{course['id']}/link", {})
    assert status == 400  # not published yet
    api.post(f"/api/academy/courses/{course['id']}/status", {"status": "published"})
    link = api.post(f"/api/academy/courses/{course['id']}/link", {})["link"]
    detail = api.get(f"/api/academy/courses/{course['id']}")
    assert detail["link"]["token"] == link["token"]
    assert (
        detail["modules"][0]["lessons"][1]["questions"][0]["correct"] == 1
    )  # the owner sees the answers

    status, page = anonymous(env.base, "GET", f"/api/public/{link['token']}")
    assert (
        status == 200
        and page["kind"] == "course"
        and page["business"]["name"] == "Code School"
    )
    assert page["page"]["course"][
        "price_cents"
    ] == 4900 and "correct" not in json.dumps(page)
    status, enrolled = anonymous(
        env.base,
        "POST",
        f"/api/public/{link['token']}/enrol",
        {"name": "Ana", "email": "ana@example.com"},
    )
    assert status == 201 and enrolled["enrolment"]["status"] == "pending_payment"
    student_token = enrolled["student_path"].removeprefix("/p/")
    status, again = anonymous(
        env.base,
        "POST",
        f"/api/public/{link['token']}/enrol",
        {"name": "Ana", "email": "ana@example.com"},
    )
    assert status == 200 and again["already"] is True and "student_path" not in again

    status, student = anonymous(env.base, "GET", f"/api/public/{student_token}")
    assert (
        status == 200 and student["page"]["modules"][0]["lessons"][1]["locked"] is True
    )
    status, refused = anonymous(
        env.base,
        "POST",
        f"/api/public/{student_token}/complete",
        {"lesson_id": first["id"]},
    )
    assert status == 403

    students = api.get("/api/academy/students")["students"]
    assert (
        students[0]["email"] == "ana@example.com"
        and students[0]["status"] == "pending_payment"
    )
    api.post(f"/api/academy/students/{students[0]['id']}/paid", {})
    status, done = anonymous(
        env.base,
        "POST",
        f"/api/public/{student_token}/complete",
        {"lesson_id": first["id"]},
    )
    assert status == 200 and done["progress"]["done"] == 1
    status, graded = anonymous(
        env.base,
        "POST",
        f"/api/public/{student_token}/quiz",
        {"lesson_id": second["id"], "answers": [1, 0]},
    )
    assert (
        status == 200 and graded["passed"] is True and graded["status"] == "completed"
    )
    certificate_token = graded["certificate"]["token"]
    status, cert = anonymous(env.base, "GET", f"/api/public/{certificate_token}")
    assert (
        status == 200
        and cert["page"]["valid"] is True
        and cert["page"]["name"] == "Ana"
    )
    assert (
        api.get(f"/api/academy/certificates?code={cert['page']['code']}")[
            "certificate"
        ]["name"]
        == "Ana"
    )

    home = api.get("/api/suite/home")
    assert home["revenue"]["by_app"] == {"academy": 4900}
    # Academy is a section of the Courses & Events app: the hub lists it inside that app
    academy = next(
        s for app in home["apps"] for s in app["sections"] if s["id"] == "academy"
    )
    assert academy["progress"]["done"] == academy["progress"]["total"] == 6
    overview = api.get("/api/academy/overview")
    assert overview["students"] == 1 and overview["completion_rate"] == 100
    status, csv_body, _ = test_careers_raw(api, "/api/academy/students?format=csv")
    assert status == 200 and b"ana@example.com" in csv_body
    outbox = api.get("/api/suite/outbox?app=academy")["messages"]
    assert {"enrolment", "payment_receipt", "course_completed"} <= {
        m["kind"] for m in outbox
    }
    assert any(env.base + "/p/" in m["body"] for m in outbox)


def test_careers_raw(api, path):
    """The raw bytes of a download through the signed-in client."""
    request = urllib.request.Request(api.base + path)
    request.add_header("X-Requested-With", "aira")
    with api.opener.open(request, timeout=30) as response:
        return response.status, response.read(), response.headers


test_careers_raw.__test__ = False  # a helper, not a test


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_academy_needs_the_app_and_respects_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/academy/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "academy")
    assert owner.call("GET", "/api/academy/overview")[0] == 200
    status, created = owner.call(
        "POST", "/api/academy/courses", {"title": "Leadership 101", "price_cents": 0}
    )
    assert status == 201
    course_id = created["course"]["id"]
    for who in ("admin", "coach"):
        assert (
            sign_in(cloud, getattr(org, who)).call(
                "POST", f"/api/academy/courses/{course_id}/modules", {"title": who}
            )[0]
            == 201
        )
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/academy/courses")[0] == 200
    assert auditor.call("POST", "/api/academy/courses", {"title": "X"})[0] == 403
    assert sign_in(cloud, org.candidate).call("GET", "/api/academy/overview")[0] == 403

    module_id = owner.call("GET", f"/api/academy/courses/{course_id}")[1]["modules"][0][
        "id"
    ]
    assert (
        owner.call(
            "POST", f"/api/academy/modules/{module_id}/lessons", {"title": "Listening"}
        )[0]
        == 201
    )
    assert (
        owner.call(
            "POST", f"/api/academy/courses/{course_id}/status", {"status": "published"}
        )[0]
        == 200
    )
    link = owner.call("POST", f"/api/academy/courses/{course_id}/link", {})[1]["link"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["course"]["title"] == "Leadership 101"
    status, enrolled = visitor.call(
        "POST",
        f"/api/public/{link['token']}/enrol",
        {"name": "Ana", "email": "ana@example.com"},
    )
    assert status == 201 and enrolled["enrolment"]["status"] == "active"
    status, student = visitor.call(
        "GET", f"/api/public/{enrolled['student_path'].removeprefix('/p/')}"
    )
    assert status == 200 and student["page"]["student"]["name"] == "Ana"
    assert (
        owner.call("GET", "/api/academy/students")[1]["students"][0]["email"]
        == "ana@example.com"
    )

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "academy")
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/academy/courses")
    assert status == 200 and theirs["courses"] == []


# ---------------------------------------------------------------------------
# in a real browser
# ---------------------------------------------------------------------------


@pytest.fixture
def browser():
    patchright = pytest.importorskip("patchright.sync_api")
    with patchright.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        yield launched
        launched.close()


def test_the_academy_screen_and_a_student_journey_in_a_browser(
    browser, env, local_index
):
    from test_leads_ui import signed_in_page

    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env)
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }

    def post(path, body):
        response = context.request.post(
            env.base + path, data=json.dumps(body), headers=headers
        )
        assert response.status in (200, 201), response.text()
        return response.json()

    post("/api/suite/business", {"name": "Code School"})
    course = post("/api/academy/courses", {"title": "Python Basics", "price_cents": 0})[
        "course"
    ]
    module = post(f"/api/academy/courses/{course['id']}/modules", {"title": "Start"})[
        "module"
    ]
    post(
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "Hello", "body": "Print things.\n\nThen more.", "preview": True},
    )
    second = post(
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "Maths", "body": "Add numbers."},
    )["lesson"]
    post(
        f"/api/academy/lessons/{second['id']}/quiz",
        {"questions": QUIZ, "pass_mark": 100},
    )
    post(f"/api/academy/courses/{course['id']}/status", {"status": "published"})
    link = post(f"/api/academy/courses/{course['id']}/link", {})["link"]

    page.goto(f"{env.base}/#/academy")
    page.wait_for_selector(".suite-kpis")
    assert page.locator(".page-head h1").inner_text() == "Academy"
    page.goto(f"{env.base}/#/academy?tab=courses&course={course['id']}")
    page.wait_for_selector(".ac-module")
    assert "Module 1: Start" in page.locator(".ac-module").inner_text()
    assert page.locator(".suite-share-url").inner_text().endswith(link["path"])
    page.goto(f"{env.base}/#/academy?tab=students")
    page.wait_for_selector(".empty")

    visitor = browser.new_context().new_page()
    visitor.set_default_timeout(60_000)
    visitor_problems: list[str] = []
    visitor.on(
        "console",
        lambda m: visitor_problems.append(m.text) if m.type == "error" else None,
    )
    visitor.on("pageerror", lambda e: visitor_problems.append(str(e)))
    visitor.goto(env.base + link["path"])
    visitor.wait_for_selector("h1:has-text('Python Basics')")
    visitor.get_by_role("button", name="Free preview").click()
    assert visitor.get_by_text("Then more.").is_visible()
    visitor.get_by_label("Your name").fill("Ana")
    visitor.get_by_label("Email").fill("ana@example.com")
    visitor.get_by_role("button", name="Enrol now").click()
    visitor.get_by_role("link", name="Start learning").click()
    visitor.wait_for_selector("text=Welcome, Ana.")
    visitor.get_by_role("button", name="Open lesson").first.click()
    visitor.get_by_role("button", name="Mark as complete").click()
    visitor.wait_for_selector("text=1 of 2 lessons done")
    visitor.locator(".pub-item", has_text="Maths").get_by_role(
        "button", name="Open lesson"
    ).click()
    visitor.get_by_label("4").check()
    visitor.get_by_label("Paris").check()
    visitor.get_by_role("button", name="Submit answers").click()
    visitor.wait_for_selector("text=Passed!")
    visitor.get_by_role("link", name="View your certificate").click()
    visitor.wait_for_selector("text=Verified certificate")
    assert "Ana" in visitor.locator("h1").inner_text()
    assert visitor_problems == [] and problems == []
