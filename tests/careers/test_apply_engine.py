"""The form engine against the stand-in ATS: every adapter and every outcome, in headless Chromium."""

from __future__ import annotations

import pytest

from aira.apply_engine import ApplyRequest, detect_ats, extract_confirmation_number, run
from aira.pdf_text import text_to_pdf
from ats_stub import (
    ATS_NAMES,
    REFERENCE_PREFIX,
    SENSITIVE_FIELD,
    UNKNOWN_FIELD,
    UNKNOWN_QUESTION,
    AtsStub,
)

PROFILE = {
    "first_name": "Hyma",
    "last_name": "Test",
    "full_name": "Hyma Test",
    "email": "hyma@example.com",
    "phone": "+1 512 555 0100",
    "city": "Austin",
    "state": "TX",
    "country": "United States",
    "linkedin_url": "https://www.linkedin.com/in/hyma-test",
    "years_of_experience": "9",
    "location_preference": "Remote",
}
CONFIRMED = {
    "work_authorization": {"value": "Yes", "confirmed": True},
    "sponsorship_required": {"value": "No", "confirmed": True},
    "legal_attestation": {"value": "Yes", "confirmed": True},
    "how_did_you_hear": {"value": "LinkedIn", "confirmed": True},
}


@pytest.fixture(scope="module")
def stub():
    server = AtsStub().start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("patchright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        yield launched
        launched.close()


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "Resume.pdf"
    path.write_bytes(text_to_pdf("Hyma Test\nCloud architect", "Resume"))
    return str(path)


def request(
    stub,
    resume,
    ats,
    *,
    variant="",
    submit=True,
    answers=None,
    extra=None,
    timeout=40,
    **kwargs,
):
    return ApplyRequest(
        job_id=f"{ats}:1",
        source=ats,
        application_url=stub.form_url(ats, variant) if variant else stub.form_url(ats),
        fields=[],
        profile=kwargs.pop("profile", PROFILE),
        standard_answers=CONFIRMED if answers is None else answers,
        resume_path=resume,
        cover_letter_path=None,
        cover_letter_text="Dear hiring team, I would like to apply.",
        submit=submit,
        timeout_seconds=timeout,
        extra_answers=extra or {},
        **kwargs,
    )


@pytest.mark.parametrize("ats", ATS_NAMES)
def test_submits_every_form_and_captures_the_confirmation(
    stub, browser, resume, ats, tmp_path
):
    stub.reset()
    outcome = run(
        request(stub, resume, ats, screenshot_dir=str(tmp_path)), browser=browser
    )
    assert outcome.status == "submitted", (
        outcome.error,
        [q.label for q in outcome.unanswered],
    )
    assert outcome.ats == ats
    assert outcome.evidence.confirmation_number.startswith(REFERENCE_PREFIX[ats])
    assert outcome.evidence.screenshot_path and outcome.evidence.submitted_at
    [submission] = stub.submissions
    assert submission.files, "the resume was uploaded"
    flat = " ".join(" ".join(values) for values in submission.fields.values())
    assert "hyma@example.com" in flat and "Hyma" in flat


def test_unknown_required_question_holds_and_extra_answer_resumes(
    stub, browser, resume
):
    stub.reset()
    held = run(
        request(stub, resume, "greenhouse", variant="unknown_required"), browser=browser
    )
    assert held.status == "needs_review" and not stub.submissions
    assert [q.label for q in held.unanswered] == [UNKNOWN_QUESTION]
    answered = run(
        request(
            stub,
            resume,
            "greenhouse",
            variant="unknown_required",
            extra={UNKNOWN_QUESTION: "The API."},
        ),
        browser=browser,
    )
    assert answered.status == "submitted"
    assert stub.submissions[-1].fields[UNKNOWN_FIELD["greenhouse"]] == ["The API."]


def test_sensitive_question_needs_a_confirmed_answer(stub, browser, resume):
    stub.reset()
    unconfirmed = {
        **CONFIRMED,
        "sponsorship_required": {"value": "No", "confirmed": False},
    }
    held = run(
        request(stub, resume, "lever", variant="sensitive", answers=unconfirmed),
        browser=browser,
    )
    assert held.status == "needs_review" and not stub.submissions
    assert any(q.status == "needs_confirmation" for q in held.unanswered)
    sent = run(request(stub, resume, "lever", variant="sensitive"), browser=browser)
    assert sent.status == "submitted"
    assert stub.submissions[-1].fields[SENSITIVE_FIELD["lever"]] == ["No"]


@pytest.mark.parametrize(
    ("variant", "status", "message"),
    [
        ("applied", "already_applied", "already exists"),
        ("captcha", "needs_review", "CAPTCHA"),
        ("fail_submit", "failed", "HTTP 500"),
    ],
)
def test_outcomes_that_never_claim_success(
    stub, browser, resume, variant, status, message
):
    stub.reset()
    outcome = run(request(stub, resume, "generic", variant=variant), browser=browser)
    assert outcome.status == status and message in outcome.error


def test_no_success_marker_is_a_failure(stub, browser, resume):
    stub.reset()
    outcome = run(
        request(stub, resume, "ashby", variant="no_marker", timeout=8), browser=browser
    )
    assert outcome.status == "failed" and "No confirmation" in outcome.error
    assert outcome.evidence.page_text


def test_fill_only_stops_before_submit(stub, browser, resume):
    stub.reset()
    outcome = run(
        request(stub, resume, "smartrecruiters", submit=False), browser=browser
    )
    assert outcome.status == "filled" and not stub.submissions
    assert sum(1 for q in outcome.questions if q.status == "filled") >= 5


def test_missing_profile_values_are_never_invented(stub, browser, resume):
    stub.reset()
    sparse = {
        key: PROFILE[key] for key in ("first_name", "last_name", "full_name", "email")
    }
    outcome = run(request(stub, resume, "greenhouse", profile=sparse), browser=browser)
    assert outcome.status == "needs_review" and not stub.submissions
    assert any("phone" in q.label.lower() for q in outcome.unanswered)


def test_static_routing():
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert detect_ats("https://jobs.lever.co/acme/x") == "lever"
    assert detect_ats("https://acme.wd5.myworkdayjobs.com/x") == "workday"
    assert detect_ats("https://www.linkedin.com/jobs/view/1") == "linkedin"
    assert detect_ats("https://careers.example.com/apply") == "generic"
    workday = ApplyRequest(
        "w:1",
        "",
        "https://acme.wd5.myworkdayjobs.com/job/1",
        [],
        {},
        {},
        None,
        None,
        "",
        True,
    )
    # Workday has its own runner; without a saved account it holds before any browser starts.
    held = run(workday)
    assert held.status == "needs_review" and "Workday accounts" in held.error
    linkedin = ApplyRequest(
        "l:1",
        "",
        "https://www.linkedin.com/jobs/view/1",
        [],
        {},
        {},
        None,
        None,
        "",
        True,
    )
    assert run(linkedin).status == "unsupported"
    empty = ApplyRequest("e:1", "", "", [], {}, {}, None, None, "", True)
    assert run(empty).status == "failed"
    assert (
        extract_confirmation_number("Your reference number is GH-10001.") == "GH-10001"
    )


@pytest.mark.parametrize("path, code", [("/blocked", 403), ("/rate-limited", 429)])
def test_a_site_that_refuses_automated_visits_is_reported_as_such(
    stub, browser, resume, path, code
):
    blocked = ApplyRequest(
        job_id="blocked:1",
        source="google_jobs",
        application_url=f"{stub.base_url}{path}",
        fields=[],
        profile=PROFILE,
        standard_answers=CONFIRMED,
        resume_path=resume,
        cover_letter_path=None,
        cover_letter_text="",
        submit=True,
        timeout_seconds=30,
        extra_answers={},
    )
    outcome = run(blocked, browser=browser)
    assert outcome.status == "unsupported", (
        "not a broken page: the site says no to programs"
    )
    assert f"HTTP {code}" in outcome.error and "apply yourself" in outcome.error
    assert any(f"HTTP {code}" in step for step in outcome.steps)


def test_a_missing_page_is_still_a_failure(stub, browser, resume):
    missing = ApplyRequest(
        job_id="missing:1",
        source="google_jobs",
        application_url=f"{stub.base_url}/no-such-page",
        fields=[],
        profile=PROFILE,
        standard_answers=CONFIRMED,
        resume_path=resume,
        cover_letter_path=None,
        cover_letter_text="",
        submit=True,
        timeout_seconds=30,
        extra_answers={},
    )
    outcome = run(missing, browser=browser)
    assert outcome.status == "failed" and "HTTP 404" in outcome.error
