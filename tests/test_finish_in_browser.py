"""Finish in browser (F14): a run held for a CAPTCHA, a sign-in or a code waits for the person, then carries on.

The engine tests drive :func:`aira.apply_engine.run` with a live :class:`aira.handoff.Handoff` against the stand-in
ATS (``tests/ats_stub.py``): a fake CAPTCHA the test "solves" (as the person would in the window), a sign-in page,
the time limit, Give up and I'm done. The HTTP journeys start the real server, let a plan's run be held for the
CAPTCHA, then open Finish in browser from Needs you and follow the item through its states. Chromium runs headless
in tests (``FINISH_IN_BROWSER_HEADLESS``); people get a visible window. Nothing here touches a real site or CAPTCHA.
"""

# ruff: noqa: F811
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from test_autopilot import (  # noqa: F401
    BoardAdapter,
    ats,
    make_plan,
    ok,
    posting,
    server,
    sign_up,
    wait_idle,
)
from test_apply_engine import CONFIRMED, PROFILE

import auto_apply_assistant as app
from aira import autopilot, handoff
from aira.apply_engine import ApplyRequest, run
from aira.pdf_text import text_to_pdf
from monosage_cloud import policy

# Every test that runs the form engine opens Chromium (in the engine's own thread, as the server does).
browser_test = pytest.mark.browser


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "Resume.pdf"
    path.write_bytes(text_to_pdf("Hyma Test\nCloud architect", "Resume"))
    return str(path)


def request(stub, resume, *, ats="generic", variant="captcha_step", **kwargs):
    return ApplyRequest(
        job_id=f"{ats}:fib",
        source=ats,
        application_url=stub.form_url(ats, variant),
        fields=[],
        profile=PROFILE,
        standard_answers=CONFIRMED,
        resume_path=resume,
        cover_letter_path=None,
        cover_letter_text="Dear hiring team, I would like to apply.",
        submit=True,
        headless=True,
        timeout_seconds=40,
        **kwargs,
    )


class Background:
    """Runs the engine on its own thread (it launches its own Chromium there), as the server's runner does."""

    def __init__(self, apply_request: ApplyRequest) -> None:
        self.outcome = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(
            target=self._go, args=(apply_request,), daemon=True
        )
        self.thread.start()

    def _go(self, apply_request: ApplyRequest) -> None:
        try:
            self.outcome = run(apply_request)
        except BaseException as error:  # noqa: BLE001 - re-raised in the test
            self.error = error

    def result(self, timeout: float = 90):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "the engine did not finish"
        if self.error is not None:
            raise self.error
        return self.outcome


