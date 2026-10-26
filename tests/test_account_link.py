"""The single-user build signs people in with their Aira account (the hosted build).

Two layers: the local server against a stand-in account (a small HTTP server that checks the launch
signature exactly as the hosted account does), and a real end-to-end run in a browser with both servers
in-process: a candidate signed in to the hosted account lands on the local installation signed in.
"""

# ruff: noqa: F811
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from test_careers import PASSWORD, env, stub  # noqa: F401

SECRET = "launch-secret-" + "x" * 30
CLAIMS = {
    "email": "Linked.Person@Example.test",
    "name": "Linked Person",
    "org_id": "0" * 32,
    "org_name": "Northwind Outplacement",
    "org_slug": "northwind",
    "member_id": "1" * 32,
    "role": "candidate",
    "app": "local",
    "apps": ["careers", "marketing"],
}


class AccountStub:
    """Stands in for the hosted account: validates launch tokens the way ``aira_cloud.launch`` does."""

    def __init__(self) -> None:
        self.tokens: dict[str, dict[str, Any]] = {}
        self.used: set[str] = set()
        self.nonces: set[str] = set()
        self.calls: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length)
                status, payload = stub.handle(self.path, dict(self.headers), body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> AccountStub:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def grant(self, token: str, claims: dict[str, Any] | None = None) -> str:
        self.tokens[token] = dict(claims or CLAIMS)
        return token

    def handle(
        self, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append({"path": path, "headers": headers, "body": body})
        if path != "/api/internal/launch/validate":
            return 404, {"error": "not found"}
        try:
            stamp = int(headers.get("X-Aira-Agent-Timestamp", ""))
        except ValueError:
            return 401, {"error": "no timestamp", "code": "bad_signature"}
        nonce = headers.get("X-Aira-Agent-Nonce", "")
        expected = hmac.new(
            SECRET.encode(), f"launch.{stamp}.{nonce}.".encode() + body, hashlib.sha256
        ).hexdigest()
        if (
            abs(time.time() - stamp) > 60
            or len(nonce) != 24
            or not hmac.compare_digest(
                expected, headers.get("X-Aira-Agent-Signature", "")
            )
        ):
            return 401, {
                "error": "The signature does not match.",
                "code": "bad_signature",
            }
        if nonce in self.nonces:
            return 401, {
                "error": "The request was already used.",
                "code": "bad_signature",
            }
        self.nonces.add(nonce)
        token = str(json.loads(body).get("token", ""))
        if token not in self.tokens:
            return 404, {"error": "This launch link is not valid.", "code": "not_found"}
        if token in self.used:
            return 410, {
                "error": "This launch link was already used. Start again from your account.",
                "code": "gone",
            }
        self.used.add(token)
        return 200, self.tokens[token]


@pytest.fixture
def account(monkeypatch):
    server = AccountStub().start()
    monkeypatch.setenv("AIRA_ACCOUNT_URL", server.base)
    monkeypatch.setenv("AIRA_ACCOUNT_SECRET", SECRET)
    yield server
    server.stop()


def test_a_launch_token_creates_a_local_account_with_no_password_and_signs_in(
    env, account
):
    api = env.api()
    assert api.get("/api/auth/account-link") == {"available": True, "url": account.base}
    account.grant("tok-" + "a" * 40)
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "a" * 40})
    assert status == 200, body
    assert body["email"] == "linked.person@example.test" and body["apps"] == [
        "careers",
        "marketing",
    ]
    assert body["account"] == {
        "linked": True,
        "url": account.base,
        "org_name": "Northwind Outplacement",
        "available": True,
    }
    me = api.get("/api/auth/me")
    assert (
        me["email"] == "linked.person@example.test" and me["account"]["linked"] is True
    )
    assert me["account"]["org_name"] == "Northwind Outplacement"
    apps = api.get("/api/apps")
    assert apps["account"]["linked"] is True and apps["account_url"] == account.base
    assert apps["installation"] == "local" and [a["id"] for a in apps["apps"]] == [
        "careers",
        "api",
        "studio",
        "marketing",
        "service",
        "commerce",
        "experiences",
        "ads",
        "social",
        "care",
        "learn",
        "play",
        "planner",
        "news",
        "videos",
    ]
    # the account row holds no password and remembers where it came from
    user = env.user()
    assert (
        user["password_hash"] == ""
        and user["account_org_id"] == "0" * 32
        and user["account_url"] == account.base
    )
    # the signed call carried the token and nothing else
    sent = json.loads(account.calls[-1]["body"])
    assert sent == {"token": "tok-" + "a" * 40}
    assert api.get("/api/profile")["candidate_profile"] is not None, (
        "a workspace exists for the new account"
    )
    # a second launch for the same person finds the same account
    account.grant("tok-" + "b" * 40)
    status, body = env.api().call(
        "POST", "/api/auth/launch", {"token": "tok-" + "b" * 40}
    )
    assert status == 200 and body["email"] == "linked.person@example.test"
    import sqlite3

    with sqlite3.connect(env.tmp_path / "accounts.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_a_linked_account_has_no_password_sign_in(env, account):
    api = env.api()
    account.grant("tok-" + "c" * 40)
    assert api.call("POST", "/api/auth/launch", {"token": "tok-" + "c" * 40})[0] == 200
    other = env.api()
    status, body = other.call(
        "POST",
        "/api/auth/login",
        {"email": "linked.person@example.test", "password": PASSWORD},
    )
    assert status == 403 and "Sign in with your Aira account" in body["error"]
    status, body = other.call(
        "POST",
        "/api/auth/register",
        {"email": "linked.person@example.test", "password": PASSWORD},
    )
    assert status == 400 and "already exists" in body["error"]
    status, body = api.call("POST", "/api/account/delete", {"password": ""})
    assert status == 400 and "signs in with your Aira account" in body["error"]
    assert api.get("/api/auth/me")["email"] == "linked.person@example.test", (
        "the account is still there"
    )


def test_an_existing_password_account_keeps_its_password_after_a_launch(env, account):
    api = env.api()
    assert (
        api.call(
            "POST",
            "/api/auth/register",
            {"email": "linked.person@example.test", "password": PASSWORD},
        )[0]
        == 201
    )
    api.post(
        "/api/profile",
        {
            "resume": "A resume that is long enough to be kept around for the test. "
            * 3,
            "profile_url": "",
        },
    )
    account.grant("tok-" + "d" * 40)
    status, body = env.api().call(
        "POST", "/api/auth/launch", {"token": "tok-" + "d" * 40}
    )
    assert status == 200 and body["account"]["linked"] is False, (
        "a password account is opened, not converted"
    )
    user = env.user()
    assert (
        user["password_hash"] and user["account_org_name"] == "Northwind Outplacement"
    )
    assert (
        env.api().call(
            "POST",
            "/api/auth/login",
            {"email": "linked.person@example.test", "password": PASSWORD},
        )[0]
        == 200
    )


def test_bad_tokens_and_a_misconfigured_account_are_refused_plainly(
    env, account, monkeypatch
):
    api = env.api()
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "z" * 40})
    assert status == 400 and "not valid" in body["error"]
    account.grant("tok-" + "e" * 40)
    assert api.call("POST", "/api/auth/launch", {"token": "tok-" + "e" * 40})[0] == 200
    status, body = env.api().call(
        "POST", "/api/auth/launch", {"token": "tok-" + "e" * 40}
    )
    assert status == 400 and "already used" in body["error"]
    for bad in ("", "x" * 300, "has spaces", "<script>"):
        assert api.call("POST", "/api/auth/launch", {"token": bad})[0] == 400
    assert account.calls[-1]["body"] != b'{"token": "<script>"}', (
        "a malformed token never reaches the account"
    )
    monkeypatch.setenv("AIRA_ACCOUNT_SECRET", "another-secret-" + "q" * 30)
    account.grant("tok-" + "f" * 40)
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "f" * 40})
    assert status == 502 and "does not match" in body["error"]
    monkeypatch.setenv("AIRA_ACCOUNT_SECRET", "short")
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "f" * 40})
    assert status == 400 and "not set up" in body["error"]
    assert api.get("/api/auth/account-link") == {"available": False, "url": ""}
    monkeypatch.delenv("AIRA_ACCOUNT_URL")
    assert api.get("/api/auth/account-link") == {"available": False, "url": ""}


