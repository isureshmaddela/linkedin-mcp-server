"""Workday: the multi-step runner against a stand-in tenant, and the encrypted account store.

The runner tests drive :func:`aira.apply_engine.run` (which hands Workday to
:mod:`aira.workday`) in headless Chromium against ``tests/workday_stub.py``; nothing
talks to a real Workday site.
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from aira import workday_accounts
from aira.airaapi.store import SecretStore
from aira.apply_engine import ApplyRequest, run
from aira.pdf_text import text_to_pdf
from aira.workday import WorkdayAccount, _choose, _Option, _step_kind
from workday_stub import DECLINE, DISABILITY_OPTIONS, EXTRA_QUESTION, WorkdayStub

EMAIL = "hyma@example.com"
PASSWORD = "Tr1cky-Workday-Pass!"
PROFILE = {
    "first_name": "Hyma",
    "last_name": "Test",
    "full_name": "Hyma Test",
    "email": EMAIL,
    "phone": "+1 512 555 0100",
    "city": "Austin",
    "state": "TX",
    "country": "United States",
    "linkedin_url": "https://www.linkedin.com/in/hyma-test",
}
CONFIRMED = {
    "work_authorization": {"value": "Yes", "confirmed": True},
    "sponsorship_required": {"value": "No", "confirmed": True},
    "legal_attestation": {"value": "Yes", "confirmed": True},
    "how_did_you_hear": {"value": "LinkedIn", "confirmed": True},
    # The person's own saved choices for the disclosure pages.
    "gender": {"value": "Decline to answer", "confirmed": True},
    "race_ethnicity": {"value": "Decline to answer", "confirmed": True},
    "veteran_status": {"value": "Decline to answer", "confirmed": True},
    "disability_status": {"value": "I do not want to answer", "confirmed": True},
}
ANSWERS = {"Phone Device Type": "Mobile"}


@pytest.fixture(scope="module")
def site():
    stub = WorkdayStub().start()
    yield stub
    stub.stop()


@pytest.fixture
def stub(site):
    site.reset()
    return site


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


class Recorder:
    def __init__(self) -> None:
        self.states: list[str] = []

    def __call__(self, state: str, message: str) -> None:
        self.states.append(state)


def account(stub, *, password=PASSWORD, create=False, recorder=None):
    return WorkdayAccount(
        host="127.0.0.1",
        email=EMAIL,
        password=password,
        create=create,
        on_result=recorder,
    )


def request(
    stub,
    resume,
    *,
    workday_account,
    submit=False,
    answers=None,
    extra=None,
    profile=None,
    tmp=None,
):
    return ApplyRequest(
        job_id="workday:1",
        source="workday",
        application_url=stub.job_url,
        fields=[],
        profile=PROFILE if profile is None else profile,
        standard_answers=CONFIRMED if answers is None else answers,
        resume_path=resume,
        cover_letter_path=None,
        cover_letter_text="",
        submit=submit,
        screenshot_dir=str(tmp) if tmp else None,
        timeout_seconds=90,
        extra_answers=ANSWERS if extra is None else extra,
        workday_account=workday_account,
    )


def _no_secret(outcome, secret=PASSWORD):
    text = " ".join([outcome.error, *outcome.steps, outcome.evidence.page_text])
    assert secret not in text


# --------------------------------------------------------------------------- #
# The flow
# --------------------------------------------------------------------------- #


def test_sign_in_walks_every_step_and_fill_only_stops_at_review(
    stub, browser, resume, tmp_path
):
    stub.add_account(EMAIL, PASSWORD)
    recorder = Recorder()
    outcome = run(
        request(
            stub, resume, workday_account=account(stub, recorder=recorder), tmp=tmp_path
        ),
        browser=browser,
    )
    assert outcome.status == "filled", (outcome.error, outcome.steps)
    assert outcome.ats == "workday"
    assert (
        outcome.evidence.screenshot_path
        and Path(outcome.evidence.screenshot_path).is_file()
    )
    assert stub.posted_steps() == [1, 2, 3, 4, 5], "every step through Review was saved"
    assert not stub.submissions, "fill-only never submits"
    assert recorder.states == ["active"]
    assert any(step.startswith("Reached Review") for step in outcome.steps)
    pages = stub.applications[EMAIL].pages
    assert pages[1]["legalNameSection_firstName"] == ["Hyma"]
    assert pages[1]["sourceDropdown"] == ["LinkedIn"]
    assert pages[1]["countryDropdown"] == ["United States"]
    assert pages[1]["phone-device-type"] == ["Mobile"]
    assert pages[2]["jobTitle"] == ["Cloud Architect"], "the parsed value is kept"
    assert stub.applications[EMAIL].files["resume"].head.startswith(b"%PDF")
    assert pages[3]["workAuthorization"] == ["Yes"] and pages[3]["sponsorship"] == [
        "No"
    ]
    # The person's saved "decline" choice, matched to each page's own wording.
    assert pages[4]["gender"] == [DECLINE] and pages[4]["ethnicityDropdown"] == [
        DECLINE
    ]
    assert pages[4]["veteranStatus"] == ["I do not wish to self-identify"]
    assert pages[5]["name"] == ["Hyma Test"]
    assert pages[5]["disabilityStatus"] == [DISABILITY_OPTIONS[2]]
    _no_secret(outcome)


def test_create_account_then_submit_reaches_the_confirmation(
    stub, browser, resume, tmp_path
):
    recorder = Recorder()
    generated = workday_accounts.generate_password()
    outcome = run(
        request(
            stub,
            resume,
            workday_account=account(
                stub, password=generated, create=True, recorder=recorder
            ),
            submit=True,
            tmp=tmp_path,
        ),
        browser=browser,
    )
    assert outcome.status == "submitted", (outcome.error, outcome.steps)
    assert stub.accounts[EMAIL]["password"] == generated, (
        "created with the saved password"
    )
    assert recorder.states == ["active"]
    assert len(stub.submissions) == 1
    assert outcome.evidence.confirmation_number.startswith("WD-")
    assert outcome.evidence.submitted_at and outcome.evidence.screenshot_path
    _no_secret(outcome, generated)


def test_an_unanswered_question_holds_and_an_answer_resumes(stub, browser, resume):
    stub.add_account(EMAIL, PASSWORD)
    held = run(
        request(stub, resume, workday_account=account(stub), extra={}), browser=browser
    )
    assert held.status == "needs_review"
    assert [q.label for q in held.unanswered] == ["Phone Device Type"]
    assert held.unanswered[0].options == ["Mobile", "Home", "Work"], (
        "the choices are shown"
    )
    assert stub.posted_steps() == [], "the page is not saved with a question left open"

    stub.extra_question = True
    held = run(request(stub, resume, workday_account=account(stub)), browser=browser)
    assert held.status == "needs_review"
    assert [q.label for q in held.unanswered] == [EXTRA_QUESTION]
    assert "Application Questions" in held.error

    done = run(
        request(
            stub,
            resume,
            workday_account=account(stub),
            extra={**ANSWERS, EXTRA_QUESTION: "The API."},
        ),
        browser=browser,
    )
    assert done.status == "filled"
    assert stub.applications[EMAIL].pages[3]["products"] == ["The API."]


def test_disclosures_and_self_identify_are_never_answered_for_the_person(
    stub, browser, resume
):
    stub.add_account(EMAIL, PASSWORD)
    no_choices = {
        key: value
        for key, value in CONFIRMED.items()
        if key
        not in ("gender", "race_ethnicity", "veteran_status", "disability_status")
    }
    held = run(
        request(
            stub,
            resume,
            workday_account=account(stub),
            answers=no_choices,
            # Profile data is never a disclosure answer.
            profile={**PROFILE, "gender": "Female", "disability_status": "No"},
        ),
        browser=browser,
    )
    assert held.status == "needs_review"
    assert "Voluntary Disclosures waits for you" in held.error
    labels = {q.label for q in held.unanswered}
    assert {"Gender", "Ethnicity", "Veteran Status"} <= labels
    assert stub.posted_steps() == [1, 2, 3], "Voluntary Disclosures was not saved"

    # Saved demographic choices, but the disability answer is not confirmed: Self Identify waits.
    unconfirmed = {
        **CONFIRMED,
        "disability_status": {"value": "No", "confirmed": False},
    }
    stub.reset()
    stub.add_account(EMAIL, PASSWORD)
    held = run(
        request(stub, resume, workday_account=account(stub), answers=unconfirmed),
        browser=browser,
    )
    assert held.status == "needs_review"
    assert "Self Identify waits for you" in held.error
    assert [q.label for q in held.unanswered] == ["Please check one of the boxes below"]
    assert held.unanswered[0].status == "needs_confirmation"
    assert stub.posted_steps() == [1, 2, 3, 4]


def test_a_verification_email_holds_then_sign_in_continues(stub, browser, resume):
    stub.verify_on_create = True
    recorder = Recorder()
    held = run(
        request(
            stub, resume, workday_account=account(stub, create=True, recorder=recorder)
        ),
        browser=browser,
    )
    assert held.status == "needs_review" and "verification email" in held.error
    assert recorder.states == ["verification_needed"]
    assert stub.posted_steps() == []
    stub.accounts[EMAIL]["verified"] = True  # the person clicked the link in the email
    done = run(
        request(stub, resume, workday_account=account(stub, recorder=recorder)),
        browser=browser,
    )
    assert done.status == "filled", done.error
    assert recorder.states == ["verification_needed", "active"]


def test_create_when_the_account_exists_signs_in(stub, browser, resume):
    stub.add_account(EMAIL, PASSWORD)
    recorder = Recorder()
    outcome = run(
        request(
            stub, resume, workday_account=account(stub, create=True, recorder=recorder)
        ),
        browser=browser,
    )
    assert outcome.status == "filled", outcome.error
    assert recorder.states == ["exists", "active"]


def test_a_rejected_password_holds_and_is_never_logged(stub, browser, resume, caplog):
    stub.add_account(EMAIL, "the-real-password-1A!")
    recorder = Recorder()
    with caplog.at_level(logging.DEBUG):
        held = run(
            request(stub, resume, workday_account=account(stub, recorder=recorder)),
            browser=browser,
        )
    assert held.status == "needs_review"
    assert "did not accept the email and password" in held.error
    assert recorder.states == ["sign_in_failed"]
    _no_secret(held)
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr(account(stub))
    # The password went to the sign-in form, and only there.
    assert [post["password"] for post in stub.auth_posts] == [[PASSWORD]]


@pytest.mark.parametrize("terms", [False, True])
def test_a_captcha_or_unconfirmed_terms_hold(stub, browser, resume, terms):
    if terms:
        answers = {k: v for k, v in CONFIRMED.items() if k != "legal_attestation"}
        held = run(
            request(
                stub,
                resume,
                workday_account=account(stub, create=True),
                answers=answers,
            ),
            browser=browser,
        )
        assert held.status == "needs_review" and "accepts its terms" in held.error
        assert EMAIL not in stub.accounts
    else:
        stub.add_account(EMAIL, PASSWORD)
        stub.captcha_on_signin = True
        held = run(
            request(stub, resume, workday_account=account(stub)), browser=browser
        )
        assert held.status == "needs_review" and "CAPTCHA" in held.error
        assert not stub.auth_posts, "nothing was posted past the CAPTCHA"


def test_already_applied_is_reported(stub, browser, resume):
    stub.add_account(EMAIL, PASSWORD)
    stub.already_applied = True
    outcome = run(request(stub, resume, workday_account=account(stub)), browser=browser)
    assert outcome.status == "already_applied"


def test_without_an_account_the_run_holds_before_any_browser():
    outcome = run(
        ApplyRequest(
            "w:1",
            "",
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin/Architect_R1",
            [],
            PROFILE,
            CONFIRMED,
            None,
            None,
            "",
            True,
        )
    )
    assert outcome.status == "needs_review" and outcome.ats == "workday"
    assert "acme.wd5.myworkdayjobs.com" in outcome.error and not outcome.steps


def test_helpers():
    options = [
        _Option(label=text, value=text, ref="") for text in ("Male", "Female", DECLINE)
    ]
    assert _choose("Decline to answer", options).label == DECLINE
    assert _choose("female", options).label == "Female"
    assert _choose("Other", options) is None
    assert _step_kind("Voluntary Disclosures") == "voluntary"
    assert _step_kind("Self Identify") == "self_identify"
    assert _step_kind("Review") == "review"
    password = workday_accounts.generate_password()
    assert len(password) == 20 and any(c.isupper() for c in password)
    assert any(c.islower() for c in password) and any(c.isdigit() for c in password)
    assert any(not c.isalnum() for c in password)
    assert workday_accounts.mask_email("hyma@example.com") == "hy•••@example.com"
    assert (
        workday_accounts.normalize_host(
            "https://Acme.wd5.myworkdayjobs.com/en-US/External"
        )
        == "acme.wd5.myworkdayjobs.com"
    )
    for bad in ("", "example.com", "not a host", "evil.com/x.myworkdayjobs.com"):
        with pytest.raises(ValueError):
            workday_accounts.normalize_host(bad)


# --------------------------------------------------------------------------- #
# The account store
# --------------------------------------------------------------------------- #


def _db(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_credentials_are_encrypted_at_rest(tmp_path):
    store = SecretStore(Fernet.generate_key())
    path = tmp_path / "user.sqlite3"
    with _db(path) as connection:
        saved = workday_accounts.save_account(
            connection,
            store,
            host="acme.wd5.myworkdayjobs.com",
            email=EMAIL,
            password=PASSWORD,
        )
    assert "password" not in saved and saved["email"] != EMAIL
    raw = path.read_bytes()
    assert PASSWORD.encode() not in raw
    with _db(path) as connection:
        row = connection.execute(
            "SELECT password_ciphertext FROM workday_accounts"
        ).fetchone()
        assert store.decrypt(row[0]) == PASSWORD
        loaded = workday_accounts.load_credentials(
            connection, store, "acme.wd5.myworkdayjobs.com"
        )
    assert loaded and loaded["password"] == PASSWORD and loaded["origin"] == "person"


def test_create_for_me_saves_a_generated_password_before_the_run(tmp_path):
    store = SecretStore(Fernet.generate_key())
    path = tmp_path / "user.sqlite3"
    url = "https://globex.wd1.myworkdayjobs.com/en-US/Careers/job/Remote/Engineer_R9"

    def connect():
        return _db(path)

    assert (
        workday_accounts.credentials_for_run(connect, store, url, profile_email=EMAIL)
        is None
    )
    with _db(path) as connection:
        workday_accounts.set_preferences(connection, auto_create=True)
    assert (
        workday_accounts.credentials_for_run(connect, store, "https://jobs.lever.co/x")
        is None
    )
    first = workday_accounts.credentials_for_run(
        connect, store, url, profile_email=EMAIL
    )
    assert first is not None and first.create and first.email == EMAIL
    assert first.host == "globex.wd1.myworkdayjobs.com" and len(first.password) == 20
    assert first.password.encode() not in path.read_bytes()
    first.on_result("verification_needed", "Workday sent a verification email.")
    again = workday_accounts.credentials_for_run(
        connect, store, url, profile_email=EMAIL
    )
    assert again is not None and again.password == first.password
    assert not again.create, "after the account exists the run signs in"
    with _db(path) as connection:
        [listed] = workday_accounts.list_accounts(connection)
    assert listed["state"] == "verification_needed" and listed["origin"] == "generated"
    assert listed["last_used_at"]


# --------------------------------------------------------------------------- #
# The HTTP API
# --------------------------------------------------------------------------- #


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Requested-With", "aira")
        req.add_header("Origin", self.base)
        try:
            with self.opener.open(req, timeout=30) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")


@pytest.fixture
def server(tmp_path, monkeypatch):
    import auto_apply_assistant as app

    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.delenv("AIRA_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=50, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "wd@example.com", "password": "a long enough password 123"},
    )
    assert status in (200, 201), body
    try:
        yield client, app, tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_accounts_api_masks_and_encrypts(server):
    client, app, tmp_path = server
    base = "/api/autopilot/workday-accounts"
    status, body = client.call("GET", base)
    assert status == 200 and json.loads(body) == {"accounts": [], "auto_create": False}

    status, body = client.call(
        "POST",
        base,
        {
            "host": "https://acme.wd5.myworkdayjobs.com/en-US/External",
            "email": EMAIL,
            "password": PASSWORD,
        },
    )
    assert status == 201, body
    assert PASSWORD not in body and EMAIL not in body
    assert json.loads(body)["account"]["host"] == "acme.wd5.myworkdayjobs.com"

    for bad in (
        {"host": "example.com", "email": EMAIL, "password": "x"},
        {"host": "acme.wd5.myworkdayjobs.com", "email": EMAIL, "password": "again"},
        {
            "host": "beta.wd5.myworkdayjobs.com",
            "email": "not-an-email",
            "password": "x",
        },
        {"host": "beta.wd5.myworkdayjobs.com", "email": EMAIL},
    ):
        status, body = client.call("POST", base, bad)
        assert status == 400, (bad, body)

    status, body = client.call(
        "POST",
        base,
        {"host": "globex.wd1.myworkdayjobs.com", "email": EMAIL, "create_for_me": True},
    )
    assert status == 201 and json.loads(body)["account"]["origin"] == "generated"

    status, body = client.call("GET", base)
    listed = json.loads(body)["accounts"]
    assert [item["host"] for item in listed] == [
        "acme.wd5.myworkdayjobs.com",
        "globex.wd1.myworkdayjobs.com",
    ]
    assert all(item["email"] == "hy•••@example.com" for item in listed)
    assert all(
        "password" not in item and "password_ciphertext" not in item for item in listed
    )

    new_password = "N3w-Workday-Pass!"
    status, body = client.call(
        "PATCH", f"{base}/acme.wd5.myworkdayjobs.com", {"password": new_password}
    )
    assert status == 200 and new_password not in body
    status, _ = client.call(
        "PATCH", f"{base}/nobody.wd5.myworkdayjobs.com", {"password": "x"}
    )
    assert status == 404

    # At rest: nothing in any database file holds the passwords; the ciphertext opens with the key.
    for path in tmp_path.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert (
                PASSWORD.encode() not in data and new_password.encode() not in data
            ), path

    def holds_accounts(path: Path) -> bool:
        with _db(path) as connection:
            return bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'workday_accounts'"
                ).fetchone()
            )

    [user_db] = [p for p in tmp_path.rglob("*.sqlite3") if holds_accounts(p)]
    with _db(user_db) as connection:
        row = connection.execute(
            "SELECT password_ciphertext FROM workday_accounts WHERE host = 'acme.wd5.myworkdayjobs.com'"
        ).fetchone()
    assert app._airaapi_secret_store().decrypt(row[0]) == new_password

    status, body = client.call(
        "POST", "/api/autopilot/workday-settings", {"auto_create": True}
    )
    assert status == 200 and json.loads(body)["auto_create"] is True
    status, body = client.call(
        "POST", "/api/autopilot/workday-settings", {"auto_create": "yes"}
    )
    assert status == 400

    status, _ = client.call("DELETE", f"{base}/acme.wd5.myworkdayjobs.com")
    assert status == 200
    status, _ = client.call("DELETE", f"{base}/acme.wd5.myworkdayjobs.com")
    assert status == 404
    status, body = client.call("GET", base)
    assert [item["host"] for item in json.loads(body)["accounts"]] == [
        "globex.wd1.myworkdayjobs.com"
    ]


def test_accounts_api_needs_a_session(server):
    client, _app, _ = server
    stranger = Client(client.base)
    status, _ = stranger.call("GET", "/api/autopilot/workday-accounts")
    assert status == 401


def test_the_settings_page_shows_the_workday_card():
    static = Path(__file__).resolve().parents[1] / "auto_apply_assistant"
    index = (static / "index.html").read_text(encoding="utf-8")
    settings = (static / "views-settings.js").read_text(encoding="utf-8")
    card = (static / "views-workday.js").read_text(encoding="utf-8")
    assert index.index("views-settings.js") < index.index("views-workday.js")
    assert 'Aira.settingsCards ? Aira.settingsCards("integrations") : []' in settings
    assert "Aira.settingsCards = " in card and "/api/autopilot/workday-accounts" in card
    assert 'password.value = ""' in card, "the typed password is cleared after saving"