def until(condition, timeout: float = 60, message: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {message}")


def waiting(session: handoff.Handoff, background: Background):
    def ready():
        if not background.thread.is_alive():
            raise AssertionError(f"the engine ended early: {background.outcome}")
        return session.state == "waiting"

    until(ready, message="the window to wait for the person")
    return session.view()


# --------------------------------------------------------------------------- #
# The engine with a live session
# --------------------------------------------------------------------------- #


@browser_test
def test_without_a_session_the_run_is_held_and_keeps_its_browser_state(
    ats, resume, tmp_path
):
    kept = tmp_path / "state" / "run.json"
    outcome = run(request(ats, resume, keep_state_path=str(kept)))
    assert outcome.status == "needs_review" and outcome.hold == "captcha"
    assert "CAPTCHA" in outcome.error and not ats.submissions
    assert kept.is_file() and "stub_session" in kept.read_text(encoding="utf-8")
    # an ordinary held question is not a step for the browser
    other = run(request(ats, resume, variant="unknown_required"))
    assert other.status == "needs_review" and other.hold == "" and other.unanswered


@browser_test
def test_the_person_solves_the_captcha_and_the_run_submits_with_the_same_cookies(
    ats, resume, tmp_path
):
    kept = tmp_path / "run.json"
    held = run(request(ats, resume, keep_state_path=str(kept)))
    assert held.hold == "captcha" and ats.sessions_issued == ["s1"]
    session = handoff.Handoff("r1")
    background = Background(
        request(
            ats,
            resume,
            handoff=session,
            storage_state_path=str(kept),
            keep_state_path=str(kept),
        )
    )
    view = waiting(session, background)
    assert view["kind"] == "captcha" and "CAPTCHA" in view["message"]
    assert view["seconds_left"] > 800 and not ats.submissions
    ats.solve_captcha()  # what the person does in the window
    outcome = background.result()
    assert outcome.status == "submitted", (outcome.error, outcome.steps)
    assert outcome.handoffs == [
        {
            "kind": "captcha",
            "outcome": "cleared",
            "seconds": outcome.handoffs[0]["seconds"],
        }
    ]
    [submission] = ats.submissions
    assert submission.value("g-recaptcha-response") == "stub-captcha-token"
    # the window started from the held run's cookies: no new session was issued
    assert submission.cookie == "s1" and ats.sessions_issued == ["s1"]
    assert "You finished the step; Monosage carries on" in outcome.steps


@browser_test
def test_the_time_limit_closes_the_window_and_holds_again(ats, resume):
    session = handoff.Handoff("r2", timeout_seconds=1.5)
    background = Background(request(ats, resume, handoff=session))
    outcome = background.result()
    assert outcome.status == "needs_review" and outcome.hold == "captcha"
    assert outcome.handoffs[0]["outcome"] == "timed_out"
    assert outcome.error.startswith("Finish in browser timed out")
    assert not ats.submissions and session.state == "finished"


@browser_test
def test_give_up_ends_the_wait_without_submitting(ats, resume):
    session = handoff.Handoff("r3")
    background = Background(request(ats, resume, handoff=session))
    waiting(session, background)
    session.decide("give_up")
    outcome = background.result()
    assert outcome.status == "needs_review" and outcome.hold == "captcha"
    assert outcome.handoffs[0]["outcome"] == "gave_up"
    assert outcome.error.startswith("You stopped Finish in browser")
    assert not ats.submissions
    with pytest.raises(ValueError):
        session.decide("done")  # the session is over


@browser_test
def test_im_done_carries_on_when_the_page_gives_no_sign(ats, resume):
    # The plain stand-in CAPTCHA never shows an answer: only the person knows it is done.
    session = handoff.Handoff("r4")
    background = Background(request(ats, resume, variant="captcha", handoff=session))
    waiting(session, background)
    with pytest.raises(ValueError):
        session.decide("maybe")
    session.decide("done")
    outcome = background.result()
    assert outcome.status == "submitted", (outcome.error, outcome.steps)
    assert outcome.handoffs[0]["outcome"] == "done" and len(ats.submissions) == 1


@browser_test
def test_a_sign_in_wall_waits_for_the_person_then_fills_and_submits(ats, resume):
    held = run(request(ats, resume, variant="signin_step"))
    assert held.status == "needs_review" and held.hold == "sign_in"
    session = handoff.Handoff("r5")
    background = Background(
        request(ats, resume, variant="signin_step", handoff=session)
    )
    assert waiting(session, background)["kind"] == "sign_in"
    ats.sign_in()
    outcome = background.result()
    assert outcome.status == "submitted", (outcome.error, outcome.steps)
    assert outcome.handoffs[0] == {
        "kind": "sign_in",
        "outcome": "cleared",
        "seconds": outcome.handoffs[0]["seconds"],
    }
    assert ats.submissions[0].value("email") == PROFILE["email"]


def test_the_session_registry_has_one_window_per_run():
    first = handoff.open_session("a" * 32, owner="u1")
    try:
        with pytest.raises(ValueError):
            handoff.open_session("a" * 32, owner="u1")
        assert handoff.active("a" * 32, owner="u2") is None
        assert handoff.active("a" * 32, owner="u1") is first
        assert [s.run_id for s in handoff.sessions_for("u1")] == ["a" * 32]
    finally:
        handoff.close("a" * 32, first)
    assert handoff.active("a" * 32) is None


# --------------------------------------------------------------------------- #
# HTTP journeys: Needs you, the window, the outcome on the application
# --------------------------------------------------------------------------- #


def _held_run(server, ats, monkeypatch, number: int):
    sign_up(server)
    monkeypatch.setattr(app, "FINISH_IN_BROWSER_HEADLESS", True)
    monkeypatch.setattr(
        BoardAdapter,
        "postings",
        [posting(ats, number, "Cloud Architect", variant="captcha_step")],
    )
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [held] = overview["open"]
    return overview, held


def _live(server, run_id: str, state: str = "waiting"):
    def ready():
        overview = ok(server, "GET", "/api/autopilot")
        item = next((run for run in overview["open"] if run["run_id"] == run_id), None)
        live = (item or {}).get("handoff_live") or {}
        return (overview, item) if live.get("state") == state else None

    return until(ready, timeout=90, message=f"Finish in browser to be {state}")


def _kept_states(tmp_path: Path) -> list[Path]:
    return list(tmp_path.rglob("handoff-state/*.json"))


def _history(server, job_id: str) -> list[str]:
    job = ok(server, "GET", f"/api/jobs/{job_id}")["job"]
    events = job.get("events") or job.get("history") or []
    if not events:
        events = ok(server, "GET", f"/api/jobs/{job_id}/events").get("events", [])
    return [event["reason"] for event in events]


@browser_test
def test_http_needs_you_offers_finish_in_browser_and_the_run_submits(
    server, ats, monkeypatch, tmp_path
):
    overview, held = _held_run(server, ats, monkeypatch, 31)
    # state 1: held for the CAPTCHA, the rest filled; Finish in browser is offered here (the local build)
    assert held["status"] == "needs_review" and held["hold"] == "captcha", held
    assert held["unanswered"] == [] and "handoff_live" not in held
    assert overview["status"]["finish_in_browser"] is True
    assert held["job"]["stage"] == "needs_review" and not ats.submissions
    assert len(_kept_states(tmp_path)) == 1, "the held run kept its cookies"
    # nothing to decide before a window is open
    status, body, _ = server.call(
        "POST",
        f"/api/autopilot/runs/{held['run_id']}/finish/decision",
        {"choice": "done"},
    )
    assert status == 400, body
    started = ok(server, "POST", f"/api/autopilot/runs/{held['run_id']}/finish", {})
    assert started["started"] and started["handoff"]["state"] in {"opening", "waiting"}
    # state 2: the window waits for the person; the item stays in Needs you with I'm done and Give up
    overview, item = _live(server, held["run_id"])
    assert item["status"] == "running" and item["handoff_live"]["kind"] == "captcha"
    assert item["handoff_live"]["seconds_left"] > 800
    assert overview["status"]["running"]["label"] == "Finishing in the browser"
    one = ok(server, "GET", f"/api/autopilot/runs/{held['run_id']}")["run"]
    assert one["handoff_live"]["state"] == "waiting"
    status, _, _ = server.call(
        "POST", f"/api/autopilot/runs/{held['run_id']}/finish", {}
    )
    assert status == 400, "one window per application"
    ats.solve_captcha()  # the person solves it in the window
    overview = wait_idle(server)
    done = ok(server, "GET", f"/api/autopilot/runs/{held['run_id']}")["run"]
    # state 3: submitted, recorded on the application, gone from Needs you
    assert done["status"] == "submitted" and done["hold"] == "", done
    assert (
        done["handoff"]["outcome"] == "cleared"
        and done["handoff"]["result"] == "submitted"
    )
    assert overview["open"] == [] and len(ats.submissions) == 1
    assert ats.submissions[0].cookie == ats.sessions_issued[0] == "s1"
    job = ok(server, "GET", f"/api/jobs/{held['job_id']}")["job"]
    assert job["stage"] == "applied" and job["submission"]["method"] == "auto"
    assert any(
        reason.startswith("Finish in browser: you did the CAPTCHA")
        for reason in _history(server, held["job_id"])
    )
    assert _kept_states(tmp_path) == [], "the kept cookies are removed once it is done"


@browser_test
def test_http_the_window_times_out_then_give_up_and_both_stay_in_needs_you(
    server, ats, monkeypatch, tmp_path
):
    _, held = _held_run(server, ats, monkeypatch, 32)
    run_id = held["run_id"]
    monkeypatch.setattr(handoff, "TIMEOUT_SECONDS", 2)
    ok(server, "POST", f"/api/autopilot/runs/{run_id}/finish", {})
    overview = wait_idle(server)
    [again] = overview["open"]
    # timed out: held again for the CAPTCHA, Finish in browser offered again, the outcome on the run and the job
    assert again["run_id"] == run_id and again["status"] == "needs_review", again
    assert again["hold"] == "captcha" and "handoff_live" not in again
    assert (
        again["handoff"]["outcome"] == "timed_out"
        and again["handoff"]["result"] == "needs_review"
    )
    assert again["error"].startswith("Finish in browser timed out")
    assert any("timed out" in reason for reason in _history(server, held["job_id"]))
    assert not ats.submissions
    monkeypatch.setattr(handoff, "TIMEOUT_SECONDS", 15 * 60)
    ok(server, "POST", f"/api/autopilot/runs/{run_id}/finish", {})
    _live(server, run_id)
    status, _, _ = server.call(
        "POST", f"/api/autopilot/runs/{run_id}/finish/decision", {"choice": "later"}
    )
    assert status == 400
    ok(
        server,
        "POST",
        f"/api/autopilot/runs/{run_id}/finish/decision",
        {"choice": "give_up"},
    )
    overview = wait_idle(server)
    [gave_up] = overview["open"]
    assert gave_up["status"] == "needs_review" and gave_up["hold"] == "captcha"
    assert gave_up["handoff"]["outcome"] == "gave_up" and not ats.submissions
    assert any(
        "you gave up on the CAPTCHA" in reason
        for reason in _history(server, held["job_id"])
    )
    # Skip still works and drops the kept cookies
    ok(server, "POST", f"/api/autopilot/runs/{run_id}/skip", {})
    assert ok(server, "GET", "/api/autopilot")["open"] == []
    assert _kept_states(tmp_path) == []


@browser_test
def test_http_finish_in_browser_refuses_what_it_cannot_finish(server, ats, monkeypatch):
    sign_up(server)
    monkeypatch.setattr(app, "FINISH_IN_BROWSER_HEADLESS", True)
    monkeypatch.setattr(
        BoardAdapter,
        "postings",
        [posting(ats, 33, "Cloud Architect", variant="unknown_required")],
    )
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    [held] = wait_idle(server)["open"]
    assert held["hold"] == "" and held["unanswered"]
    status, body, _ = server.call(
        "POST", f"/api/autopilot/runs/{held['run_id']}/finish", {}
    )
    assert status == 400 and "not waiting for a step" in body["error"], body
    status, _, _ = server.call("POST", f"/api/autopilot/runs/{'0' * 32}/finish", {})
    assert status == 404


def test_hosted_needs_you_links_to_the_employer_page_instead(monkeypatch):
    # The hosted build's runs live on workers the sign-in broker cannot attach to: the routes are not offered.
    assert policy.POLICY["api_autopilot_run_finish"].scope == "deny"
    assert policy.POLICY["api_autopilot_run_finish_decision"].scope == "deny"
    monkeypatch.setattr(app.HOSTING, "start_background", lambda *_args: {})
    assert app._finish_in_browser_available() is False
    with pytest.raises(ValueError, match="your own computer"):
        app._autopilot_finish_in_browser({"user_id": "u"}, "f" * 32)


def test_a_held_run_view_names_its_step(tmp_path):
    import sqlite3

    from aira import schema

    connection = sqlite3.connect(tmp_path / "runs.sqlite3")
    connection.row_factory = sqlite3.Row
    schema.migrate(connection)
    assert schema.LATEST_VERSION >= 24
    job = {"job_id": "lever:1", "application_url": "https://jobs.lever.co/acme/1"}
    created = autopilot.create_run(connection, job=job, mode="apply", trigger="user")
    assert created["hold"] == "" and created["handoff"] == {}
    updated = autopilot.update_run(
        connection,
        created["run_id"],
        {
            "status": "needs_review",
            "hold": "captcha",
            "handoff": {"outcome": "gave_up"},
        },
    )
    assert updated["hold"] == "captcha" and updated["handoff"] == {"outcome": "gave_up"}
    assert updated["open"]
    assert (
        autopilot.normalize_outcome({"status": "needs_review", "hold": "nonsense"})[
            "hold"
        ]
        == ""
    )


# --------------------------------------------------------------------------- #
# The Needs you item in a real browser: held, the window open, given up
# --------------------------------------------------------------------------- #


def _session_cookies(client, base: str) -> list[dict]:
    import urllib.request

    jar = next(
        handler.cookiejar
        for handler in client.opener.handlers
        if isinstance(handler, urllib.request.HTTPCookieProcessor)
    )
    return [{"name": c.name, "value": c.value, "url": base} for c in jar]


@browser_test
def test_ui_the_needs_you_item_goes_from_held_to_the_window_and_back(
    server, ats, monkeypatch
):
    from patchright.sync_api import sync_playwright

    _held_run(server, ats, monkeypatch, 34)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 900})
            context.add_cookies(_session_cookies(server, server.base))
            page = context.new_page()
            page.set_default_timeout(60_000)
            problems: list[str] = []
            page.on(
                "console",
                lambda m: problems.append(m.text) if m.type == "error" else None,
            )
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(server.base + "/#/needs")
            # held: what the step is, Finish in browser, the employer's page
            card = page.locator("article.aap-open.hold[data-hold='captcha']")
            card.wait_for()
            assert "A CAPTCHA is on the form." in card.inner_text()
            assert card.get_by_text("A step needs you").count() == 1
            assert (
                card.get_by_role("link", name="Open the employer's page ↗").count() == 1
            )
            card.get_by_role("button", name="Finish in browser").click()
            page.get_by_role("button", name="Open the window").click()
            # the window is open: the item waits with I'm done and Give up
            live = page.locator("article.aap-open.handoff[data-handoff='waiting']")
            live.wait_for(timeout=90_000)
            assert (
                "Waiting for you in the browser window: do the CAPTCHA there"
                in live.inner_text()
            )
            assert live.get_by_role("button", name="I'm done").is_enabled()
            live.get_by_role("button", name="Give up").click()
            page.locator(".modal").get_by_role("button", name="Give up").click()
            # given up: held again, saying so, Finish in browser offered again
            again = page.locator(
                "article.aap-open.hold[data-hold='captcha'] .aap-handoff-last"
            )
            again.wait_for(timeout=90_000)
            assert again.inner_text() == "You gave up on Finish in browser last time."
            assert not ats.submissions
            assert problems == [], problems
        finally:
            browser.close()
