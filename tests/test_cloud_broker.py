"""The sign-in broker with a real Chromium on both ends: a stub login site, the app over HTTP, the broker in
this process, and a second browser acting as the person's screen (clicking and typing through the canvas)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest
from cloud_support import build_org, make_app
from patchright.sync_api import sync_playwright
from websockets.sync.client import connect

from aira.hosting import HOOKS
from aira_cloud.gateway import Gateway, make_server
from aira_cloud.streaming import Broker, clamp_size, host_allowed, navigation_hosts

SECRET = "b" * 40
BROKER_SECRET = "k" * 40
COOKIE_VALUE = "AQEDARstubSessionValue123456789"
PASSWORD = "correct horse battery staple"

LOGIN_PAGE = """<!doctype html><meta name=viewport content="width=device-width">
<style>body{margin:0;font:4vh sans-serif;background:#fff;color:#111}
input,button,a{position:absolute;left:10vw;width:80vw;height:12vh;font-size:5vh;box-sizing:border-box;display:block}
#u{top:10vh}#p{top:30vh}#go{top:50vh}#out{top:75vh;height:10vh}</style>
<form method=post action=/auth><input id=u name=user autocomplete=off><input id=p name=pass type=password>
<button id=go>Sign in</button></form><a id=out href="http://localhost:__PORT__/forbidden">Other site</a>"""


@dataclass
class Stub:
    """The site standing in for linkedin.com/login."""

    requests: list[tuple[str, str, str, str]] = field(default_factory=list)
    server: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.server_address[1]

    @property
    def login_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/login"

    def hosts_seen(self) -> set[str]:
        return {host.rsplit(":", 1)[0] for _m, host, _p, _b in self.requests}

    def credentials(self) -> list[dict[str, list[str]]]:
        return [
            parse_qs(body)
            for method, _h, path, body in self.requests
            if path == "/auth"
        ]


def start_stub() -> Stub:
    stub = Stub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def _body(
            self, status: int, text: str, headers: dict[str, str] | None = None
        ) -> None:
            data = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            stub.requests.append(("GET", self.headers.get("Host", ""), self.path, ""))
            if self.path.startswith("/login"):
                self._body(200, LOGIN_PAGE.replace("__PORT__", str(stub.port)))
            elif self.path.startswith("/feed"):
                self._body(200, "<h1>Signed in</h1>")
            else:
                self._body(200, "<h1>Forbidden place</h1>")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode()
            stub.requests.append(
                ("POST", self.headers.get("Host", ""), self.path, body)
            )
            self._body(
                302,
                "",
                {
                    "Location": "/feed",
                    "Set-Cookie": f"li_at={COOKIE_VALUE}; Path=/; Max-Age=3600; HttpOnly",
                },
            )

    stub.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=stub.server.serve_forever, daemon=True).start()
    return stub


@dataclass
class Stack:
    app: Any
    org: Any
    stub: Stub
    broker: Broker
    port: int
    app_url: str
    extra: list[Any] = field(default_factory=list)

    def link(self, token: str) -> str:
        return f"http://127.0.0.1:{self.port}/s/{token}"

    def issue(self, who: str = "candidate") -> tuple[str, str]:
        candidate = getattr(self.org, who).member_id
        return self.app.linkedin.create_remote_login(
            self.org.org_id, candidate
        ), candidate

    def profile_dir(self, candidate_id: str) -> Path:
        return (
            self.app.workspaces.candidate_dir(self.org.org_id, candidate_id)
            / "linkedin"
            / "profile"
        )

    def audit(self, candidate_id: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.app.audit.list(self.org.org_id, candidate_id=candidate_id)
            if r["action"].startswith("auth.remote_login")
        ]


@pytest.fixture
def make_stack(tmp_path):
    HOOKS.reset()
    made: list[Any] = []

    def build(**broker_overrides: Any) -> Stack:
        stub = start_stub()
        app = make_app(
            tmp_path / f"stack{len(made)}",
            broker_secret=BROKER_SECRET,
            broker_start_url=stub.login_url,
            broker_signed_in_domain="127.0.0.1",
            **broker_overrides,
        )
        org = build_org(app, plan="team")
        server = make_server(Gateway(app), "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        app_url = f"http://127.0.0.1:{server.server_address[1]}"
        broker = Broker(app.config, clock=app.clock.now, app_url=app_url)
        broker.poll_interval = 0.3
        broker.heartbeat_interval = 0.6
        broker.finalize_delay = 0.8
        port = broker.start_in_thread()
        stack = Stack(app, org, stub, broker, port, app_url)
        made.append((stack, server))
        return stack

    yield build
    for stack, server in made:
        stack.broker.stop_thread()
        server.shutdown()
        server.server_close()
        assert stack.stub.server is not None
        stack.stub.server.shutdown()
        stack.app.close()


def wait_for(predicate, timeout: float = 40.0, step: float = 0.2) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out waiting for the condition")


def wait_for_frames(viewer, timeout: float = 60.0) -> None:
    """The viewer page's CSP forbids eval, so poll from the test instead of using wait_for_function."""
    wait_for(
        lambda: int(viewer.get_attribute("#screen", "data-frames") or 0) > 0, timeout
    )


def ws_session(stack: Stack, token: str, width: int = 800, height: int = 600):
    """A bare WebSocket viewer: returns (connection, collected messages)."""
    connection = connect(f"ws://127.0.0.1:{stack.port}/ws/{token}", open_timeout=10)
    connection.send(json.dumps({"t": "hello", "w": width, "h": height}))
    return connection


def next_status(connection, timeout: float = 30.0) -> dict[str, Any]:
    """The next JSON message (frames are skipped)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            message = connection.recv(timeout=max(0.1, deadline - time.time()))
        except TimeoutError:
            break
        if isinstance(message, str):
            return json.loads(message)
    raise AssertionError("no status message arrived")


def first_frame(connection, timeout: float = 40.0) -> bytes:
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = connection.recv(timeout=max(0.1, deadline - time.time()))
        if isinstance(message, bytes):
            return message
        data = json.loads(message)
        assert data.get("type") != "error", data
    raise AssertionError("no frame arrived")


# ----------------------------------------------------------------------------- the whole sign-in, as a person does it


def sign_in_through_the_canvas(
    viewer, stack: Stack, token: str, *, touch: bool = False
) -> None:
    viewer.goto(stack.link(token))
    wait_for_frames(viewer)
    canvas = viewer.locator("#screen")

    def at(fx: float, fy: float) -> tuple[float, float]:
        box = canvas.bounding_box()
        assert box is not None
        return box["x"] + fx * box["width"], box["y"] + fy * box["height"]

    def press(fx: float, fy: float) -> None:
        x, y = at(fx, fy)
        if touch:
            viewer.touchscreen.tap(x, y)
        else:
            viewer.mouse.click(x, y)

    press(0.5, 0.16)  # the username field
    if touch:
        wait_for(
            lambda: (
                viewer.evaluate("document.activeElement && document.activeElement.id")
                == "kb"
            ),
            15,
        )
    viewer.keyboard.type("ann@example.test", delay=20)
    press(0.5, 0.36)  # the password field
    viewer.keyboard.type(PASSWORD, delay=20)
    if touch:
        press(0.5, 0.56)  # the Sign in button
    else:
        viewer.keyboard.press("Enter")
    viewer.wait_for_selector("#done.show", timeout=60000)


def profile_cookies(profile: Path) -> list[dict[str, Any]]:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=True
        )
        try:
            return context.cookies()
        finally:
            context.close()


def test_a_person_signs_in_through_the_stream_and_the_profile_holds_the_session(
    make_stack, caplog
):
    import logging

    caplog.set_level(logging.DEBUG)
    stack = make_stack()
    token, candidate = stack.issue()
    seen_frames: list[Any] = []
    set_cookie_headers: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        viewer = browser.new_context(viewport={"width": 900, "height": 700}).new_page()
        viewer.on(
            "websocket",
            lambda ws: ws.on(
                "framereceived", lambda payload: seen_frames.append(payload)
            ),
        )
        viewer.on(
            "response",
            lambda response: set_cookie_headers.extend(
                value
                for name, value in response.headers.items()
                if name.lower() == "set-cookie"
            ),
        )
        sign_in_through_the_canvas(viewer, stack, token)
        assert "signed in" in viewer.locator("#done").inner_text().lower()
        assert viewer.evaluate("document.cookie") == ""
        browser.close()

    # what was typed reached the site, through the broker
    assert stack.stub.credentials()[0]["user"] == ["ann@example.test"]
    assert stack.stub.credentials()[0]["pass"] == [PASSWORD]
    # ... and was not logged anywhere
    assert PASSWORD not in caplog.text and "ann@example.test" not in caplog.text

    # the profile on this host now holds the session, in the form the worker expects
    cookies = {
        c["name"]: c["value"] for c in profile_cookies(stack.profile_dir(candidate))
    }
    assert cookies.get("li_at") == COOKIE_VALUE
    import auto_apply_assistant as local

    assert local._profile_is_ready(stack.profile_dir(candidate))
    exported = json.loads(
        (stack.profile_dir(candidate).parent / "cookies.json").read_text(
            encoding="utf-8"
        )
    )
    assert any(c["name"] == "li_at" for c in exported)

    # the person's screen only ever received pixels and short status messages
    assert seen_frames, "frames were streamed"
    for payload in seen_frames:
        raw = payload if isinstance(payload, bytes) else payload.encode()
        assert COOKIE_VALUE.encode() not in raw and b"li_at" not in raw
        if isinstance(payload, str):
            assert json.loads(payload)["type"] in {
                "focus",
                "ended",
                "blocked",
                "notice",
                "error",
            }
    assert not set_cookie_headers, "the broker never sets a cookie"

    # start and end are in the organization's audit log
    actions = {r["action"]: r for r in stack.audit(candidate)}
    assert set(actions) == {"auth.remote_login_started", "auth.remote_login_finished"}
    assert _outcome(actions["auth.remote_login_finished"]) == "signed_in"

    # the link is single use
    again = ws_session(stack, token)
    message = next_status(again)
    assert message["type"] == "error" and message["code"] == "link_used"
    again.close()


def test_a_phone_signs_in_by_tapping_and_the_keyboard_follows_the_focus(make_stack):
    stack = make_stack()
    token, candidate = stack.issue()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 780}, has_touch=True, is_mobile=True
        )
        viewer = context.new_page()
        sign_in_through_the_canvas(viewer, stack, token, touch=True)
        assert "signed in" in viewer.locator("#done").inner_text().lower()
        browser.close()
    assert stack.stub.credentials()[0]["pass"] == [PASSWORD]
    assert {c["name"] for c in profile_cookies(stack.profile_dir(candidate))} >= {
        "li_at"
    }


def test_navigation_away_from_the_allowed_hosts_is_blocked_and_leaving_ends_the_session(
    make_stack,
):
    stack = make_stack()
    token, candidate = stack.issue()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        viewer = browser.new_context(viewport={"width": 900, "height": 700}).new_page()
        viewer.goto(stack.link(token))
        wait_for_frames(viewer)
        box = viewer.locator("#screen").bounding_box()
        assert box is not None
        # the link to http://localhost:... is another host than the allowed 127.0.0.1
        viewer.mouse.click(
            box["x"] + 0.5 * box["width"], box["y"] + 0.8 * box["height"]
        )
        wait_for(lambda: "not allowed" in viewer.locator("#state").inner_text(), 20)
        time.sleep(1.0)
        assert "localhost" not in stack.stub.hosts_seen()
        assert not any(path == "/forbidden" for _m, _h, path, _b in stack.stub.requests)
        # still on the sign-in page: the login form works afterwards
        viewer.mouse.click(
            box["x"] + 0.5 * box["width"], box["y"] + 0.16 * box["height"]
        )
        viewer.keyboard.type("still-here")
        viewer.mouse.click(
            box["x"] + 0.5 * box["width"], box["y"] + 0.56 * box["height"]
        )
        wait_for(lambda: stack.stub.credentials())
        assert stack.stub.credentials()[0]["user"] == ["still-here"]
        browser.close()  # the person closes the window
    wait_for(lambda: len(stack.audit(candidate)) == 2)


def test_only_http_to_allowed_hosts_may_be_navigated_to(tmp_path):
    from aira_cloud.config import CloudConfig

    config = CloudConfig(
        data_root=tmp_path, broker_allowed_hosts=("accounts.example.test",)
    )
    hosts = navigation_hosts(config)
    assert "linkedin.com" in hosts and "accounts.example.test" in hosts
    for url in (
        "https://www.linkedin.com/login",
        "https://linkedin.com/checkpoint/challenge",
        "https://accounts.example.test/o/oauth2",
        "about:blank",
    ):
        assert host_allowed(url, hosts), url
    for url in (
        "file:///etc/passwd",
        "chrome://settings",
        "view-source:https://www.linkedin.com",
        "javascript:alert(1)",
        "data:text/html,hi",
        "https://evil-linkedin.com/login",
        "https://linkedin.com.evil.test/login",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data",
        "ftp://www.linkedin.com/",
    ):
        assert not host_allowed(url, hosts), url
    assert clamp_size(10, 10) == (320, 360) and clamp_size(99999, 99999) == (1600, 1400)
    assert clamp_size("x", None) == (1000, 700)


def test_unknown_expired_used_and_badly_signed_links_are_refused(make_stack):
    stack = make_stack()
    unknown = ws_session(stack, "x" * 32)
    message = next_status(unknown)
    assert message["type"] == "error" and message["code"] == "invalid_link"
    unknown.close()

    expired, _candidate = stack.issue()
    stack.app.clock.advance(seconds=stack.app.config.remote_login_ttl + 1)
    late = ws_session(stack, expired)
    message = next_status(late)
    assert message["code"] == "link_used" and "expired" in message["message"]
    late.close()

    used, _ = stack.issue()
    stack.app.linkedin.validate(used)  # somebody else already used it
    twice = ws_session(stack, used)
    assert next_status(twice)["code"] == "link_used"
    twice.close()

    fresh, _ = stack.issue()
    stack.broker.app.secret = "wrong-secret-" + "z" * 30
    forged = ws_session(stack, fresh)
    message = next_status(forged)
    assert message["code"] == "misconfigured"
    forged.close()
    stack.broker.app.secret = BROKER_SECRET
    # nothing was audited for refusals: the sign-in never started
    assert stack.audit(stack.org.candidate.member_id) == []


def test_pages_other_than_the_viewer_are_not_served_and_the_viewer_is_locked_down(
    make_stack,
):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    stack = make_stack()
    token, _ = stack.issue()
    with urlopen(stack.link(token)) as response:  # noqa: S310 - the local broker
        html = response.read().decode()
        headers = {k.lower(): v for k, v in response.headers.items()}
    assert "<canvas" in html and "https://" not in html.replace(
        "https://www.linkedin", ""
    )
    assert "set-cookie" not in headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert (
        headers["referrer-policy"] == "no-referrer"
        and headers["cache-control"] == "no-store"
    )
    # looking at the page does not use the link up
    assert stack.app.linkedin.validate(token)["candidate_id"]
    for path in ("/", "/s/short", "/ws/no", "/api/anything", "/s/../etc/passwd"):
        with pytest.raises(HTTPError) as caught:
            urlopen(f"http://127.0.0.1:{stack.port}{path}")  # noqa: S310
        assert caught.value.code in {404, 426}, path
    with urlopen(f"http://127.0.0.1:{stack.port}/healthz") as response:  # noqa: S310
        assert response.read() == b"ok"
    # a WebSocket from another website's page is refused
    request = Request(
        f"http://127.0.0.1:{stack.port}/ws/{'a' * 32}",
        headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            "Origin": "https://evil.example.test",
        },
    )
    with pytest.raises(HTTPError) as caught:
        urlopen(request)  # noqa: S310
    assert caught.value.code == 403


def test_one_session_per_candidate_and_the_host_cap_is_enforced(make_stack):
    stack = make_stack(broker_max_sessions=1)
    first, candidate = stack.issue("candidate")
    one = ws_session(stack, first)
    first_frame(one)
    assert len(stack.broker.sessions) == 1

    second, _ = stack.issue("other")  # a different candidate, but the host is full
    refused = ws_session(stack, second)
    message = next_status(refused)
    assert message["type"] == "error" and message["code"] == "busy"
    refused.close()
    # refused before the link was used: it still works when there is room
    from aira_cloud.db import one as one_row

    with stack.app.db.read() as connection:
        row = one_row(
            connection,
            "SELECT state FROM remote_login_sessions WHERE token_hash = ?",
            (__import__("aira_cloud.crypto", fromlist=["x"]).hash_token(second),),
        )
    assert row["state"] == "pending"
    one.close()
    wait_for(lambda: not stack.broker.sessions)
    stack.broker.config.broker_max_sessions = 2
    stack.broker.heartbeat_interval = (
        600  # leave revocation out of it: this is about replacement
    )

    # the same candidate signing in again replaces the older window (and only one browser holds the profile)
    a, _ = stack.issue("candidate")
    older = ws_session(stack, a)
    first_frame(older)
    b, _ = stack.issue("candidate")
    newer = ws_session(stack, b)
    state_a = next_status(older, timeout=30)
    assert state_a["type"] == "ended" and state_a["outcome"] == "replaced"
    older.close()
    first_frame(newer)
    assert len(stack.broker.sessions) == 1
    newer.close()
    wait_for(lambda: not stack.broker.sessions)
    stack.broker.heartbeat_interval = 0.6
    finished = [
        r for r in stack.audit(candidate) if r["action"] == "auth.remote_login_finished"
    ]
    outcomes = sorted(_outcome(r) for r in finished)
    assert outcomes.count("replaced") == 1 and "abandoned" in outcomes


def test_a_newer_link_revokes_a_session_in_progress(make_stack):
    stack = make_stack()
    token, candidate = stack.issue()
    live = ws_session(stack, token)
    first_frame(live)
    stack.issue()  # the candidate clicked Connect again
    message = next_status(live, timeout=20)
    assert message["type"] == "ended" and message["outcome"] == "revoked"
    live.close()
    wait_for(lambda: not stack.broker.sessions)
    assert any(_outcome(r) == "revoked" for r in stack.audit(candidate))


def _outcome(row: dict[str, Any]) -> str:
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail or "{}")
    return str(detail.get("outcome", ""))


def test_a_session_that_outlives_its_time_limit_ends_and_is_audited(make_stack):
    stack = make_stack(broker_session_seconds=3)
    token, candidate = stack.issue()
    live = ws_session(stack, token)
    first_frame(live)
    message = next_status(live, timeout=20)
    assert message["type"] == "ended" and message["outcome"] == "timeout"
    live.close()
    wait_for(lambda: len(stack.audit(candidate)) == 2)
    assert _outcome(stack.audit(candidate)[-1]) == "timeout" or any(
        _outcome(r) == "timeout" for r in stack.audit(candidate)
    )


def test_cancel_and_suspension_end_a_session(make_stack):
    stack = make_stack()
    token, candidate = stack.issue()
    live = ws_session(stack, token)
    first_frame(live)
    live.send(json.dumps({"t": "cancel"}))
    message = next_status(live, timeout=20)
    assert message["outcome"] == "cancelled"
    live.close()
    wait_for(lambda: not stack.broker.sessions)

    other, other_candidate = stack.issue("other")
    live = ws_session(stack, other)
    first_frame(live)
    with stack.app.db.write() as connection:
        connection.execute(
            "UPDATE organizations SET status = 'suspended' WHERE org_id = ?",
            (stack.org.org_id,),
        )
    message = next_status(live, timeout=20)
    assert message["outcome"] == "revoked"
    live.close()
    wait_for(lambda: not stack.broker.sessions)
    assert any(_outcome(r) == "cancelled" for r in stack.audit(candidate))
    assert any(_outcome(r) == "revoked" for r in stack.audit(other_candidate))


def test_the_event_route_needs_the_agent_signature_and_cannot_be_used_for_other_tokens(
    make_stack,
):
    from cloud_support import Browser

    stack = make_stack()
    token, _candidate = stack.issue()
    browser = Browser(stack.app)
    status, body = browser.call(
        "POST",
        "/api/internal/remote-login/event",
        {"token": token, "event": "started"},
    )
    assert status == 401 and body["code"] == "bad_signature"
    # an unvalidated token cannot be reported on, and an unknown one is not found
    with pytest.raises(Exception) as caught:
        stack.broker.app.event(token, "started")
    assert getattr(caught.value, "code", "") == "link_used"
    with pytest.raises(Exception) as caught:
        stack.broker.app.event("n" * 32, "heartbeat")
    assert getattr(caught.value, "code", "") == "invalid_link"
