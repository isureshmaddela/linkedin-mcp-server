"""Password reset (a one-time code shown in the console Aira runs in) and a signed-in password change."""

# ruff: noqa: F811
from __future__ import annotations

import logging
import re
import sqlite3

import pytest
from test_careers import PASSWORD, env, stub  # noqa: F401

import auto_apply_assistant as app

EMAIL = "reset.person@example.com"
NEW_PASSWORD = "a brand new password 456"
CODE = re.compile(r"Reset code for (\S+): ([A-Z2-9]{4}-[A-Z2-9]{4})")


def _register(env):
    api = env.api()
    status, body = api.call(
        "POST", "/api/auth/register", {"email": EMAIL, "password": PASSWORD}
    )
    assert status == 201, body
    return api


def _ask_for_code(env, caplog, email=EMAIL) -> tuple[int, dict, str | None]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=app.logger.name):
        status, body = env.api().call(
            "POST", "/api/auth/password/forgot", {"email": email}
        )
    found = [CODE.search(record.getMessage()) for record in caplog.records]
    codes = [match.group(2) for match in found if match]
    return status, body, codes[-1] if codes else None


def test_a_reset_code_sets_a_new_password_and_signs_out_everywhere(env, caplog):
    old_session = _register(env)
    status, body, code = _ask_for_code(env, caplog)
    assert status == 200 and "window where Monosage is running" in body["message"]
    assert code, "the code is shown in the console"
    with sqlite3.connect(env.tmp_path / "accounts.sqlite3") as connection:
        stored = connection.execute("SELECT code_hash FROM password_resets").fetchone()[
            0
        ]
    assert code not in stored and code.replace("-", "") not in stored

    fresh = env.api()
    status, body = fresh.call(
        "POST",
        "/api/auth/password/reset",
        {
            "email": EMAIL.upper(),
            "code": code.lower().replace("-", " "),
            "password": NEW_PASSWORD,
        },
    )
    assert status == 200 and body["email"] == EMAIL
    assert fresh.get("/api/auth/me")["email"] == EMAIL, (
        "signed in with the new password"
    )
    assert old_session.call("GET", "/api/auth/me")[0] == 401, "older sessions end"

    other = env.api()
    assert (
        other.call("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})[0]
        == 400
    )
    assert (
        other.call(
            "POST", "/api/auth/login", {"email": EMAIL, "password": NEW_PASSWORD}
        )[0]
        == 200
    )
    status, body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": code, "password": PASSWORD},
    )
    assert status == 400 and "wrong or has expired" in body["error"], (
        "a code works once"
    )


def test_an_unknown_email_gets_the_same_answer_and_no_code(env, caplog):
    _register(env)
    known = _ask_for_code(env, caplog)
    unknown = _ask_for_code(env, caplog, "nobody@example.com")
    assert known[0] == unknown[0] == 200 and known[1] == unknown[1]
    assert unknown[2] is None
    status, body = env.api().call(
        "POST", "/api/auth/password/forgot", {"email": "not an email"}
    )
    assert status == 400


def test_wrong_codes_are_limited_and_codes_expire(env, caplog, monkeypatch):
    _register(env)
    _status, _body, code = _ask_for_code(env, caplog)
    assert code
    wrong = "ZZZZ-ZZZZ" if code != "ZZZZ-ZZZZ" else "YYYY-YYYY"
    for _ in range(app.RESET_CODE_MAX_ATTEMPTS):
        status, body = env.api().call(
            "POST",
            "/api/auth/password/reset",
            {"email": EMAIL, "code": wrong, "password": NEW_PASSWORD},
        )
        assert status == 400 and "wrong or has expired" in body["error"]
    status, _body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": code, "password": NEW_PASSWORD},
    )
    assert status == 400, "the right code no longer works after too many wrong ones"

    _status, _body, code = _ask_for_code(env, caplog)
    real_time = app.time.time
    monkeypatch.setattr(
        app.time, "time", lambda: real_time() + app.RESET_CODE_TTL_SECONDS + 1
    )
    status, _body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": code, "password": NEW_PASSWORD},
    )
    assert status == 400, "an expired code is refused"
    monkeypatch.setattr(app.time, "time", real_time)
    assert (
        env.api().call(
            "POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD}
        )[0]
        == 200
    )


