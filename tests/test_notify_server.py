"""Notifications on the single-user server: the installable app's files at the root, the notification centre and
Web Push routes, the live stream (heartbeat, Last-Event-ID, the cap), and the places that publish to it (a chat
message across households, a trivia room) plus the minute-by-minute reminder tick."""

# ruff: noqa: F811
from __future__ import annotations

import http.client
import json
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

import pytest
from life_support import Household
from notify_support import Browser, PushStub

import auto_apply_assistant as app
import test_careers
from aira import notify
from aira.notify import dispatch, realtime, store
from aira.suite import kit, public
from aira.suite.social import db as social_db
from test_careers import env, stub  # noqa: F401

api = test_careers.api


@pytest.fixture(autouse=True)
def live(monkeypatch):
    kit.forget_schema()
    realtime.HUB.reset()
    dispatch.DISPATCHER.clear()
    monkeypatch.setattr(realtime, "HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(notify, "_live", True)
    yield
    realtime.HUB.shutdown()  # every open stream ends, so the server can stop
    notify.flush(5)
    dispatch.DISPATCHER.clear()


@pytest.fixture
def push():
    stub_ = PushStub().start()
    yield stub_
    stub_.stop()


def cookie_of(client: Any) -> str:
    jar = next(
        handler.cookiejar
        for handler in client.opener.handlers
        if isinstance(handler, urllib.request.HTTPCookieProcessor)
    )
    return "; ".join(f"{c.name}={c.value}" for c in jar)


class Stream:
    """An EventSource, as far as a test needs one: reads ``id:`` and ``data:`` blocks."""

    def __init__(
        self, env: Any, client: Any, *, last: str = "", query: str = ""
    ) -> None:
        parts = urlsplit(env.base)
        self.connection = http.client.HTTPConnection(
            parts.hostname, parts.port, timeout=10
        )
        headers = {"Accept": "text/event-stream"}
        if client is not None:
            headers["Cookie"] = cookie_of(client)
        if last:
            headers["Last-Event-ID"] = last
        self.connection.request("GET", f"/api/notify/stream{query}", headers=headers)
        self.response = self.connection.getresponse()
        self.status = self.response.status

    def next(self, timeout: float = 10.0) -> dict[str, Any]:
        """The next event (heartbeats and the retry line are skipped)."""
        end = time.monotonic() + timeout
        block: dict[str, str] = {}
        while time.monotonic() < end:
            line = self.response.fp.readline().decode("utf-8")
            if not line:
                raise AssertionError("the stream ended")
            line = line.rstrip("\n")
            if not line:
                if "data" in block:
                    return {
                        **json.loads(block["data"]),
                        "event_id": block.get("id", ""),
                    }
                block = {}
                continue
            if line.startswith(":"):
                block["ping"] = "1"
                continue
            name, _, value = line.partition(": ")
            block[name] = value
        raise AssertionError("no event in time")

    def pings(self, count: int = 1) -> int:
        seen = 0
        while seen < count:
            line = self.response.fp.readline().decode("utf-8")
            assert line, "the stream ended"
            if line.startswith(": ping"):
                seen += 1
        return seen

    def close(self) -> None:
        self.response.close()  # the response holds the socket open until it is closed too
        self.connection.close()


def channel_of(env: Any, email: str = "careers@example.com") -> str:
    import sqlite3

    with sqlite3.connect(env.tmp_path / "accounts.sqlite3") as connection:
        user_id = connection.execute(
            "SELECT user_id FROM users WHERE email = ?", (email,)
        ).fetchone()[0]
    return f"local:{user_id}"


def test_the_installable_app_is_served_from_the_root(env, api):
    with urllib.request.urlopen(env.base + "/sw.js", timeout=30) as reply:
        assert reply.status == 200 and "javascript" in reply.headers["Content-Type"]
        assert reply.headers["Cache-Control"] == "no-cache" and reply.headers["ETag"]
        assert "Content-Security-Policy" in reply.headers
        body = reply.read().decode()
        etag = reply.headers["ETag"]
    assert (
        'addEventListener("push"' in body
        and 'addEventListener("notificationclick"' in body
    )
    again = urllib.request.Request(env.base + "/sw.js", headers={"If-None-Match": etag})
    with pytest.raises(urllib.error.HTTPError) as revalidated:
        urllib.request.urlopen(again, timeout=30)
    assert revalidated.value.code == 304
    with urllib.request.urlopen(
        env.base + "/manifest.webmanifest", timeout=30
    ) as reply:
        assert reply.headers["Content-Type"].startswith("application/manifest+json")
        manifest = json.loads(reply.read())
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"
    assert {icon["purpose"] for icon in manifest["icons"]} == {"any", "maskable"}
    for icon in manifest["icons"]:
        with urllib.request.urlopen(env.base + icon["src"], timeout=30) as reply:
            assert (
                reply.headers["Content-Type"] == "image/png"
                and reply.read()[:8] == b"\x89PNG\r\n\x1a\n"
            )
    index = (app.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in index
    assert (
        '<script src="/static/realtime.js"></script>' in index
        and '<script src="/static/notify.js"></script>' in index
    )


def test_the_notification_centre_and_push_devices_through_the_routes(env, api, push):
    me = Household(env, api).owner
    settings = me.get("/api/notify/settings")
    assert len(notify.webpush.unb64url(settings["push"]["key"])) == 65
    assert settings["live"] is True and settings["push"]["devices"] == []
    assert [c["id"] for c in settings["categories"]] == [
        "careers",
        "medicine",
        "bookings",
        "helpdesk",
        "stocks",
        "messages",
    ]
    assert (
        env.tmp_path / notify.webpush.KEY_FILE
    ).is_file()  # kept in the data directory
    status, refused = me.call(
        "POST",
        "/api/notify/push/subscribe",
        {"endpoint": "http://10.0.0.5/x", "keys": {"p256dh": "a", "auth": "b"}},
    )
    assert status == 400 and "push service" in refused["error"]
    status, _ = me.call("POST", "/api/notify/push/test", {})
    assert status == 400  # no browser yet
    browser = Browser.new(push)
    saved = me.post(
        "/api/notify/push/subscribe",
        browser.subscription("Firefox on Linux"),
        expect=201,
    )
    assert [d["device"] for d in saved["push"]["devices"]] == ["Firefox on Linux"]
    assert me.post("/api/notify/push/test", {})["queued"] == 1
    notify.flush()
    assert [m["title"] for m in browser.received(push)] == ["Monosage"]
    saved = me.ok(
        "PUT",
        "/api/notify/settings",
        {
            "categories": {"stocks": {"push": False}},
            "quiet": {"on": True, "start": "21:00", "end": "06:30"},
        },
    )
    assert (
        saved["prefs"]["categories"]["stocks"]["push"] is False
        and saved["prefs"]["quiet"]["start"] == "21:00"
    )
    status, _ = me.call("PUT", "/api/notify/settings", {"timezone": "Nowhere/Land"})
    assert status == 400
    with env.user_db() as connection:
        notify.notify(
            connection,
            {"user_id": "x"},
            "careers",
            "Follow-up due",
            "Something is due.",
            "/#/dashboard",
        )
    listed = me.get("/api/notify/items")
    assert listed["unread"] == 1 and listed["items"][0]["title"] == "Follow-up due"
    assert (
        me.get("/api/notify/items?unread=1")["items"][0]["id"]
        == listed["items"][0]["id"]
    )
    assert me.post("/api/notify/read", {})["unread"] == 0
    assert (
        me.post("/api/notify/push/unsubscribe", {"endpoint": browser.endpoint})["push"][
            "devices"
        ]
        == []
    )


def test_the_live_stream_needs_a_session_resumes_and_is_capped(env, api):
    channel = channel_of(env)
    status, _ = Stream(env, None).status, None
    assert status == 401
    first = Stream(env, api)
    assert first.status == 200 and first.response.headers["Content-Type"].startswith(
        "text/event-stream"
    )
    assert first.pings() == 1  # the heartbeat keeps the connection open
    a = notify.publish(channel, "chat", {"chat": "c1"}, person="p:abc")
    got = first.next()
    assert got == {"event_id": a, "topic": "chat", "person": "p:abc", "chat": "c1"}
    first.close()
    b = notify.publish(channel, "trivia", {"room": "r1"})
    c = notify.publish(channel, "notify", {"id": "n1"})
    notify.publish("local:someone-else", "chat", {"chat": "theirs"})
    resumed = Stream(
        env, api, last=a
    )  # what the browser sends after a dropped connection
    assert [resumed.next()["event_id"], resumed.next()["event_id"]] == [b, c]
    resumed.close()
    by_query = Stream(env, api, query=f"?last={b}")  # a new tab taking over the stream
    assert by_query.next()["event_id"] == c
    by_query.close()
    stale = Stream(env, api, last="0000-1")  # from before a restart
    assert stale.next()["topic"] == "resync"
    stale.close()
    deadline = time.monotonic() + 10
    while realtime.HUB.stats()["streams"] and time.monotonic() < deadline:
        time.sleep(0.05)  # a closed stream is noticed at its next heartbeat
    assert realtime.HUB.stats()["streams"] == 0
    open_streams = [Stream(env, api) for _ in range(realtime.PER_CHANNEL)]
    assert all(s.status == 200 for s in open_streams)
    extra = Stream(
        env, api
    )  # one more page: it takes the oldest page's slot instead of being refused
    assert extra.status == 200
    deadline = time.monotonic() + 5
    while (
        realtime.HUB.stats()["streams"] > realtime.PER_CHANNEL
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)
    assert realtime.HUB.stats()["streams"] == realtime.PER_CHANNEL
    for s in [*open_streams, extra]:
        s.close()


def test_a_chat_message_reaches_another_household_live_and_in_its_notification_centre(
    env, api
):
    public.set_default_index(app._public_link_index)
    social_db.reset_path()
    social_db.forget()
    pat = Household(env, api).owner
    second = env.api()
    status, _ = second.call(
        "POST",
        "/api/auth/register",
        {"email": "sam@example.com", "password": test_careers.PASSWORD},
    )
    assert status == 201
    sam = Household(env, second).owner
    accounts = {}
    for person, handle in ((pat, "patlive"), (sam, "samlive")):
        assert person.get("/api/social/me")["account"] is None
        accounts[handle] = person.post(
            "/api/social/me", {"handle": handle}, expect=201
        )["account"]
    sam.post(f"/api/social/friends/{accounts['patlive']['id']}", {})
    pat.post(f"/api/social/friends/{accounts['samlive']['id']}", {})
    chat = pat.post(
        "/api/social/chats", {"person_id": accounts["samlive"]["id"]}, expect=201
    )["chat"]
    watching = Stream(env, second)
    pat.post(
        f"/api/social/chats/{chat['id']}/messages",
        {"text": "Lunch on Friday?"},
        expect=201,
    )
    seen = [watching.next(), watching.next()]
    assert {event["topic"] for event in seen} == {"chat", "notify"}
    hint = next(event for event in seen if event["topic"] == "chat")
    assert (
        hint["chat"] == chat["id"]
        and hint["person"] == f"p:{sam.id}"
        and "text" not in hint
    )
    pat.post(
        f"/api/social/chats/{chat['id']}/messages", {"text": "Or Saturday"}, expect=201
    )
    inbox = sam.get("/api/notify/items")
    [item] = inbox["items"]  # one per chat while unread
    assert item["title"].startswith("New message from") and item["count"] == 2
    assert item[
        "url"
    ] == f"/#/social?tab=chats&chat={chat['id']}" and "Lunch" not in json.dumps(item)
    assert pat.get("/api/notify/items")["items"] == []  # nothing for the sender
    watching.close()
    social_db.reset_path()
    social_db.forget()


def test_a_trivia_move_tells_the_household_at_once(env, api):
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    room = pat.post("/api/play/rooms", {"questions": 3, "seconds": 60}, expect=201)[
        "room"
    ]
    watching = Stream(env, api)
    ana.post(f"/api/play/rooms/{room['id']}/join", {})
    event = watching.next()
    assert (
        event["topic"] == "trivia"
        and event["room"] == room["id"]
        and event["person"] == ""
    )
    watching.close()


def test_the_reminder_tick_reaches_every_account(env, api, monkeypatch):
    Household(env, api)
    with env.user_db() as connection:
        connection.execute(
            "INSERT INTO tasks (task_id, kind, title, due_at, status) VALUES ('t1', 'follow_up', 'Call back Initech', ?, 'open')",
            (kit.iso(kit.utcnow()),),
        )
    monkeypatch.setattr(app, "_notify_last", 0.0)
    app._run_notify_reminders()
    app._run_notify_reminders()  # within the minute: nothing runs again
    with env.user_db() as connection:
        items = store.items(
            connection,
            [
                "owner",
                *[
                    f"p:{r[0]}"
                    for r in connection.execute("SELECT id FROM life_profiles")
                ],
            ],
        )
    assert [i["title"] for i in items] == ["Follow-up due"] and items[0][
        "detail"
    ] == "Call back Initech"
