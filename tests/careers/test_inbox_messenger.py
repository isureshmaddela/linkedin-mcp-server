"""Messages tab backend: cache-first reads, background sync, optimistic sends.

LinkedIn is replaced by a stand-in worker that returns pages in the format the
real site produces. Reads must never reach it; only a sync, an explicit refresh,
or a thread that has never been saved may.
"""

from __future__ import annotations

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import auto_apply_assistant as app

USER = {"user_id": "messenger-user"}
CONNECTED = {"state": "connected", "message": "LinkedIn session connected."}
DISCONNECTED = {"state": "disconnected", "message": "Connect your LinkedIn account."}

DANA_WORK = "Hi Jordan, we are hiring a Salesforce developer. May I know your current work authorization in USA?"


def list_row(name: str, label: str, preview: str, me: str = "Jordan Example") -> str:
    return (
        f"Status is online\n{name}\n{label}\n{label}\n\n{preview}\n\n"
        f". Press return to go to conversation details\n"
        f"Open the options list in your conversation with {me} and {name}\n"
    )


def thread_page(participant: str, messages: list[tuple[str, str, str]]) -> str:
    """messages: (sender, time, text), oldest first, all dated TODAY."""
    blocks = ["TODAY"]
    for sender, clock, text in messages:
        first = sender.split()[0]
        blocks.append(
            f"{sender} sent the following message at {clock}\nView {first}’s profile\n"
            f"{sender}   {clock}\n\n{text}\n"
        )
    blocks.append("Reply to conversation with “Sure”\nSure")
    return "\n".join(blocks)


