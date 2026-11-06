"""Notifications (aira/notify): Web Push crypto and delivery, the notification centre, choices and quiet hours,
email, the live hub, and the reminders made on the server. No real push service is ever called: a stub on
127.0.0.1 receives the encrypted messages and the tests decrypt them as a browser would."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from notify_support import Browser, PushStub

from aira import notify
from aira.hosting import HOOKS
from aira.notify import dispatch, realtime, reminders, store, webpush
from aira.suite import kit, life


class Req:
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    kit.forget_schema()
    realtime.HUB.reset()
    dispatch.DISPATCHER.clear()
    monkeypatch.setattr(webpush, "_key_dir", lambda: tmp_path / "keys")
    monkeypatch.setattr(webpush, "_cached", None)
    monkeypatch.setattr(notify, "_home", lambda: "local:test")
    monkeypatch.setattr(notify, "_mailer", None)
    monkeypatch.setattr(notify, "_live", True)
    HOOKS.notify = None
    yield
    notify.flush(5)
    dispatch.DISPATCHER.clear()
    HOOKS.notify = None


@pytest.fixture
def push():
    stub = PushStub().start()
    yield stub
    stub.stop()


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "home.sqlite3")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    rosa = life.add(
        db, pat, {"name": "Rosa", "age_group": "senior", "email": "rosa@home.example"}
    )
    return {"pat": pat, "rosa": rosa}


def stream_events(channel: str = "local:test") -> list[realtime.Event]:
    return realtime.HUB.wait(channel, 0, 0.01)


# ---------------------------------------------------------------------------
# Web Push: RFC 8291 encryption, RFC 8292 tokens, which endpoints are posted to
# ---------------------------------------------------------------------------


def test_the_message_encryption_matches_the_rfc_8291_example():
    def key(text):
        return ec.derive_private_key(
            int.from_bytes(webpush.unb64url(text), "big"), ec.SECP256R1()
        )

    sender = key("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")
    receiver = key("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94")
    p256dh = webpush.b64url(webpush._point(receiver.public_key()))
    assert (
        p256dh
        == "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    )
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        p256dh,
        "BTBZMqHH6r4Tts7J_aSIgg",
        salt=webpush.unb64url("DGv6ra1nlYgDCS1FRnbzlw"),
        sender_key=sender,
    )
    assert webpush.b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl"
        "7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )
    assert (
        webpush.decrypt(body, receiver, "BTBZMqHH6r4Tts7J_aSIgg")
        == b"When I grow up, I want to be a watermelon"
    )


def test_every_message_has_its_own_keys_and_refuses_bad_subscriptions(push):
    browser = Browser.new(push)
    first = webpush.encrypt(b"hello", browser.p256dh, browser.auth)
    second = webpush.encrypt(b"hello", browser.p256dh, browser.auth)
    assert (
        first != second and first[:16] != second[:16]
    )  # a new salt and sender key each time
    assert webpush.decrypt(first, browser.key, browser.auth) == b"hello"
    with pytest.raises(ValueError):
        webpush.encrypt(b"x" * 3001, browser.p256dh, browser.auth)
    with pytest.raises(ValueError):
        webpush.encrypt(b"hi", webpush.b64url(b"\x04" + b"1" * 10), browser.auth)


def test_the_vapid_key_is_made_once_and_its_token_verifies(tmp_path):
    public = webpush.public_key()
    path = tmp_path / "keys" / webpush.KEY_FILE
    assert path.is_file() and b"PRIVATE KEY" in path.read_bytes()
    stamp = path.stat().st_mtime_ns
    webpush._cached = None
    assert (
        webpush.public_key() == public and path.stat().st_mtime_ns == stamp
    )  # kept, not remade
    assert len(webpush.unb64url(public)) == 65
    header = webpush.vapid_header(
        "https://fcm.googleapis.com/fcm/send/abc", now=1_000_000
    )
    token, key = header.removeprefix("vapid t=").split(", k=")
    assert key == public
    head, claims, signature = token.split(".")
    assert json.loads(webpush.unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    body = json.loads(webpush.unb64url(claims))
    assert body == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1_000_000 + 12 * 3600,
        "sub": "mailto:notifications@monosage.invalid",
    }
    raw = webpush.unb64url(signature)
    der = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), webpush.unb64url(public)
    )
    verifier.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        verifier.verify(der, f"{head}.{claims}x".encode(), ec.ECDSA(hashes.SHA256()))


def test_only_browser_push_services_are_posted_to(push):
    allowed = webpush.endpoint_allowed
    assert allowed("https://fcm.googleapis.com/fcm/send/abc")
    assert allowed("https://updates.push.services.mozilla.com/wpush/v2/abc")
    assert allowed("https://wns2-par02p.notify.windows.com/w/?token=abc")
    assert allowed("https://web.push.apple.com/abc")
    for refused in (
        "http://fcm.googleapis.com/fcm/send/abc",  # not https
        "https://fcm.googleapis.com:8443/x",
        "https://evil.example/fcm.googleapis.com",
        "https://fcm.googleapis.com.evil.example/x",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pw@fcm.googleapis.com/x",
        "http://127.0.0.1:9/x",  # a local address that is not the test's stub
        "",
    ):
        assert not allowed(refused), refused
    assert allowed(f"{push.base}/ok/1")  # the test stub, added for this test only


# ---------------------------------------------------------------------------
# the notification centre, choices and quiet hours
# ---------------------------------------------------------------------------


def test_a_notification_is_stored_counted_and_announced_once_per_key(db, people):
    pat = people["pat"]
    first = notify.notify(
        db,
        pat,
        "careers",
        "Follow-up due",
        "Something is due.",
        "/#/dashboard",
        key="task:1",
    )
    assert first and first["person"] == f"p:{pat['id']}" and not first["read"]
    assert (
        notify.notify(db, pat, "careers", "Follow-up due", "Again", key="task:1")
        is None
    )
    keys = store.keys_of(db, pat)
    assert store.unread(db, keys) == 1
    [event] = stream_events()
    assert (
        event.topic == "notify"
        and event.person == f"p:{pat['id']}"
        and event.data["id"] == first["id"]
    )
    assert store.mark_read(db, keys) == 1 and store.unread(db, keys) == 0
    with pytest.raises(ValueError):
        notify.notify(db, pat, "weather", "Rain")
    # a link to another site is dropped: a click only ever opens this app's pages
    for elsewhere in ("https://evil.example/", "//evil.example/x", "/\\evil.example"):
        other = notify.notify(db, pat, "stocks", "Alert", url=elsewhere)
        assert other["url"] == "", elsewhere


def test_collapse_keeps_one_unread_item_per_thread(db, people):
    rosa = people["rosa"]
    for text in ("one", "two", "three"):
        notify.notify(db, rosa, "messages", f"New message: {text}", collapse="chat:abc")
    [item] = store.items(db, store.keys_of(db, rosa))
    assert item["title"] == "New message: three" and item["count"] == 3
    store.mark_read(db, store.keys_of(db, rosa))
    notify.notify(db, rosa, "messages", "New message: four", collapse="chat:abc")
    assert len(store.items(db, store.keys_of(db, rosa))) == 2


def test_the_account_owner_is_one_person_for_careers_and_the_household(db, people):
    pat = people["pat"]
    item = notify.notify(
        db,
        {"user_id": "u1", "email": "pat@home.example"},
        "careers",
        "Interview at 15:00",
    )
    assert (
        item["person"] == f"p:{pat['id']}"
    )  # "owner" is Pat once the household has profiles
    fresh = sqlite3.connect(":memory:")
    fresh.row_factory = sqlite3.Row
    lonely = notify.notify(fresh, {"user_id": "u2"}, "careers", "Follow-up due")
    assert lonely["person"] == "owner"


def test_choices_are_checked_and_merged_with_the_defaults(db, people):
    key = store.person_key(people["rosa"])
    assert store.prefs(db, key)["categories"]["medicine"] == {
        "push": True,
        "email": True,
    }
    assert store.prefs(db, key)["categories"]["careers"] == {
        "push": True,
        "email": False,
    }
    saved = store.save_prefs(
        db,
        key,
        {
            "categories": {"careers": {"email": True}, "weather": {"push": False}},
            "quiet": {"on": True, "start": "23:30", "end": "6:15"},
            "timezone": "Asia/Kolkata",
        },
    )
    assert saved["categories"]["careers"] == {"push": True, "email": True}
    assert "weather" not in saved["categories"]
    assert saved["quiet"] == {"on": True, "start": "23:30", "end": "06:15"}
    with pytest.raises(ValueError):
        store.save_prefs(db, key, {"timezone": "Mars/Olympus"})
    with pytest.raises(ValueError):
        store.save_prefs(db, key, {"email": "not an address"})
    assert (
        store.save_prefs(db, key, {"quiet": {"start": "25:99"}})["quiet"]["start"]
        == "23:30"
    )


def test_quiet_hours_cross_midnight_in_the_persons_time_zone():
    choice = {"quiet": {"on": True, "start": "22:00", "end": "07:00"}}
    at = lambda hour, minute=0: datetime(2026, 11, 6, hour, minute, tzinfo=timezone.utc)  # noqa: E731
    assert store.in_quiet(choice, at(23), "UTC") and store.in_quiet(
        choice, at(6, 59), "UTC"
    )
    assert not store.in_quiet(choice, at(7), "UTC") and not store.in_quiet(
        choice, at(12), "UTC"
    )
    # 17:00 UTC is 22:30 in India
    assert store.in_quiet(choice, at(17), "Asia/Kolkata")
    assert not store.in_quiet(
        {"quiet": {"on": False, "start": "00:00", "end": "23:59"}}, at(12), "UTC"
    )


def test_quiet_hours_hold_pushes_but_not_urgent_ones_then_send_one_digest(
    db, people, push
):
    rosa = people["rosa"]
    browser = Browser.new(push)
    store.subscribe(db, store.person_key(rosa), browser.subscription())
    store.save_prefs(
        db,
        store.person_key(rosa),
        {"quiet": {"on": True, "start": "22:00", "end": "07:00"}, "timezone": "UTC"},
    )
    night = datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)
    held = notify.notify(db, rosa, "stocks", "Price alert", "AAPL rose.", now=night)
    notify.notify(db, rosa, "messages", "New message", now=night)
    urgent = notify.notify(
        db,
        rosa,
        "medicine",
        "Time for your medicine",
        "A scheduled medicine is due.",
        urgency="high",
        now=night,
    )
    assert held["held"] and not urgent["held"]
    notify.flush()
    assert [m["title"] for m in browser.received(push)] == ["Time for your medicine"]
    assert notify.flush_held(db, night + timedelta(hours=1)) == 0  # still quiet
    assert notify.flush_held(db, night + timedelta(hours=9)) == 1
    notify.flush()
    digest = browser.received(push)[-1]
    assert (
        digest["body"] == "2 notifications while quiet hours were on."
        and digest["tag"] == "quiet-hours"
    )
    assert notify.flush_held(db, night + timedelta(hours=10)) == 0


# ---------------------------------------------------------------------------
# delivery: Web Push on the background thread, email
# ---------------------------------------------------------------------------


def test_a_push_reaches_every_browser_encrypted_and_never_inline(
    db, people, push, monkeypatch
):
    rosa = people["rosa"]
    laptop, phone = Browser.new(push), Browser.new(push)
    for browser in (laptop, phone):
        store.subscribe(db, store.person_key(rosa), browser.subscription())
    started = []
    real_send = webpush.send
    monkeypatch.setattr(
        webpush, "send", lambda *a, **k: started.append(1) or real_send(*a, **k)
    )
    item = notify.notify(
        db,
        rosa,
        "medicine",
        "Time for your medicine",
        "A scheduled medicine is due (08:00).",
        "/#/care",
        urgency="high",
        detail="Metformin 500 mg",
        key="dose:1",
    )
    # notify() returned before any delivery: they run on the delivery thread
    assert notify.flush()
    assert len(started) == 2
    for browser in (laptop, phone):
        [payload] = browser.received(push)
        assert payload == {
            "title": "Time for your medicine",
            "body": "A scheduled medicine is due (08:00).",
            "url": "/#/care",
            "tag": "dose:1",
            "category": "medicine",
            "id": item["id"],
        }
        assert "Metformin" not in json.dumps(payload)  # the detail never leaves the app
    delivery = push.deliveries[0]
    assert delivery.headers["content-encoding"] == "aes128gcm"
    assert delivery.headers["urgency"] == "high" and int(delivery.headers["ttl"]) > 0
    assert delivery.headers["authorization"].startswith("vapid t=")
    assert delivery.headers["topic"] == "dose1"
    assert (
        db.execute(
            "SELECT COUNT(*) FROM nt_push_subs WHERE last_ok_at IS NOT NULL"
        ).fetchone()[0]
        == 2
    )


def test_an_expired_subscription_is_forgotten_and_a_busy_service_is_tried_again(
    db, people, push
):
    rosa = people["rosa"]
    gone, busy = Browser.new(push, "gone"), Browser.new(push, "busy")
    for browser in (gone, busy):
        store.subscribe(db, store.person_key(rosa), browser.subscription())
    notify.notify(db, rosa, "stocks", "Price alert", "TSLA fell.")
    notify.flush(include_delayed=True)
    deadline = time.monotonic() + 10
    while len(push.deliveries) < 3 and time.monotonic() < deadline:
        notify.flush(include_delayed=True)
        time.sleep(0.05)
    paths = [d.path for d in push.deliveries]
    assert paths.count(gone.endpoint.removeprefix(push.base)) == 1
    assert (
        paths.count(busy.endpoint.removeprefix(push.base)) == 2
    )  # 429, then delivered
    left = [row["endpoint"] for row in store.subscriptions(db, store.keys_of(db, rosa))]
    assert left == [busy.endpoint]


def test_subscribing_refuses_anything_but_a_push_service(db, people):
    key = store.person_key(people["rosa"])
    with pytest.raises(ValueError, match="push service"):
        store.subscribe(
            db,
            key,
            {"endpoint": "http://10.0.0.5/admin", "keys": {"p256dh": "x", "auth": "y"}},
        )
    with pytest.raises(ValueError, match="push keys"):
        store.subscribe(
            db,
            key,
            {
                "endpoint": "https://fcm.googleapis.com/fcm/send/a",
                "keys": {"p256dh": "bad", "auth": "y"},
            },
        )


def test_email_goes_to_the_outbox_when_chosen_and_never_carries_the_detail(db, people):
    rosa = people["rosa"]
    notify.notify(
        db, rosa, "careers", "Follow-up due", "Something is due."
    )  # careers email is off by default
    assert [m for m in kit.outbox(db)] == []
    store.save_prefs(
        db, store.person_key(rosa), {"categories": {"careers": {"email": True}}}
    )
    notify.notify(
        db,
        rosa,
        "careers",
        "Interview at 15:00",
        "Your interview starts within the hour.",
        "/#/interviews",
        detail="Acme · Recruiter screen",
    )
    [mail] = kit.outbox(db)
    assert mail["recipient"] == "rosa@home.example" and mail["status"] == "approved"
    assert mail["subject"] == "Interview at 15:00" and mail["kind"] == "notify_careers"
    assert "Acme" not in mail["body"] and "/#/interviews" in mail["body"]
    # a chat's second message updates the unread item and sends no second email
    store.save_prefs(
        db, store.person_key(rosa), {"categories": {"messages": {"email": True}}}
    )
    notify.notify(db, rosa, "messages", "New message", collapse="chat:1")
    notify.notify(db, rosa, "messages", "New message", collapse="chat:1")
    assert len([m for m in kit.outbox(db) if m["kind"] == "notify_messages"]) == 1
    notify.notify(db, rosa, "medicine", "Time for your medicine", email=False)
    assert not [m for m in kit.outbox(db) if m["kind"] == "notify_medicine"]


def test_the_hosted_build_emails_through_the_platform_mail_on_the_delivery_thread(
    db, people, tmp_path
):
    sent = []
    HOOKS.notify = {
        "mailer": lambda: lambda to, subject, body: sent.append((to, subject, body)),
        "key_dir": lambda: tmp_path / "secrets",
        "live": False,
    }
    rosa = people["rosa"]
    store.save_prefs(
        db, store.person_key(rosa), {"categories": {"stocks": {"email": True}}}
    )
    notify.notify(db, rosa, "stocks", "Price alert: AAPL", "Apple rose above $200.")
    notify.flush()
    assert sent == [("rosa@home.example", "Price alert: AAPL", sent[0][2])]
    assert "Apple rose above $200." in sent[0][2]
    assert kit.outbox(db) == []  # not the organization's outbox
    assert stream_events() == []  # pages poll in the hosted build
    assert notify.live() is False
    webpush._cached = None
    assert webpush.public_key() and (tmp_path / "secrets" / webpush.KEY_FILE).is_file()


# ---------------------------------------------------------------------------
# the live hub
# ---------------------------------------------------------------------------


def test_the_hub_resumes_after_an_id_and_asks_for_a_resync_otherwise(monkeypatch):
    hub = realtime.Hub()
    first = hub.publish("local:a", "chat", {"chat": "1"}, person="p:x")
    hub.publish("local:b", "chat", {"chat": "2"})
    third = hub.publish("local:a", "notify", {"id": "n"})
    events, resync = hub.since("local:a", first)
    assert [e.id for e in events] == [third] and not resync
    assert hub.since("local:a", "") == ([], False)
    assert hub.since("local:a", "otherepoch-1") == (
        [],
        True,
    )  # a restart: fetch everything once
    monkeypatch.setattr(realtime, "BUFFER", 2)
    small = realtime.Hub()
    ids = [small.publish("local:a", "chat", {"n": n}) for n in range(5)]
    events, resync = small.since("local:a", ids[0])
    assert resync and [e.data["n"] for e in events] == [3, 4]
    wire = events[0].wire()
    assert wire.startswith(f"id: {ids[3]}\ndata: ") and json.loads(
        wire.split("data: ", 1)[1]
    ) == {"topic": "chat", "person": "", "n": 3}


def test_the_hub_caps_streams_and_wakes_waiters():
    hub = realtime.Hub()
    opened = [hub.open_stream("local:a") for _ in range(realtime.PER_CHANNEL)]
    after = [seq for seq, _ in opened]
    # a fifth page of the same account takes the oldest page's slot (it navigated away and has not noticed yet)
    _, newest = hub.open_stream("local:a")
    oldest = opened[0][1]
    assert hub.ended("local:a", oldest) and not hub.ended("local:a", newest)
    assert hub.wait("local:a", after[0], 5, oldest) == []  # the told-to-end stream stops waiting at once
    assert hub.stats()["streams"] == realtime.PER_CHANNEL
    hub.close("local:a", oldest)  # its own close does not free a second slot
    assert hub.stats()["streams"] == realtime.PER_CHANNEL
    hub.close("local:a", newest)
    assert hub.stats()["streams"] == realtime.PER_CHANNEL - 1
    # past the total cap a page is refused and keeps polling
    monkey = realtime.TOTAL
    try:
        realtime.TOTAL = hub.stats()["streams"]
        with pytest.raises(realtime.Busy):
            hub.open_stream("local:b")
    finally:
        realtime.TOTAL = monkey
    assert hub.wait("local:a", after[0], 0.01) == []
    import threading

    got = []
    waiter = threading.Thread(
        target=lambda: got.extend(hub.wait("local:a", after[0], 5))
    )
    waiter.start()
    time.sleep(0.05)
    hub.publish("local:a", "trivia", {"room": "r"})
    waiter.join(2)
    assert [e.topic for e in got] == ["trivia"]
    hub.shutdown()
    assert hub.wait("local:a", 99, 5) == []


# ---------------------------------------------------------------------------
# reminders made on the server
# ---------------------------------------------------------------------------


def _at(day: str, clock: str, zone: str = "America/New_York") -> datetime:
    from zoneinfo import ZoneInfo

    return (
        datetime.fromisoformat(f"{day}T{clock}")
        .replace(tzinfo=ZoneInfo(zone))
        .astimezone(timezone.utc)
    )


def test_a_due_dose_is_told_once_without_naming_the_medicine(db, people, push):
    from aira.suite.care import service as care
    from aira.suite.care import store as care_store

    care_store.ensure(db)
    kit.save_business(db, {"name": "Home", "timezone": "America/New_York"})
    rosa = people["rosa"]
    pills = care.create_medicine(
        db, rosa, {"name": "Metformin", "dose": "500 mg", "times": "08:00"}
    )
    db.execute(
        "UPDATE cr_medicines SET created_at = '2025-01-01T00:00:00Z' WHERE id = ?",
        (pills["id"],),
    )
    db.commit()
    browser = Browser.new(push)
    store.subscribe(db, store.person_key(rosa), browser.subscription())
    assert reminders.run(db, _at("2026-01-15", "07:50"))["medicine"] == 0  # not yet
    assert reminders.run(db, _at("2026-01-15", "08:05"))["medicine"] == 1
    assert reminders.run(db, _at("2026-01-15", "08:06"))["medicine"] == 0  # once
    [item] = store.items(db, store.keys_of(db, rosa))
    assert (
        item["title"] == "Time for your medicine"
        and item["detail"] == "Metformin 500 mg"
    )
    assert "Metformin" not in item["body"] and item["urgency"] == "high"
    notify.flush()
    [payload] = browser.received(push)
    assert "Metformin" not in json.dumps(payload) and payload["url"] == "/#/care"
    assert not kit.outbox(db)  # a reminder for every dose never goes by email


def test_a_missed_dose_reaches_household_caregivers_without_naming_the_medicine(
    db, people, push
):
    from aira.suite.care import service as care
    from aira.suite.care import store as care_store

    care_store.ensure(db)
    kit.save_business(db, {"name": "Home", "timezone": "America/New_York"})
    pat, rosa = people["pat"], people["rosa"]
    db.execute(
        "UPDATE life_profiles SET email = 'pat@home.example' WHERE id = ?", (pat["id"],)
    )
    db.commit()
    pills = care.create_medicine(
        db, rosa, {"name": "Metformin", "dose": "500 mg", "times": "08:00"}
    )
    db.execute(
        "UPDATE cr_medicines SET created_at = '2025-01-01T00:00:00Z' WHERE id = ?",
        (pills["id"],),
    )
    care.add_caregiver(db, rosa, {"profile_id": pat["id"]})
    care.add_caregiver(db, rosa, {"email": "nurse@clinic.example", "name": "Nurse Ann"})
    db.execute("UPDATE cr_caregivers SET created_at = '2025-01-01T00:00:00Z'")
    db.commit()
    browser = Browser.new(push)
    store.subscribe(db, store.person_key(pat), browser.subscription())
    assert care.notify_missed(db, _at("2026-01-15", "09:30")) == 2
    [item] = store.items(db, store.keys_of(db, pat))
    assert (
        item["title"] == "Rosa missed a scheduled medicine"
        and item["detail"] == "Metformin 500 mg"
    )
    notify.flush()
    [payload] = browser.received(push)
    assert payload[
        "title"
    ] == "Rosa missed a scheduled medicine" and "Metformin" not in json.dumps(payload)
    mails = {m["recipient"]: m for m in kit.outbox(db) if m["kind"] == "missed_dose"}
    assert set(mails) == {"pat@home.example", "nurse@clinic.example"}
    for mail in mails.values():
        assert (
            "Metformin" not in mail["subject"] + mail["body"]
            and mail["status"] == "approved"
        )
        assert "scheduled medicine" in mail["subject"]


def test_a_staff_member_hears_about_an_appointment_an_hour_ahead(db, people):
    from aira.suite.bookings import store as bookings_store

    bookings_store.ensure(db)
    rosa = people["rosa"]
    now = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    stamp = kit.now()
    for staff_id, name, email in (
        ("s1", "Rosa", "rosa@home.example"),
        ("s2", "Sam", "sam@salon.example"),
    ):
        db.execute(
            "INSERT INTO bk_staff (id, name, email, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (staff_id, name, email, stamp, stamp),
        )
    for booking_id, staff_id, minutes in (
        ("b1", "s1", 30),
        ("b2", "s2", 45),
        ("b3", "s1", 180),
    ):
        start = kit.iso(now + timedelta(minutes=minutes))
        db.execute(
            """INSERT INTO bk_bookings (id, reference, service_id, staff_id, customer_id, service_name, customer_name,
            start_at, end_at, busy_until, created_at, updated_at) VALUES (?, ?, 'x', ?, 'c', 'Haircut', 'Priya Shah', ?, ?, ?, ?, ?)""",
            (
                booking_id,
                f"R-{booking_id}",
                staff_id,
                start,
                start,
                start,
                stamp,
                stamp,
            ),
        )
    db.commit()
    assert reminders.run(db, now)["bookings"] == 2
    [item] = store.items(db, store.keys_of(db, rosa))
    assert item["title"] == "Booking at 09:30" and item["body"] == "Haircut with Priya."
    [mail] = [m for m in kit.outbox(db) if m["kind"] == "booking_staff_reminder"]
    assert mail["recipient"] == "sam@salon.example" and mail["status"] == "approved"
    assert reminders.run(db, now + timedelta(minutes=5))["bookings"] == 0  # once each


def test_careers_follow_ups_and_interviews_never_name_the_employer_outside_the_app(
    db, push
):
    from aira.schema import migrate

    migrate(db)
    db.commit()
    now = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
    due = (now - timedelta(minutes=5)).isoformat(timespec="minutes")
    db.execute(
        "INSERT INTO tasks (task_id, kind, title, due_at, status) VALUES ('t1', 'follow_up', 'Send follow-up after Acme screen', ?, 'open')",
        (due,),
    )
    db.execute(
        "INSERT INTO tasks (task_id, kind, title, due_at, status) VALUES ('t2', 'reminder', 'Old one', ?, 'open')",
        ((now - timedelta(days=5)).isoformat(timespec="minutes"),),
    )
    db.execute(
        "INSERT INTO jobs (job_id, source, external_id, title, company, job_url, follow_up_at) VALUES ('j1', 'manual', 'j1', 'Data Analyst', 'Globex', 'https://jobs.example/1', ?)",
        (due,),
    )
    start = (now + timedelta(minutes=40)).isoformat()
    db.execute(
        "INSERT INTO interviews (interview_id, company, role, interview_type, start_at, end_at) VALUES ('i1', 'Acme', 'Analyst', 'recruiter_screen', ?, ?)",
        (start, start),
    )
    db.commit()
    browser = Browser.new(push)
    store.subscribe(db, "owner", browser.subscription())
    assert reminders.run(db, now, only=("careers",))["careers"] == 3
    assert (
        reminders.run(db, now + timedelta(minutes=1), only=("careers",))["careers"] == 0
    )
    items = {i["title"]: i for i in store.items(db, ["owner"])}
    assert set(items) == {
        "Follow-up due",
        "Follow up on an application",
        "Interview at 09:40",
    }
    assert items["Interview at 09:40"]["detail"].startswith("Acme · Analyst")
    notify.flush()
    sent = json.dumps(browser.received(push))
    assert "Acme" not in sent and "Globex" not in sent and "Interview at 09:40" in sent


def test_a_stock_alert_that_fired_is_told_once(db, people):
    from aira.suite.stocks import store as stocks_store

    stocks_store.ensure(db)
    rosa = people["rosa"]
    stamp = kit.now()
    db.execute(
        """INSERT INTO stk_alerts (id, owner_id, symbol, kind, price_cents, active, triggered_at, triggered_on,
        triggered_price_cents, created_at, updated_at) VALUES ('a1', ?, 'AAPL', 'above', 20000, 0, ?, ?, 20500, ?, ?)""",
        (rosa["id"], stamp, kit.today(), stamp, stamp),
    )
    db.commit()
    assert reminders.run(db, only=("stocks",))["stocks"] == 1
    assert reminders.run(db, only=("stocks",))["stocks"] == 0
    [item] = store.items(db, store.keys_of(db, rosa))
    assert item["title"] == "Price alert: AAPL" and "rose above" in item["body"]


def test_reminders_never_make_an_apps_tables(db):
    assert reminders.run(db) == {
        "medicine": 0,
        "bookings": 0,
        "careers": 0,
        "stocks": 0,
    }
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert not {t for t in tables if t.startswith(("cr_", "bk_", "stk_", "suite_"))}