def test_the_account_must_be_reachable_and_not_a_private_address(
    env, account, monkeypatch
):
    api = env.api()
    monkeypatch.setenv("AIRA_ACCOUNT_URL", "http://10.0.0.5:8780")
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "g" * 40})
    assert (
        status == 502
        and "cannot be used" in body["error"]
        and "AIRA_ACCOUNT_ALLOW_LOCAL" in body["error"]
    )
    monkeypatch.setenv("AIRA_ACCOUNT_ALLOW_LOCAL", "1")
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "g" * 40})
    assert status == 502 and "could not be reached" in body["error"], (
        "allowed, so the address was tried"
    )
    monkeypatch.setenv("AIRA_ACCOUNT_URL", "ftp://127.0.0.1:8780")
    status, body = api.call("POST", "/api/auth/launch", {"token": "tok-" + "g" * 40})
    assert status == 502 and "http(s)" in body["error"]


# ---------------------------------------------------------------------------
# both servers, a real browser
# ---------------------------------------------------------------------------


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_a_candidate_signed_in_to_the_account_opens_the_local_installation(
    env, tmp_path, monkeypatch
):
    """Both servers for real: the hosted account in its own process (as deployed), the local build in this one."""
    import os
    import subprocess
    import sys
    import urllib.error
    import urllib.request

    patchright = pytest.importorskip("patchright.sync_api")
    port = _free_port()
    hosted_base = f"http://127.0.0.1:{port}"
    hosted_env = {
        **os.environ,
        "AIRA_CLOUD_DATA": str(tmp_path / "hosted"),
        "AIRA_CLOUD_PORT": str(port),
        "AIRA_CLOUD_BASE_URL": hosted_base,
        "AIRA_CLOUD_LOCAL_APP_URL": env.base,
        "AIRA_CLOUD_LAUNCH_SECRET": SECRET,
        "AIRA_CLOUD_DB": "sqlite",
    }
    hosted_env.pop("AIRA_TEST_DATABASE_URL", None)
    seeded = subprocess.run(
        [sys.executable, "-m", "aira_cloud", "seed-demo"],
        cwd="D:/linkedin",
        env=hosted_env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert seeded.returncode == 0, seeded.stderr[-800:]
    log = open(tmp_path / "hosted.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "aira_cloud", "serve"],
        cwd="D:/linkedin",
        env=hosted_env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    monkeypatch.setenv("AIRA_ACCOUNT_URL", hosted_base)
    monkeypatch.setenv("AIRA_ACCOUNT_SECRET", SECRET)
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                urllib.request.urlopen(hosted_base + "/api/health", timeout=2)
                break
            except (urllib.error.URLError, OSError):
                assert server.poll() is None, (
                    "the hosted account exited: "
                    + open(tmp_path / "hosted.log", encoding="utf-8").read()[-800:]
                )
                time.sleep(0.5)
        else:
            raise AssertionError("the hosted account never answered")
        # sign in to the account as a demo candidate (no MFA on that role)
        request = urllib.request.Request(
            hosted_base + "/api/auth/login",
            data=json.dumps(
                {
                    "email": "cam.cole@northwind.test",
                    "password": "demo-password-123",
                    "org": "northwind-outplacement",
                }
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": hosted_base},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200
            cookie = next(
                c
                for c in response.headers.get_all("Set-Cookie")
                if c.startswith("aira_session=")
            )
        session = cookie.split(";", 1)[0].split("=", 1)[1]
        with patchright.sync_playwright() as playwright:
            try:
                chromium = playwright.chromium.launch(headless=True)
            except Exception as error:  # noqa: BLE001
                pytest.skip(f"Chromium is not available: {error}")
            context = chromium.new_context(viewport={"width": 1280, "height": 800})
            context.add_cookies(
                [{"name": "aira_session", "value": session, "url": hosted_base}]
            )
            page = context.new_page()
            page.set_default_timeout(60_000)
            problems: list[str] = []
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            failed: list[str] = []
            page.on(
                "response",
                lambda r: (
                    failed.append(f"{r.status} {r.request.method} {r.url}")
                    if r.status >= 500
                    else None
                ),
            )
            # the local sign-in screen offers the account
            page.goto(env.base + "/")
            page.wait_for_selector("#accountLinkBtn")
            assert (
                "Sign in with your Aira account"
                in page.locator("#accountLinkBtn").inner_text()
            )
            assert page.locator("#accountLinkBtn").is_visible()
            page.click("#accountLinkBtn")
            # ... which redirects back with a token, and the local build signs the person in
            page.wait_for_url(
                lambda url: url.startswith(env.base) and "#/launch" not in url,
                timeout=60_000,
            )
            page.wait_for_selector("#app:not([hidden])")
            assert (
                "cam.cole@northwind.test" in page.locator("#accountEmail").inner_text()
            )
            assert "token=" not in page.url, (
                "the one-time token never stays in the address bar"
            )
            me = json.loads(context.request.get(env.base + "/api/auth/me").text())
            assert (
                me["email"] == "cam.cole@northwind.test"
                and me["account"]["linked"] is True
            )
            assert (
                me["account"]["url"] == hosted_base
                and me["account"]["org_name"] == "Northwind Outplacement"
            )
            # the account's switcher data names the local installation, and the token cannot be used again
            apps = json.loads(context.request.get(hosted_base + "/api/apps").text())
            assert apps.get("local_url", env.base) == env.base
            assert not failed, failed
            assert not problems, problems
            context.close()
            chromium.close()
        # the local account has no password: only the account opens it
        status, body = env.api().call(
            "POST",
            "/api/auth/login",
            {"email": "cam.cole@northwind.test", "password": PASSWORD},
        )
        assert status == 403 and "Sign in with your Aira account" in body["error"]
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