class Worker:
    """Stand-in for the per-account LinkedIn MCP worker."""

    def __init__(self) -> None:
        self.threads: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.failing: set[str] = set()
        self.inbox_error: Exception | None = None
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.sent: list[dict] = []
        self.lock = threading.Lock()

    def add(self, thread_id, participant, label, preview, messages) -> None:
        self.threads[thread_id] = {
            "participant": participant,
            "label": label,
            "preview": preview,
            "messages": messages,
        }

    def __call__(self, name, arguments):
        thread_id = arguments.get("thread_id", "")
        with self.lock:
            self.calls.append((name, thread_id))
        if name == "get_inbox":
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(10), "test never released the inbox"
            if self.inbox_error:
                raise self.inbox_error
            text = "Messaging\nConversation List\n" + "".join(
                list_row(t["participant"], t["label"], t["preview"])
                for t in self.threads.values()
            )
            text += "Load more conversations\n"
            return {
                "sections": {"inbox": text},
                "references": {
                    "inbox": [
                        {
                            "kind": "conversation",
                            "url": f"/messaging/thread/{tid}/",
                            "text": t["participant"],
                        }
                        for tid, t in self.threads.items()
                    ]
                },
            }
        if name == "get_conversation":
            if thread_id in self.failing:
                raise RuntimeError("LinkedIn timed out")
            item = self.threads[thread_id]
            return {
                "sections": {
                    "conversation": thread_page(item["participant"], item["messages"])
                },
                "references": {
                    "conversation": [
                        {
                            "kind": "person",
                            "text": f"Open {item['participant']}’s profile",
                        }
                    ]
                },
            }
        if name == "reply_to_conversation":
            item = self.threads[thread_id]
            item["messages"].append(("Jordan Example", "2:05 PM", arguments["message"]))
            item["preview"] = f"You: {arguments['message']}"
            self.sent.append(arguments)
            return {"status": "sent", "sent": True, "message": "Reply sent."}
        raise AssertionError(f"unexpected tool {name}")


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "messenger.sqlite3")
    monkeypatch.setattr(app, "INBOX_SYNC_DELAY_SECONDS", 0)
    monkeypatch.setattr(app, "INBOX_MANUAL_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(app, "CONVERSATION_RETRY_SECONDS", 0)
    monkeypatch.setattr(app, "ITEM_DELAY_SECONDS", 0)
    monkeypatch.setattr(app, "_linkedin_status", lambda user: CONNECTED)
    app._save_profile_settings(
        "", "", application_profile={"first_name": "Jordan", "last_name": "Example"}
    )
    fake = Worker()
    fake.add(
        "t-dana",
        "Dana Lee",
        "11:42 AM",
        f"Dana: {DANA_WORK}",
        [("Dana Lee", "11:42 AM", DANA_WORK)],
    )
    fake.add(
        "t-sam",
        "Sam Rivera",
        "11:39 AM",
        "You: Sending my resume now",
        [
            (
                "Sam Rivera",
                "11:30 AM",
                "Please share your resume for the staffing role.",
            ),
            ("Jordan Example", "11:39 AM", "Sending my resume now"),
        ],
    )
    monkeypatch.setattr(app, "_call_mcp", fake)
    yield fake
    wait_idle()


def wait_idle(timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = app._inbox_sync_status(USER)
        if not status["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("the inbox sync never finished")


def sync_now(**options) -> dict:
    result = app._start_inbox_sync(USER, **options)
    wait_idle()
    return result


def payload(**options) -> dict:
    options.setdefault("q", "")
    options.setdefault("flt", "all")
    options.setdefault("limit", 100)
    return app._inbox_payload(USER, **options)


# ---------------------------------------------------------------------------
# Cache first
# ---------------------------------------------------------------------------


def test_inbox_read_never_touches_linkedin(worker, monkeypatch):
    monkeypatch.setattr(app, "_linkedin_status", lambda user: DISCONNECTED)
    result = payload()
    assert worker.calls == []
    assert result["conversations"] == [] and result["saved_total"] == 0
    assert result["linkedin_connected"] is False
    assert result["synced_at"] is None and result["stale"] is True
    assert result["syncing"] is False
    assert "not connected" in result["message"].lower()


def test_the_first_sync_fills_the_cache_with_previews(worker):
    started = app._start_inbox_sync(USER, trigger="manual")
    assert started["started"] is True and started["reason"] == ""
    status = wait_idle()
    assert status["status"] == "ok" and status["phase"] == "done"
    assert status["total"] == 2 and status["done"] == 2 and status["failed"] == 0
    assert status["finished_at"] and not status["error"]

    result = payload()
    assert result["linkedin_connected"] is True and result["stale"] is False
    assert result["synced_at"] and result["syncing"] is False
    by_id = {c["thread_id"]: c for c in result["conversations"]}
    dana, sam = by_id["t-dana"], by_id["t-sam"]
    assert dana["participant"] == "Dana Lee"
    assert dana["snippet"] == DANA_WORK
    assert dana["last_sender"] == "them" and dana["needs_reply"] is True
    assert dana["last_message_label"] == "11:42 AM"
    assert dana["has_messages"] is True and dana["message_count"] == 1
    assert sam["last_sender"] == "us" and sam["needs_reply"] is False
    assert sam["snippet"] == "Sending my resume now"
    assert sam["message_count"] == 2 and sam["is_recruiter"] is True
    # Keys the earlier UI and API relied on are still present.
    for key in (
        "thread_id",
        "participant",
        "mode",
        "mode_set_by_user",
        "recruiter_id",
        "last_status",
    ):
        assert key in dana
    assert result["counts"] == {
        "all": 2,
        "needs_reply": 1,
        "recruiters": 2,
        "auto": 0,
        "unread": 1,
    }
    # Reading the cache does not call LinkedIn again.
    calls = len(worker.calls)
    payload()
    payload(q="dana")
    assert len(worker.calls) == calls


def test_filters_and_search(worker):
    sync_now()
    assert [c["thread_id"] for c in payload(flt="needs_reply")["conversations"]] == [
        "t-dana"
    ]
    assert [c["thread_id"] for c in payload(flt="unread")["conversations"]] == [
        "t-dana"
    ]
    assert [c["thread_id"] for c in payload(q="resume")["conversations"]] == ["t-sam"]
    # The search covers message text that is not in the preview.
    assert [c["thread_id"] for c in payload(q="staffing role")["conversations"]] == [
        "t-sam"
    ]
    assert [c["thread_id"] for c in payload(q="dana")["conversations"]] == ["t-dana"]
    nothing = payload(q="no such person")
    assert nothing["conversations"] == [] and "match" in nothing["message"]
    assert nothing["saved_total"] == 2

    assert payload(flt="auto")["conversations"] == []
    app._set_thread_policy("t-sam", mode="auto", participant="Sam Rivera")
    auto = payload(flt="auto")
    assert [c["thread_id"] for c in auto["conversations"]] == ["t-sam"]
    assert auto["conversations"][0]["mode"] == "auto"
    assert auto["conversations"][0]["mode_set_by_user"] is True

    app._link_thread_recruiter("t-dana", "Dana Lee", "Acme")
    linked = {c["thread_id"]: c for c in payload()["conversations"]}
    assert (
        linked["t-dana"]["recruiter_id"]
        and linked["t-dana"]["recruiter_name"] == "Dana Lee"
    )


def test_scope_setting_drives_the_auto_filter(worker):
    sync_now()
    app._save_settings({"auto_reply_scope": "all"})
    assert payload(flt="auto")["counts"]["auto"] == 2
    app._save_settings({"auto_reply_scope": "recruiters"})
    # Both conversations read like recruiter outreach.
    assert payload(flt="auto")["counts"]["auto"] == 2
    app._save_settings({"auto_reply_scope": "selected"})
    assert payload(flt="auto")["counts"]["auto"] == 0


def test_opening_a_conversation_marks_it_read(worker):
    sync_now()
    assert payload(flt="unread")["counts"]["unread"] == 1
    app._open_conversation("t-dana", USER, refresh=False)
    assert payload(flt="unread")["counts"]["unread"] == 0
    # New activity makes it unread again.
    worker.threads["t-dana"]["preview"] = "Dana: Are you there?"
    worker.threads["t-dana"]["label"] = "12:10 PM"
    worker.threads["t-dana"]["messages"].append(
        ("Dana Lee", "12:10 PM", "Are you there?")
    )
    sync_now()
    assert payload(flt="unread")["counts"]["unread"] == 1


# ---------------------------------------------------------------------------
# Sync behaviour
# ---------------------------------------------------------------------------


def test_sync_is_single_flight(worker):
    worker.gate = threading.Event()
    first = app._start_inbox_sync(USER, trigger="manual")
    assert first["started"] is True
    assert worker.entered.wait(10)
    second = app._start_inbox_sync(USER, trigger="tab")
    assert second["started"] is False and second["reason"] == "already_running"
    assert second["sync"]["running"] is True
    assert payload()["syncing"] is True
    worker.gate.set()
    wait_idle()
    assert [name for name, _ in worker.calls].count("get_inbox") == 1
    assert app._inbox_sync_status(USER)["status"] == "ok"


def test_sync_reads_the_newest_threads_only(worker, monkeypatch):
    monkeypatch.setattr(app, "INBOX_SYNC_THREADS", 1)
    sync_now()
    fetched = [tid for name, tid in worker.calls if name == "get_conversation"]
    assert fetched == ["t-dana"]
    # The list still has both conversations.
    assert payload()["saved_total"] == 2
    assert {c["thread_id"]: c["has_messages"] for c in payload()["conversations"]} == {
        "t-dana": True,
        "t-sam": False,
    }


def test_sync_skips_threads_that_did_not_change(worker):
    sync_now()
    reads = [tid for name, tid in worker.calls if name == "get_conversation"]
    assert sorted(reads) == ["t-dana", "t-sam"]
    sync_now()
    assert len([1 for name, _ in worker.calls if name == "get_conversation"]) == 2
    status = app._inbox_sync_status(USER)
    assert status["total"] == 0 and status["status"] == "ok"
    # A changed preview is read again, and force reads everything.
    worker.threads["t-sam"]["preview"] = "Sam: Great, thanks"
    worker.threads["t-sam"]["messages"].append(
        ("Sam Rivera", "12:00 PM", "Great, thanks")
    )
    sync_now()
    assert len([1 for name, _ in worker.calls if name == "get_conversation"]) == 3
    sync_now(force=True)
    assert len([1 for name, _ in worker.calls if name == "get_conversation"]) == 5


def test_one_failing_thread_does_not_stop_the_sync(worker):
    worker.failing.add("t-dana")
    sync_now()
    status = app._inbox_sync_status(USER)
    assert status["status"] == "partial"
    assert status["done"] == 1 and status["failed"] == 1
    assert "timed out" in status["error"]
    flags = {c["thread_id"]: c["has_messages"] for c in payload()["conversations"]}
    assert flags == {"t-dana": False, "t-sam": True}
    # The failed thread is retried on the next sync.
    worker.failing.clear()
    sync_now()
    assert app._inbox_sync_status(USER)["status"] == "ok"


def test_sync_gives_up_after_repeated_failures(worker, monkeypatch):
    for index in range(4):
        worker.add(
            f"t-extra{index}",
            f"Person {index}",
            "10:00 AM",
            f"Person: hi {index}",
            [(f"Person {index}", "10:00 AM", "hi")],
        )
    worker.failing.update(worker.threads)
    sync_now()
    status = app._inbox_sync_status(USER)
    assert (
        status["status"] == "error"
        and status["failed"] == app.INBOX_SYNC_MAX_FAILURES_IN_A_ROW
    )
    reads = [1 for name, _ in worker.calls if name == "get_conversation"]
    assert len(reads) < 2 * len(worker.threads) * 2


def test_a_failed_inbox_load_keeps_the_cache(worker):
    sync_now()
    before = payload()["saved_total"]
    worker.inbox_error = RuntimeError("LinkedIn could not load the inbox.")
    sync_now()
    status = app._inbox_sync_status(USER)
    assert status["status"] == "error" and "could not load" in status["error"]
    assert payload()["saved_total"] == before == 2


def test_sync_needs_a_connected_session(worker, monkeypatch):
    monkeypatch.setattr(app, "_linkedin_status", lambda user: DISCONNECTED)
    result = app._start_inbox_sync(USER)
    assert result["started"] is False and result["reason"] == "not_connected"
    assert worker.calls == []
    assert app._inbox_sync_status(USER)["status"] == "idle"


def test_only_if_stale_skips_a_fresh_cache(worker):
    sync_now()
    calls = len(worker.calls)
    result = app._start_inbox_sync(USER, trigger="tab", only_if_stale=True)
    assert result["started"] is False and result["reason"] == "fresh"
    assert len(worker.calls) == calls


def test_a_sync_that_finds_nothing_still_confirms_the_copy(worker, monkeypatch):
    worker.threads.clear()
    worker.inbox_error = None

    def empty_inbox(name, arguments):
        if name == "get_inbox":
            return {"sections": {"inbox": "Messaging\nNo conversations yet"}}
        return worker(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", empty_inbox)
    sync_now()
    status = app._inbox_sync_status(USER)
    assert status["status"] == "ok" and status["total"] == 0
    result = payload()
    assert (
        result["saved_total"] == 0 and result["synced_at"] and result["stale"] is False
    )
    again = app._start_inbox_sync(USER, trigger="tab", only_if_stale=True)
    assert again["started"] is False and again["reason"] == "fresh"


def test_a_failed_sync_is_not_retried_every_minute(worker):
    worker.inbox_error = RuntimeError("LinkedIn could not load the inbox.")
    sync_now(trigger="tab", only_if_stale=True)
    assert app._inbox_sync_status(USER)["status"] == "error"
    calls = len(worker.calls)
    again = app._start_inbox_sync(USER, trigger="tab", only_if_stale=True)
    assert again["started"] is False and again["reason"] == "recent_attempt"
    assert len(worker.calls) == calls
    # A manual sync always tries.
    manual = app._start_inbox_sync(USER, trigger="manual")
    assert manual["started"] is True
    wait_idle()


def test_sync_progress_is_recorded(worker):
    worker.gate = threading.Event()
    app._start_inbox_sync(USER)
    assert worker.entered.wait(10)
    running = app._inbox_sync_status(USER)
    assert running["running"] is True and running["status"] == "running"
    assert running["phase"] == "listing" and running["finished_at"] is None
    worker.gate.set()
    wait_idle()


def test_a_crashed_run_is_reported_as_interrupted(worker):
    with app._connect() as connection:
        app.inbox_store.start_sync_run(connection, "manual", int(time.time()))
    status = app._inbox_sync_status(USER)
    assert status["running"] is False and status["status"] == "interrupted"


def test_sync_marks_no_more_threads_read_than_the_old_refresh(worker, monkeypatch):
    for index in range(15):
        worker.add(
            f"t-n{index}",
            f"Name {index}",
            "9:00 AM",
            f"Name: hello {index}",
            [(f"Name {index}", "9:00 AM", "hello")],
        )
    sync_now()
    reads = [tid for name, tid in worker.calls if name == "get_conversation"]
    assert len(reads) == app.INBOX_SYNC_THREADS == 10
    inbox_limits = [1 for name, _ in worker.calls if name == "get_inbox"]
    assert len(inbox_limits) == 1


# ---------------------------------------------------------------------------
# Opening a conversation
# ---------------------------------------------------------------------------


def test_a_saved_conversation_opens_without_linkedin(worker, monkeypatch):
    sync_now()
    monkeypatch.setattr(app, "_call_mcp", lambda *a: pytest.fail("LinkedIn was called"))
    opened = app._open_conversation("t-sam", USER, refresh=False)
    assert opened["source"] == "cache" and opened["stale"] is False
    assert opened["participant"] == "Sam Rivera" and opened["speaker"] == "us"
    assert [(m["sender"], m["own"]) for m in opened["messages"]] == [
        ("Sam Rivera", False),
        ("Jordan Example", True),
    ]
    assert opened["messages"][0]["text"].startswith("Please share your resume")
    assert opened["messages"][0]["sent_on"]
    for key in (
        "thread_id",
        "conversation",
        "messages",
        "speaker",
        "effective_mode",
        "mode_reason",
        "participant",
        "draft",
        "policy",
        "sends",
    ):
        assert key in opened
    assert opened["linkedin_connected"] is True and opened["synced_at"]
    assert opened["note"] == "" and opened["live_error"] == ""


def test_refresh_reads_linkedin_and_updates_the_cache(worker):
    sync_now()
    worker.threads["t-dana"]["messages"].append(
        ("Dana Lee", "12:10 PM", "Are you there?")
    )
    cached = app._open_conversation("t-dana", USER, refresh=False)
    assert len(cached["messages"]) == 1
    live = app._open_conversation("t-dana", USER, refresh=True)
    assert live["source"] == "live" and len(live["messages"]) == 2
    again = app._open_conversation("t-dana", USER, refresh=False)
    assert again["source"] == "cache" and len(again["messages"]) == 2


def test_an_unsaved_conversation_is_loaded_once_then_cached(worker):
    first = app._open_conversation("t-sam", USER, refresh=False)
    assert first["source"] == "live" and len(first["messages"]) == 2
    reads = len([1 for name, _ in worker.calls if name == "get_conversation"])
    second = app._open_conversation("t-sam", USER, refresh=False)
    assert second["source"] == "cache"
    assert len([1 for name, _ in worker.calls if name == "get_conversation"]) == reads


def test_not_connected_and_nothing_saved_is_an_explained_empty_page(
    worker, monkeypatch
):
    monkeypatch.setattr(app, "_linkedin_status", lambda user: DISCONNECTED)
    opened = app._open_conversation("t-sam", USER, refresh=False)
    assert opened["messages"] == [] and opened["source"] == "cache"
    assert opened["linkedin_connected"] is False
    assert "Settings > Integrations" in opened["note"]
    assert worker.calls == []


def test_not_connected_still_shows_what_was_saved(worker, monkeypatch):
    sync_now()
    monkeypatch.setattr(app, "_linkedin_status", lambda user: DISCONNECTED)
    calls = len(worker.calls)
    opened = app._open_conversation("t-sam", USER, refresh=True)
    assert len(opened["messages"]) == 2 and opened["note"] == ""
    assert opened["linkedin_connected"] is False
    assert len(worker.calls) == calls
    listed = payload()
    assert listed["linkedin_connected"] is False and listed["saved_total"] == 2


def test_a_failed_refresh_serves_the_saved_copy(worker):
    sync_now()
    worker.failing.add("t-sam")
    opened = app._open_conversation("t-sam", USER, refresh=True)
    assert len(opened["messages"]) == 2 and opened["source"] == "cache"
    assert "timed out" in opened["live_error"]


def test_a_failed_first_load_is_not_an_error_page(worker):
    worker.failing.add("t-sam")
    opened = app._open_conversation("t-sam", USER, refresh=False)
    assert opened["messages"] == [] and opened["live_error"]
    assert "could not load" in opened["note"].lower()


def test_old_copies_are_flagged_stale(worker, monkeypatch):
    sync_now()
    with app._connect() as connection:
        connection.execute(
            "UPDATE inbox_threads SET messages_synced_at = messages_synced_at - 7200, listed_at = listed_at - 7200"
        )
        connection.execute(
            "UPDATE inbox_sync_runs SET finished_at = finished_at - 7200"
        )
    opened = app._open_conversation("t-sam", USER, refresh=False)
    assert opened["stale"] is True and opened["source"] == "cache"
    assert payload()["stale"] is True
    result = app._start_inbox_sync(USER, trigger="tab", only_if_stale=True)
    assert result["started"] is True
    wait_idle()


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

REPLY = "Hi Dana, thanks for reaching out. I would be glad to hear more about the role. Jordan"


def test_a_sent_reply_shows_up_at_once_and_is_reconciled(worker, monkeypatch):
    sync_now()
    # The worker lists the reply only after the next sync: emulate a lag.
    original = worker.__call__

    def lagging(name, arguments):
        if name == "reply_to_conversation":
            worker.sent.append(arguments)
            return {"status": "sent", "sent": True, "message": "Reply sent."}
        return original(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", lagging)
    app._send_thread_reply("t-dana", REPLY, participant="Dana Lee")

    opened = app._open_conversation("t-dana", USER, refresh=False)
    assert [m["text"] for m in opened["messages"]][-1] == REPLY
    assert (
        opened["messages"][-1]["own"] is True
        and opened["messages"][-1]["source"] == "aira"
    )
    assert opened["speaker"] == "us"
    row = next(c for c in payload()["conversations"] if c["thread_id"] == "t-dana")
    assert row["last_sender"] == "us" and row["needs_reply"] is False
    assert row["snippet"] == REPLY and row["unread"] is False
    assert payload()["counts"]["needs_reply"] == 0

    # LinkedIn now shows the reply: the local copy gives way, nothing doubles.
    worker.threads["t-dana"]["messages"].append(("Jordan Example", "2:05 PM", REPLY))
    worker.threads["t-dana"]["preview"] = f"You: {REPLY}"
    refreshed = app._open_conversation("t-dana", USER, refresh=True)
    assert [m["text"] for m in refreshed["messages"]].count(REPLY) == 1
    assert [m["source"] for m in refreshed["messages"]] == ["linkedin", "linkedin"]
    assert len(refreshed["sends"]) == 1 and refreshed["sends"][0]["status"] == "sent"


def test_a_failed_send_adds_nothing_to_the_cache(worker, monkeypatch):
    sync_now()

    def failing(name, arguments):
        if name == "reply_to_conversation":
            return {
                "status": "composer_unavailable",
                "sent": False,
                "message": "No reply box.",
            }
        return worker(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", failing)
    with pytest.raises(RuntimeError):
        app._send_thread_reply("t-dana", REPLY, participant="Dana Lee")
    opened = app._open_conversation("t-dana", USER, refresh=False)
    assert [m["text"] for m in opened["messages"]] == [DANA_WORK]


def test_guardrails_still_apply_to_cached_threads(worker):
    sync_now()
    with pytest.raises(ValueError, match="placeholder"):
        app._send_thread_reply("t-dana", "Hi [Recruiter Name], thanks for the note!")
    with pytest.raises(ValueError):
        app._send_thread_reply("t-dana", "   ")
    assert worker.sent == []
    opened = app._open_conversation("t-dana", USER, refresh=False)
    assert len(opened["messages"]) == 1


# ---------------------------------------------------------------------------
# Inbox checks keep the cache warm
# ---------------------------------------------------------------------------


def test_the_scheduled_inbox_check_fills_the_cache(worker, monkeypatch):
    monkeypatch.setattr(
        app, "_ollama_json", lambda model, prompt: {"reply": REPLY, "needs_reply": True}
    )
    app._run_messages_schedule({"max_items": 5})
    listed = payload()
    assert listed["saved_total"] == 2
    by_id = {c["thread_id"]: c for c in listed["conversations"]}
    assert by_id["t-dana"]["snippet"] == DANA_WORK
    assert by_id["t-dana"]["has_messages"] is True
    # A check never sends on its own in manual conversations.
    assert worker.sent == []


def test_a_caching_failure_cannot_break_an_inbox_check(worker, monkeypatch):
    monkeypatch.setattr(
        app, "_ollama_json", lambda model, prompt: {"reply": REPLY, "needs_reply": True}
    )

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(app.inbox_store, "upsert_listing", broken)
    monkeypatch.setattr(app.inbox_store, "store_conversation", broken)
    summary = app._run_messages_schedule({"max_items": 5})
    assert "reply drafts" in summary


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Requested-With", "aira")
        request.add_header("Origin", self.base)
        try:
            with self.opener.open(request, timeout=30) as response:
                status, payload_bytes = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload_bytes = error.code, error.read()
        return status, json.loads(
            payload_bytes.decode("utf-8")
        ) if payload_bytes else {}


@pytest.fixture
def http_client(tmp_path, worker, monkeypatch):
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=50, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    try:
        yield client
    finally:
        deadline = time.time() + 15
        while time.time() < deadline and app._inbox_sync_running:
            time.sleep(0.02)
        httpd.shutdown()
        httpd.server_close()


def http_wait(client: Client, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = client.call("GET", "/api/inbox/sync")
        assert status == 200
        if not body["running"]:
            return body
        time.sleep(0.05)
    raise AssertionError("sync never finished")


def sign_up(client: Client) -> None:
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "messenger@example.com", "password": "a long enough password 123"},
    )
    assert status == 201, body


def test_routes_require_a_session(http_client):
    for method, path in (
        ("GET", "/api/inbox"),
        ("GET", "/api/inbox/sync"),
        ("POST", "/api/inbox/sync"),
        ("GET", "/api/conversations/t-dana"),
    ):
        assert (
            http_client.call(method, path, {} if method == "POST" else None)[0] == 401
        )


def test_http_journey_cache_first_sync_then_read(http_client, worker):
    sign_up(http_client)
    status, empty = http_client.call("GET", "/api/inbox")
    assert status == 200 and empty["conversations"] == [] and empty["saved_total"] == 0
    assert empty["linkedin_connected"] is True
    assert set(empty) >= {
        "conversations",
        "total",
        "counts",
        "synced_at",
        "stale",
        "syncing",
        "sync",
        "linkedin_connected",
        "message",
    }
    assert worker.calls == [], "listing the inbox must not touch LinkedIn"

    status, started = http_client.call(
        "POST", "/api/inbox/sync", {"trigger": "tab", "only_if_stale": True}
    )
    assert status == 202 and started["started"] is True
    finished = http_wait(http_client)
    # The tab's automatic sync reads the list only: no thread is opened on LinkedIn.
    assert finished["status"] == "ok" and finished["total"] == 0
    assert [name for name, _ in worker.calls] == ["get_inbox"]
    status, previews = http_client.call("GET", "/api/inbox")
    assert {c["thread_id"]: c["snippet"] for c in previews["conversations"]} == {
        "t-dana": DANA_WORK,
        "t-sam": "Sending my resume now",
    }
    status, started = http_client.call("POST", "/api/inbox/sync", {})
    assert status == 202 and started["started"] is True
    finished = http_wait(http_client)
    assert finished["status"] == "ok" and finished["done"] == 2

    status, listed = http_client.call("GET", "/api/inbox?filter=needs_reply")
    assert status == 200 and [c["thread_id"] for c in listed["conversations"]] == [
        "t-dana"
    ]
    status, searched = http_client.call("GET", "/api/inbox?q=staffing")
    assert [c["thread_id"] for c in searched["conversations"]] == ["t-sam"]
    assert http_client.call("GET", "/api/inbox?filter=nonsense")[0] == 400
    assert http_client.call("GET", "/api/inbox?limit=0")[0] == 400

    status, again = http_client.call("POST", "/api/inbox/sync", {"only_if_stale": True})
    assert status == 200 and again["started"] is False and again["reason"] == "fresh"

    calls = len(worker.calls)
    status, conversation = http_client.call("GET", "/api/conversations/t-sam")
    assert status == 200 and conversation["source"] == "cache"
    assert (
        len(conversation["messages"]) == 2
        and conversation["policy"]["thread_id"] == "t-sam"
    )
    assert len(worker.calls) == calls

    status, refreshed = http_client.call("GET", "/api/conversations/t-sam?refresh=1")
    assert status == 200 and refreshed["source"] == "live"
    assert len(worker.calls) == calls + 1


def test_http_conversation_is_never_a_404_when_unsaved_and_offline(
    http_client, monkeypatch
):
    sign_up(http_client)
    monkeypatch.setattr(app, "_linkedin_status", lambda user: DISCONNECTED)
    status, conversation = http_client.call("GET", "/api/conversations/t-unknown")
    assert status == 200
    assert (
        conversation["messages"] == [] and conversation["linkedin_connected"] is False
    )
    assert "Settings > Integrations" in conversation["note"]
    status, sync = http_client.call("POST", "/api/inbox/sync", {})
    assert (
        status == 200 and sync["started"] is False and sync["reason"] == "not_connected"
    )
    status, listed = http_client.call("GET", "/api/inbox")
    assert (
        listed["linkedin_connected"] is False
        and "not connected" in listed["message"].lower()
    )


def test_http_send_appends_to_the_saved_thread(http_client, worker):
    sign_up(http_client)
    http_client.call("POST", "/api/inbox/sync", {})
    http_wait(http_client)
    status, sent = http_client.call(
        "POST",
        "/api/conversations/t-dana/send",
        {"reply": REPLY, "confirm": True, "participant": "Dana Lee"},
    )
    assert status == 200 and sent["send"]["status"] == "sent"
    status, conversation = http_client.call("GET", "/api/conversations/t-dana")
    assert status == 200
    assert [m["text"] for m in conversation["messages"]].count(REPLY) == 1


def test_export_includes_the_cache(http_client, worker):
    sign_up(http_client)
    http_client.call("POST", "/api/inbox/sync", {})
    http_wait(http_client)
    status, export = http_client.call("GET", "/api/account/export")
    assert status == 200
    assert len(export["tables"]["inbox_threads"]) == 2
    assert len(export["tables"]["inbox_messages"]) == 3


# ---------------------------------------------------------------------------
# Reads, staleness and coordination
# ---------------------------------------------------------------------------


def conversation_reads(worker) -> list[str]:
    return [tid for name, tid in worker.calls if name == "get_conversation"]


def test_a_tab_sync_reads_the_list_only(worker):
    for index in range(15):
        name = f"Person {chr(65 + index)}"
        worker.add(
            f"t-n{index}",
            name,
            "9:00 AM",
            f"Person: hello {index}",
            [(name, "9:00 AM", "hello")],
        )
    started = app._start_inbox_sync(USER, trigger="tab", only_if_stale=True)
    assert started["started"] is True
    status = wait_idle()
    assert status["status"] == "ok" and status["total"] == 0
    # Opening the Messages tab must not open (and so mark read) any conversation.
    assert conversation_reads(worker) == []
    listed = payload()
    assert listed["saved_total"] == 17 and listed["stale"] is False
    assert all(c["snippet"] for c in listed["conversations"])
    assert not any(c["has_messages"] for c in listed["conversations"])


def test_repeated_manual_syncs_are_throttled(worker, monkeypatch):
    monkeypatch.setattr(app, "INBOX_MANUAL_COOLDOWN_SECONDS", 60)
    first = app._start_inbox_sync(USER, trigger="manual")
    assert first["started"] is True
    wait_idle()
    calls = len(worker.calls)
    second = app._start_inbox_sync(USER, trigger="manual")
    assert second["started"] is False and second["reason"] == "recent_attempt"
    assert len(worker.calls) == calls
    forced = app._start_inbox_sync(USER, trigger="manual", force=True)
    assert forced["started"] is True
    wait_idle()


def test_a_sync_waits_for_a_running_inbox_check(worker):
    assert app._try_begin_automation(USER["user_id"]) is True
    try:
        result = app._start_inbox_sync(USER, trigger="manual")
        assert result["started"] is False and result["reason"] == "busy"
        assert worker.calls == []
    finally:
        app._end_automation(USER["user_id"])
    assert app._start_inbox_sync(USER, trigger="manual")["started"] is True
    wait_idle()
    # The sync gave the slot back, so an inbox check can run again.
    assert app._try_begin_automation(USER["user_id"]) is True
    app._end_automation(USER["user_id"])


def test_an_inbox_check_cannot_start_while_a_sync_runs(worker):
    worker.gate = threading.Event()
    app._start_inbox_sync(USER, trigger="manual")
    assert worker.entered.wait(10)
    assert app._try_begin_automation(USER["user_id"]) is False
    worker.gate.set()
    wait_idle()


def test_an_unreadable_list_does_not_wipe_previews_or_hide_new_messages(
    worker, monkeypatch
):
    sync_now()
    before = {c["thread_id"]: c for c in payload()["conversations"]}
    assert before["t-dana"]["needs_reply"] is True

    original = worker.__call__

    def blank_list(name, arguments):
        if name == "get_inbox":
            return {"sections": {"inbox": "Messaging\nsomething unexpected\n"}}
        return original(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", blank_list)
    # The unreadable list cannot be matched to threads, but threads are known.
    app._cache_inbox_listing(
        [
            {"thread_id": "t-dana", "participant": "Dana Lee"},
            {"thread_id": "t-sam", "participant": "Sam Rivera"},
        ],
        "garbled text with no rows",
    )
    after = {c["thread_id"]: c for c in payload()["conversations"]}
    assert after["t-dana"]["snippet"] == DANA_WORK
    assert after["t-dana"]["needs_reply"] is True
    assert after["t-dana"]["last_sender"] == "them"

    # With nothing readable a manual sync never trusts the old fingerprint: a
    # message that arrived meanwhile is fetched.
    monkeypatch.setattr(app, "_call_mcp", original)
    worker.threads["t-dana"]["messages"].append(
        ("Dana Lee", "12:10 PM", "Are you there?")
    )

    def unreadable_inbox(name, arguments):
        if name == "get_inbox":
            result = original(name, arguments)
            result["sections"]["inbox"] = "Messaging\n" + "\n".join(
                f"{t['participant']} (no times)" for t in worker.threads.values()
            )
            return result
        return original(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", unreadable_inbox)
    reads = len(conversation_reads(worker))
    sync_now()
    assert len(conversation_reads(worker)) - reads == 2
    opened = app._open_conversation("t-dana", USER, refresh=False)
    assert opened["messages"][-1]["text"] == "Are you there?"


def test_a_row_without_a_preview_shows_the_newest_saved_message(worker, monkeypatch):
    monkeypatch.setattr(
        app, "_call_mcp", lambda name, arguments: pytest.fail("no LinkedIn call")
    )
    with app._connect() as connection:
        app.inbox_store.store_conversation(
            connection,
            "t-x",
            participant="Xavier",
            messages=[
                {
                    "sender": "Xavier",
                    "own": False,
                    "date": "Today",
                    "time": "9:00 AM",
                    "text": "Please send your availability.",
                }
            ],
            raw_text="",
            last_sender="them",
            needs_reply=True,
            is_recruiter=False,
            now=int(time.time()),
        )
        connection.execute(
            "UPDATE inbox_threads SET snippet = '' WHERE thread_id = 't-x'"
        )
    row = payload()["conversations"][0]
    assert row["snippet"] == "Please send your availability."


def test_a_thread_confirmed_by_the_list_is_not_scraped_again_when_opened(worker):
    sync_now()
    with app._connect() as connection:
        connection.execute(
            "UPDATE inbox_threads SET messages_synced_at = messages_synced_at - 7200,"
            " listed_at = listed_at - 7200"
        )
        connection.execute(
            "UPDATE inbox_sync_runs SET finished_at = finished_at - 7200"
        )
    assert app._open_conversation("t-sam", USER, refresh=False)["stale"] is True
    reads = len(conversation_reads(worker))

    # A new list read shows the same preview: nothing arrived, the copy is current.
    app._start_inbox_sync(USER, trigger="tab", only_if_stale=True)
    wait_idle()
    assert len(conversation_reads(worker)) == reads
    opened = app._open_conversation("t-sam", USER, refresh=True, only_if_stale=True)
    assert opened["source"] == "cache" and opened["stale"] is False
    assert len(conversation_reads(worker)) == reads

    # An explicit Refresh always reads LinkedIn.
    explicit = app._open_conversation("t-sam", USER, refresh=True)
    assert explicit["source"] == "live"
    assert len(conversation_reads(worker)) == reads + 1


def test_a_changed_preview_makes_the_saved_thread_stale(worker):
    sync_now()
    worker.threads["t-dana"]["preview"] = "Dana: Are you there?"
    worker.threads["t-dana"]["label"] = "12:10 PM"
    worker.threads["t-dana"]["messages"].append(
        ("Dana Lee", "12:10 PM", "Are you there?")
    )
    app._start_inbox_sync(USER, trigger="tab")
    wait_idle()
    with app._connect() as connection:
        connection.execute(
            "UPDATE inbox_threads SET messages_synced_at = messages_synced_at - 7200"
        )
    stale = app._open_conversation("t-dana", USER, refresh=False)
    assert stale["stale"] is True
    live = app._open_conversation("t-dana", USER, refresh=True, only_if_stale=True)
    assert live["source"] == "live" and len(live["messages"]) == 2


def test_a_sync_that_fails_every_thread_is_not_a_fresh_copy(worker):
    worker.failing.update(worker.threads)
    sync_now()
    status = app._inbox_sync_status(USER)
    assert status["status"] == "error"
    listed = payload()
    assert listed["saved_total"] == 2
    assert listed["stale"] is True and listed["synced_at"] is None


def test_the_same_reply_sent_on_two_days_is_kept_until_listed(worker, monkeypatch):
    sync_now()
    original = worker.__call__
    hidden = {"on": False}

    def delivering(name, arguments):
        if name == "reply_to_conversation":
            worker.sent.append(arguments)
            if not hidden["on"]:
                item = worker.threads[arguments["thread_id"]]
                item["messages"].append(
                    ("Jordan Example", "2:05 PM", arguments["message"])
                )
            return {"status": "sent", "sent": True, "message": "Reply sent."}
        return original(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", delivering)
    app._send_thread_reply("t-dana", REPLY, participant="Dana Lee")
    app._open_conversation("t-dana", USER, refresh=True)
    # Two days later the same words go out again, and LinkedIn is slow to list them.
    with app._connect() as connection:
        connection.execute(
            "UPDATE message_sends SET created_at = '2026-01-01T00:00:00+00:00'"
        )
    hidden["on"] = True
    app._send_thread_reply("t-dana", REPLY, participant="Dana Lee")
    shown = app._open_conversation("t-dana", USER, refresh=True)
    assert [m["text"] for m in shown["messages"]].count(REPLY) == 2
    assert shown["speaker"] == "us"
    row = next(c for c in payload()["conversations"] if c["thread_id"] == "t-dana")
    assert row["last_sender"] == "us" and row["needs_reply"] is False


def test_a_send_is_refused_after_new_messages_were_saved(worker):
    sync_now()
    seen = len(app._open_conversation("t-dana", USER, refresh=False)["messages"])
    worker.threads["t-dana"]["messages"].append(
        ("Dana Lee", "12:10 PM", "Are you there?")
    )
    app._open_conversation("t-dana", USER, refresh=True)
    with pytest.raises(ValueError, match="New messages arrived"):
        app._check_thread_unchanged("t-dana", seen)
    app._check_thread_unchanged("t-dana", seen + 1)
    app._check_thread_unchanged("t-dana", None)
    app._check_thread_unchanged("t-dana", "two")


def test_the_saved_copy_can_be_cleared(worker):
    sync_now()
    cleared = app._clear_inbox_cache(USER)
    assert cleared["cleared"] is True and cleared["threads"] == 2
    listed = payload()
    assert listed["saved_total"] == 0 and listed["synced_at"] is None
    worker.gate = threading.Event()
    app._start_inbox_sync(USER, trigger="manual")
    assert worker.entered.wait(10)
    with pytest.raises(ValueError, match="sync is running"):
        app._clear_inbox_cache(USER)
    worker.gate.set()
    wait_idle()


def test_a_finished_sync_prunes_idle_threads(worker):
    sync_now()
    with app._connect() as connection:
        connection.execute(
            "INSERT INTO inbox_threads (thread_id, participant, listed_at, synced_at)"
            " VALUES ('t-ancient', 'Old Contact', 1, 1)"
        )
    assert payload()["saved_total"] == 3
    sync_now()
    assert payload()["saved_total"] == 2


def test_http_clear_and_send_guard(http_client, worker):
    sign_up(http_client)
    http_client.call("POST", "/api/inbox/sync", {})
    http_wait(http_client)
    status, conversation = http_client.call("GET", "/api/conversations/t-dana")
    assert status == 200 and conversation["saved"] is True
    count = conversation["message_count"]

    worker.threads["t-dana"]["messages"].append(
        ("Dana Lee", "12:10 PM", "Are you there?")
    )
    http_client.call("GET", "/api/conversations/t-dana?refresh=1")
    status, refused = http_client.call(
        "POST",
        "/api/conversations/t-dana/send",
        {
            "reply": REPLY,
            "confirm": True,
            "participant": "Dana Lee",
            "seen_messages": count,
        },
    )
    assert status == 400 and "New messages arrived" in refused["error"]
    assert worker.sent == []

    status, cleared = http_client.call("DELETE", "/api/inbox/cache")
    assert status == 200 and cleared["cleared"] is True
    status, listed = http_client.call("GET", "/api/inbox")
    assert listed["saved_total"] == 0
    status, unsaved = http_client.call("GET", "/api/conversations/t-unknown2")
    assert status == 200 and unsaved["saved"] is False and unsaved["messages"] == []