def test_a_new_code_replaces_the_old_one_and_short_passwords_are_refused(env, caplog):
    _register(env)
    first = _ask_for_code(env, caplog)[2]
    second = _ask_for_code(env, caplog)[2]
    assert first and second and first != second
    status, body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": second, "password": "short"},
    )
    assert status == 400 and "12" in body["error"]
    status, _body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": first, "password": NEW_PASSWORD},
    )
    assert status == 400
    status, _body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": second, "password": NEW_PASSWORD},
    )
    assert status == 200


def test_changing_the_password_keeps_this_session_and_ends_the_others(env):
    api = _register(env)
    other = env.api()
    assert (
        other.call("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})[0]
        == 200
    )
    status, body = api.call(
        "POST",
        "/api/account/password",
        {"current_password": "not it at all", "new_password": NEW_PASSWORD},
    )
    assert status == 400 and "incorrect" in body["error"]
    status, body = api.call(
        "POST",
        "/api/account/password",
        {"current_password": PASSWORD, "new_password": "short"},
    )
    assert status == 400
    status, body = api.call(
        "POST",
        "/api/account/password",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert status == 200, body
    assert api.get("/api/auth/me")["email"] == EMAIL
    assert other.call("GET", "/api/auth/me")[0] == 401
    assert (
        env.api().call(
            "POST", "/api/auth/login", {"email": EMAIL, "password": NEW_PASSWORD}
        )[0]
        == 200
    )
    assert env.api().call("POST", "/api/account/password", {})[0] == 401


def test_deleting_an_account_removes_its_reset_code(env, caplog):
    api = _register(env)
    assert _ask_for_code(env, caplog)[2]
    assert api.call("POST", "/api/account/delete", {"password": PASSWORD})[0] == 200
    with sqlite3.connect(env.tmp_path / "accounts.sqlite3") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "route",
    [
        "api_auth_password_forgot",
        "api_auth_password_reset",
        "api_account_password",
        "api_settings_reset_mail",
        "api_settings_reset_mail_save",
        "api_settings_reset_mail_clear",
        "api_settings_reset_mail_test",
    ],
)
def test_the_hosted_build_keeps_its_own_password_routes(route):
    from aira_cloud import policy

    assert policy.POLICY[route] is policy.DENY


# --- emailing the code ------------------------------------------------------------------------


@pytest.fixture
def smtp():
    from smtp_stub import SmtpStub

    server = SmtpStub(require_login=("sender@example.com", "mail-secret")).start()
    yield server
    server.stop()


def _custom_mail(smtp, **extra):
    return {
        "provider": "custom",
        "smtp_host": "127.0.0.1",
        "smtp_port": smtp.port,
        "smtp_security": "none",
        "smtp_username": "sender@example.com",
        "password": "mail-secret",
        "from_email": "sender@example.com",
        "from_name": "Monosage at home",
        **extra,
    }


def _wait_for(smtp, count=1):
    import time

    deadline = time.time() + 10
    while len(smtp.messages) < count and time.time() < deadline:
        time.sleep(0.05)
    return smtp.messages


def test_gmail_settings_keep_the_app_password_secret(env, monkeypatch):
    monkeypatch.delenv("AIRA_SMTP_HOST", raising=False)
    owner = _register(env)
    status, body = owner.call("GET", "/api/settings/reset-mail")
    assert status == 200 and body["configured"] is False and body["can_edit"] is True
    status, body = owner.call(
        "POST",
        "/api/settings/reset-mail",
        {"provider": "gmail", "smtp_username": "me@gmail.com"},
    )
    assert status == 400 and "app password" in body["error"]
    status, body = owner.call(
        "POST",
        "/api/settings/reset-mail",
        {
            "provider": "gmail",
            "smtp_username": "me@gmail.com",
            "password": "abcd efgh ijkl mnop",
            "smtp_host": "evil.example",
        },
    )
    assert status == 200, body
    assert (body["smtp_host"], body["smtp_port"], body["smtp_security"]) == (
        "smtp.gmail.com",
        587,
        "starttls",
    )
    assert body["from_email"] == "me@gmail.com" and body["has_password"] is True
    assert "password" not in body and "password_cipher" not in body
    with sqlite3.connect(env.tmp_path / "accounts.sqlite3") as connection:
        cipher = connection.execute(
            "SELECT password_cipher FROM reset_mail"
        ).fetchone()[0]
    assert b"abcd" not in cipher
    assert app._reset_mail_settings()["password"] == "abcdefghijklmnop", (
        "spaces from Google's display are dropped"
    )
    # saving again without a password keeps the saved one
    status, body = owner.call(
        "POST",
        "/api/settings/reset-mail",
        {"provider": "gmail", "smtp_username": "me@gmail.com", "from_name": "Home"},
    )
    assert (
        status == 200 and body["has_password"] is True and body["from_name"] == "Home"
    )

    second = env.api()
    assert (
        second.call(
            "POST",
            "/api/auth/register",
            {"email": "second@example.com", "password": PASSWORD},
        )[0]
        == 201
    )
    status, body = second.call("GET", "/api/settings/reset-mail")
    assert status == 200 and body["can_edit"] is False and "password" not in body
    assert (
        second.call(
            "POST",
            "/api/settings/reset-mail",
            _custom_mail(type("S", (), {"port": 25})()),
        )[0]
        == 403
    )
    assert second.call("DELETE", "/api/settings/reset-mail")[0] == 403
    assert owner.call("DELETE", "/api/settings/reset-mail")[0] == 200
    assert owner.get("/api/settings/reset-mail")["configured"] is False


def test_the_reset_code_is_emailed_when_a_mail_server_is_set(
    env, caplog, smtp, monkeypatch
):
    monkeypatch.delenv("AIRA_SMTP_HOST", raising=False)
    owner = _register(env)
    assert owner.call("POST", "/api/settings/reset-mail", _custom_mail(smtp))[0] == 200
    status, body = owner.call("POST", "/api/settings/reset-mail/test", {})
    assert status == 200 and body["to"] == EMAIL
    assert _wait_for(smtp) and smtp.messages[0]["to"] == [EMAIL]

    status, body, code = _ask_for_code(env, caplog)
    assert status == 200 and body["emailed"] is True and "emailed" in body["message"]
    messages = _wait_for(smtp, 2)
    assert len(messages) == 2
    mail = smtp.parsed(-1)
    assert mail["To"] == EMAIL and "Monosage at home" in mail["From"]
    assert code in mail.get_payload(decode=True).decode()
    status, _body = env.api().call(
        "POST",
        "/api/auth/password/reset",
        {"email": EMAIL, "code": code, "password": NEW_PASSWORD},
    )
    assert status == 200

    # an unknown address gets the same answer and no email
    status, unknown, _code = _ask_for_code(env, caplog, "nobody@example.com")
    assert unknown == body
    assert len(_wait_for(smtp, 3)) == 2


def test_a_wrong_mail_password_is_reported_by_the_test_email(env, smtp, monkeypatch):
    monkeypatch.delenv("AIRA_SMTP_HOST", raising=False)
    owner = _register(env)
    assert (
        owner.call(
            "POST", "/api/settings/reset-mail", _custom_mail(smtp, password="wrong")
        )[0]
        == 200
    )
    status, body = owner.call("POST", "/api/settings/reset-mail/test", {})
    assert status == 400 and "refused the username or password" in body["error"]


def test_env_settings_are_used_when_nothing_is_saved(env, caplog, smtp, monkeypatch):
    for name, value in {
        "AIRA_SMTP_HOST": "127.0.0.1",
        "AIRA_SMTP_PORT": str(smtp.port),
        "AIRA_SMTP_SECURITY": "none",
        "AIRA_SMTP_USERNAME": "sender@example.com",
        "AIRA_SMTP_PASSWORD": "mail-secret",
    }.items():
        monkeypatch.setenv(name, value)
    owner = _register(env)
    summary = owner.get("/api/settings/reset-mail")
    assert (
        summary["configured"]
        and summary["source"] == "env"
        and summary["from_email"] == "sender@example.com"
    )
    _status, body, code = _ask_for_code(env, caplog)
    assert body["emailed"] is True
    assert _wait_for(smtp) and code in smtp.parsed(-1).get_payload(decode=True).decode()
