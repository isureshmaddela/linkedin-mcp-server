"""Notifications in the hosted build: the route policy, the installable app's files through the gateway, the
notification centre for a member, Web Push with the VAPID key in the platform's secrets, email through the
platform mail, and the live stream through the gateway's streamed responses."""

from __future__ import annotations

import json

import pytest
from cloud_support import Browser as Gateway
from cloud_support import build_org, local_module, sign_in
from notify_support import Browser, PushStub

from aira import notify
from aira.notify import dispatch, realtime, webpush
from aira.suite import kit
from monosage_cloud.policy import POLICY


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    kit.forget_schema()
    realtime.HUB.reset()
    dispatch.DISPATCHER.clear()
    monkeypatch.setattr(webpush, "_cached", None)
    yield
    notify.flush(5)
    dispatch.DISPATCHER.clear()


@pytest.fixture
def push():
    stub = PushStub().start()
    yield stub
    stub.stop()


def test_every_notification_route_has_a_hosted_rule():
    local = local_module()
    names = [
        name
        for _m, _p, name, _a in local.ROUTES
        if "notify" in name or name in {"ui_service_worker", "ui_manifest"}
    ]
    assert len(names) == 10
    for name in names:
        assert name in POLICY, name
    assert POLICY["api_notify_stream"].scope == "deny"
    assert POLICY["ui_service_worker"].scope == POLICY["ui_manifest"].scope == "public"
    for name in names:
        rule = POLICY[name]
        if name.startswith("airaapi__notify_routes__"):
            assert rule.scope == "org_api" and rule.permission.startswith("life.")
            assert (
                rule.audit is False or not rule.write
            )  # personal choices stay out of the organization's audit log


def test_the_app_files_and_a_members_notifications_through_the_gateway(cloud, push):
    status, body = Gateway(cloud).call("GET", "/sw.js", raw=True)
    assert status == 200 and b'addEventListener("push"' in body
    status, body = Gateway(cloud).call("GET", "/manifest.webmanifest", raw=True)
    assert status == 200 and json.loads(body)["name"] == "Monosage"
    org = build_org(cloud)
    member = sign_in(cloud, org.candidate)
    status, settings = member.call("GET", "/api/notify/settings")
    assert status == 200, settings
    assert settings["live"] is True  # hosted pages stream through the gateway
    assert (cloud.config.data_root / "secrets" / webpush.KEY_FILE).is_file()
    status, _ = member.call("GET", "/api/notify/stream")
    response = member.last_response
    assert status == 200 and response.stream is not None
    assert member.header("Content-Type").startswith("text/event-stream")
    realtime.publish(org.org_id, "chat", {"chat": "c1"}, person="p:1")
    sent: list[str] = []
    response.stream(sent.append, lambda: any("c1" in text for text in sent))
    assert sent[0].startswith("retry:") and any('"topic":"chat"' in text for text in sent)
    assert realtime.HUB.stats()["streams"] == 0  # the stream gave its slot back
    browser = Browser.new(push)
    status, saved = member.call(
        "POST", "/api/notify/push/subscribe", browser.subscription()
    )
    assert status == 201 and len(saved["push"]["devices"]) == 1
    status, saved = member.call(
        "PUT", "/api/notify/settings", {"categories": {"stocks": {"email": True}}}
    )
    assert status == 200 and saved["prefs"]["categories"]["stocks"]["email"] is True
    # A reminder made in the organization's apps (the suite tick): the member's browser and the platform mail.
    local = local_module()
    with cloud.workspaces.bind_org_api(org.org_id):
        with local._connect() as connection:
            item = notify.notify(
                connection,
                f"m:{org.candidate.member_id}",
                "stocks",
                "Price alert: INFY",
                "Infosys rose above 1,800.",
                "/#/stocks",
            )
    assert item is not None
    notify.flush()
    [payload] = browser.received(push)
    assert payload["title"] == "Price alert: INFY"
    mail = cloud.mail.latest(org.candidate.email, "notification")
    assert (
        mail and mail["subject"] == "Price alert: INFY" and mail["org_id"] == org.org_id
    )
    assert "Infosys rose above 1,800." in mail["body"]
    status, listed = member.call("GET", "/api/notify/items")
    assert (
        status == 200
        and listed["unread"] == 1
        and listed["items"][0]["id"] == item["id"]
    )
    status, read = member.call("POST", "/api/notify/read", {"ids": [item["id"]]})
    assert status == 200 and read["unread"] == 0
    # someone else in the organization sees none of it
    other = sign_in(cloud, org.other)
    status, theirs = other.call("GET", "/api/notify/items")
    assert status == 200 and theirs["items"] == []


def test_the_hosted_server_streams_live_updates_over_a_socket(cloud):
    """The real server (not the in-process gateway): headers first, then each hint as it happens."""
    import socket
    import threading
    import time

    from monosage_cloud.gateway import Gateway as CloudGateway
    from monosage_cloud.gateway import make_server

    org = build_org(cloud)
    member = sign_in(cloud, org.candidate)
    server = make_server(CloudGateway(cloud), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        cookie = "; ".join(f"{k}={v}" for k, v in member.cookies.items())
        with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
            sock.sendall(
                (
                    "GET /api/notify/stream HTTP/1.1\r\nHost: 127.0.0.1:8780\r\n"
                    "Origin: http://127.0.0.1:8780\r\nX-Requested-With: aira\r\n"
                    f"Cookie: {cookie}\r\n\r\n"
                ).encode()
            )
            received = b""
            while b"retry:" not in received:
                received += sock.recv(4096)
            head = received.split(b"\r\n\r\n", 1)[0].decode()
            assert head.startswith("HTTP/1.0 200") or head.startswith("HTTP/1.1 200"), head
            assert "text/event-stream" in head and "Content-Length" not in head
            deadline = time.monotonic() + 5
            while realtime.HUB.stats()["streams"] == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            realtime.publish(org.org_id, "dating", {"match": "m1"}, person="p:2")
            while b'"match":"m1"' not in received:
                chunk = sock.recv(4096)
                assert chunk, "the stream closed before the hint arrived"
                received += chunk
        # A draining server ends its streams at the next hint or heartbeat, and each gives its slot back.
        server.draining.set()
        realtime.publish(org.org_id, "pulse", {})
        deadline = time.monotonic() + 10
        while realtime.HUB.stats()["streams"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert realtime.HUB.stats()["streams"] == 0
    finally:
        server.draining.set()
        server.shutdown()
        server.server_close()
