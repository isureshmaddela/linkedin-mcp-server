"""Sending LinkedIn replies: manual send, per-conversation auto-reply, and worker recovery."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

import auto_apply_assistant as app

CLEAN_REPLY = "Hi Priya, thanks for reaching out. I'd be glad to learn more about the Cloud Architect role. Jordan"
RECRUITER_TEXT = (
    "Priya Recruiter\nHi Jordan, I came across your profile and think you'd be a great fit for a Cloud "
    "Architect role on our platform team. Would you be open to a quick chat this week?\n10:42 AM"
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "replies.sqlite3")
    monkeypatch.setattr(app, "ITEM_DELAY_SECONDS", 0)
    # The user's own name lets Aira tell who wrote the latest message.
    app._save_profile_settings(
        "",
        "",
        application_profile={"first_name": "Jordan", "last_name": "Example"},
    )
    return tmp_path


class FakeLinkedIn:
    """Stands in for the per-account LinkedIn MCP worker."""

    def __init__(self, threads: dict[str, str], *, send_status: str = "sent"):
        self.threads = threads
        self.send_status = send_status
        self.sent: list[dict] = []

    def __call__(self, name, arguments):
        if name == "get_inbox":
            return {
                "references": {
                    "inbox": [
                        {
                            "kind": "conversation",
                            "url": f"/messaging/thread/{thread_id}/",
                            "text": thread_id,
                        }
                        for thread_id in self.threads
                    ]
                }
            }
        if name == "get_conversation":
            return {
                "sections": {"conversation": self.threads[arguments["thread_id"]]},
                "references": {
                    "conversation": [{"kind": "person", "text": "Priya Recruiter"}]
                },
            }
        if name == "reply_to_conversation":
            assert arguments["confirm_send"] is True
            self.sent.append(arguments)
            if self.send_status != "sent":
                return {
                    "status": self.send_status,
                    "sent": False,
                    "message": "LinkedIn did not show a reply box.",
                }
            self.threads[arguments["thread_id"]] += (
                f"\nYou\n{arguments['message']}\n10:50 AM"
            )
            return {"status": "sent", "sent": True, "message": "Reply sent."}
        raise AssertionError(f"unexpected tool {name}")


def fake_model(reply=CLEAN_REPLY, needs_reply=True):
    calls = []

    def generate(model, prompt):
        calls.append(prompt)
        return {"reply": reply, "needs_reply": needs_reply}

    generate.calls = calls
    return generate


# ---------------------------------------------------------------------------
# Manual send
# ---------------------------------------------------------------------------


def test_manual_send_uses_in_thread_tool_and_records_history(db, monkeypatch):
    linkedin = FakeLinkedIn({"2-abc": RECRUITER_TEXT})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    app._save_reply_draft("2-abc", "Priya Recruiter", CLEAN_REPLY)

    record = app._send_thread_reply("2-abc", CLEAN_REPLY, participant="Priya Recruiter")

    assert linkedin.sent == [
        {"thread_id": "2-abc", "message": CLEAN_REPLY, "confirm_send": True}
    ]
    assert record["status"] == "sent" and record["mode"] == "manual"
    assert app._message_draft("2-abc") is None, "a sent draft is cleared"
    assert [s["status"] for s in app._thread_sends("2-abc")] == ["sent"]
    with app._connect() as connection:
        actions = [
            row["action"] for row in connection.execute("SELECT action FROM audit_logs")
        ]
    assert "message.send" in actions


def test_same_reply_cannot_be_sent_twice_in_a_day(db, monkeypatch):
    monkeypatch.setattr(app, "_call_mcp", FakeLinkedIn({"2-abc": RECRUITER_TEXT}))
    app._send_thread_reply("2-abc", CLEAN_REPLY)
    with pytest.raises(ValueError, match="already sent"):
        app._send_thread_reply("2-abc", "  " + CLEAN_REPLY.upper() + " ")


def test_unconfirmed_delivery_is_recorded_as_failed(db, monkeypatch):
    monkeypatch.setattr(
        app,
        "_call_mcp",
        FakeLinkedIn({"2-abc": RECRUITER_TEXT}, send_status="composer_unavailable"),
    )
    with pytest.raises(RuntimeError, match="reply box"):
        app._send_thread_reply("2-abc", CLEAN_REPLY)
    assert [s["status"] for s in app._thread_sends("2-abc")] == ["failed"]


def test_older_linkedin_session_gets_a_restart_hint(db, monkeypatch):
    def old_worker(name, arguments):
        raise RuntimeError("Unknown tool: 'reply_to_conversation'")

    monkeypatch.setattr(app, "_call_mcp", old_worker)
    with pytest.raises(RuntimeError, match="Restart LinkedIn session"):
        app._send_thread_reply("2-abc", CLEAN_REPLY)


def test_placeholder_and_empty_replies_are_rejected(db, monkeypatch):
    monkeypatch.setattr(app, "_call_mcp", FakeLinkedIn({"2-abc": RECRUITER_TEXT}))
    with pytest.raises(ValueError, match="placeholder"):
        app._send_thread_reply("2-abc", "Hi [Recruiter Name], thanks!")
    with pytest.raises(ValueError):
        app._send_thread_reply("2-abc", "   ")
    with pytest.raises(ValueError, match="thread"):
        app._send_thread_reply("../bad", CLEAN_REPLY)


def test_linked_recruiter_receives_the_sent_message(db, monkeypatch):
    monkeypatch.setattr(app, "_call_mcp", FakeLinkedIn({"2-abc": RECRUITER_TEXT}))
    linked = app._link_thread_recruiter("2-abc", "Priya Recruiter", "Acme")
    app._send_thread_reply("2-abc", CLEAN_REPLY)

    with app._connect() as connection:
        recruiter = app.get_recruiter(connection, linked["recruiter"]["recruiter_id"])
    assert recruiter is not None
    summaries = [item["summary"] for item in recruiter["interactions"]]
    assert CLEAN_REPLY[:60] in summaries[0]
    assert (
        app._thread_policy("2-abc")["recruiter_id"]
        == linked["recruiter"]["recruiter_id"]
    )


def test_send_endpoint_requires_explicit_confirmation(db, monkeypatch):
    linkedin = FakeLinkedIn({"2-abc": RECRUITER_TEXT})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    responses = []
    handler = object.__new__(app.RequestHandler)
    handler.path = "/api/conversations/2-abc/send"
    handler._origin_is_local = lambda: True
    handler._request_user = lambda: {"user_id": "test-user"}
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.send_error = lambda status, *args: responses.append((status, {}))

    handler._read_json = lambda: {"reply": CLEAN_REPLY}
    handler.do_POST()
    assert responses[-1][0] == 400 and linkedin.sent == []

    handler._read_json = lambda: {
        "reply": CLEAN_REPLY,
        "confirm": True,
        "participant": "Priya Recruiter",
    }
    handler.do_POST()
    assert responses[-1][0] == 200
    assert responses[-1][1]["send"]["status"] == "sent"
    assert len(linkedin.sent) == 1


# ---------------------------------------------------------------------------
# Reply mode and the inbox schedule
# ---------------------------------------------------------------------------


def test_reply_mode_defaults_to_manual_and_validates(db):
    assert app._thread_policy("2-abc")["mode"] == "manual"
    assert (
        app._set_thread_policy("2-abc", mode="auto", participant="Priya")["mode"]
        == "auto"
    )
    with pytest.raises(ValueError, match="manual or auto"):
        app._set_thread_policy("2-abc", mode="always")


def test_schedule_auto_replies_only_in_auto_conversations(db, monkeypatch):
    linkedin = FakeLinkedIn(
        {"auto-thread": RECRUITER_TEXT, "manual-thread": RECRUITER_TEXT}
    )
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", fake_model())
    app._set_thread_policy("auto-thread", mode="auto")

    summary = app._run_messages_schedule({"max_items": 5})

    assert [item["thread_id"] for item in linkedin.sent] == ["auto-thread"]
    assert "1 auto-replies sent" in summary
    assert "1 reply drafts for your approval" in summary
    assert app._message_draft("manual-thread") is not None
    assert app._message_draft("auto-thread") is None
    assert app._thread_sends("auto-thread")[0]["mode"] == "auto"


def test_sensitive_question_is_held_for_review(db, monkeypatch):
    linkedin = FakeLinkedIn(
        {"auto-thread": RECRUITER_TEXT + "\nWhat are your salary expectations?"}
    )
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", fake_model())
    app._set_thread_policy("auto-thread", mode="auto")

    summary = app._run_messages_schedule({"max_items": 5})

    assert linkedin.sent == []
    assert "1 held for your review" in summary
    held = app._thread_sends("auto-thread")[0]
    assert held["status"] == "held" and "compensation" in held["detail"]
    assert app._message_draft("auto-thread") is not None, (
        "held drafts stay available to send manually"
    )
    status = app._auto_reply_status()
    assert [item["thread_id"] for item in status["held"]] == ["auto-thread"]


def test_pause_and_daily_limit_hold_automatic_replies(db, monkeypatch):
    linkedin = FakeLinkedIn({"auto-thread": RECRUITER_TEXT})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", fake_model())
    app._set_thread_policy("auto-thread", mode="auto")
    app._save_settings({"auto_reply_paused": True})

    app._run_messages_schedule({"max_items": 5})
    assert linkedin.sent == []
    assert "paused" in app._thread_sends("auto-thread")[0]["detail"]

    app._save_settings({"auto_reply_paused": False, "daily_message_limit": 0})
    linkedin.threads["auto-thread"] += (
        "\nPriya Recruiter\nJust following up on my note above, any thoughts?"
    )
    app._run_messages_schedule({"max_items": 5})
    assert linkedin.sent == []
    assert "limit" in app._thread_sends("auto-thread")[0]["detail"]


def test_no_second_auto_reply_until_the_recruiter_writes_again(db, monkeypatch):
    linkedin = FakeLinkedIn({"auto-thread": RECRUITER_TEXT})
    model = fake_model()
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", model)
    app._set_thread_policy("auto-thread", mode="auto")

    app._run_messages_schedule({"max_items": 5})
    assert len(linkedin.sent) == 1 and len(model.calls) == 1

    # Our reply is now the newest message: the next check skips without drafting.
    summary = app._run_messages_schedule({"max_items": 5})
    assert len(linkedin.sent) == 1 and len(model.calls) == 1
    assert "1 need no reply" in summary

    # The recruiter writes again soon after: the 6-hour cooldown holds the reply.
    linkedin.threads["auto-thread"] += (
        "\nPriya Recruiter\n"
        + "Great, does Thursday at 2pm work for a call with the team? " * 3
    )
    app._run_messages_schedule({"max_items": 5})
    assert len(linkedin.sent) == 1
    assert "within the last 6 hours" in app._thread_sends("auto-thread")[0]["detail"]


def test_cooldown_allows_reply_after_it_expires(db, monkeypatch):
    linkedin = FakeLinkedIn({"auto-thread": RECRUITER_TEXT})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(
        app,
        "_ollama_json",
        fake_model(reply="Thanks Priya, Thursday afternoon works well for me. Jordan"),
    )
    app._set_thread_policy("auto-thread", mode="auto")
    app._send_thread_reply("auto-thread", CLEAN_REPLY, mode="auto")
    earlier = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(
        timespec="seconds"
    )
    with app._connect() as connection:
        connection.execute("UPDATE message_sends SET created_at = ?", (earlier,))
    linkedin.threads["auto-thread"] += (
        "\nPriya Recruiter\n"
        + "Great, does Thursday afternoon work for a call with the team? " * 3
    )

    app._run_messages_schedule({"max_items": 5})

    assert len(linkedin.sent) == 2


def test_auto_reply_status_reports_counts_and_schedule(db, monkeypatch):
    monkeypatch.setattr(app, "_call_mcp", FakeLinkedIn({"auto-thread": RECRUITER_TEXT}))
    app._set_thread_policy("auto-thread", mode="auto")
    app._create_schedule({"kind": "messages", "interval_minutes": 30, "max_items": 5})
    app._send_thread_reply("auto-thread", CLEAN_REPLY, mode="auto")

    status = app._auto_reply_status()

    assert status["auto_threads"] == 1
    assert status["sent_today"] == 1
    assert status["daily_limit"] == 20 and status["paused"] is False
    assert status["schedule"]["interval_minutes"] == 30


# ---------------------------------------------------------------------------
# LinkedIn worker recovery
# ---------------------------------------------------------------------------


def _closed_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_dead_worker_restarts_from_saved_sign_in_once_per_minute(db, monkeypatch):
    monkeypatch.setattr(app, "AUTH_DB_PATH", db / "accounts.sqlite3")
    monkeypatch.setattr(app, "_profile_is_ready", lambda path: True)
    started = []
    monkeypatch.setattr(
        app,
        "_start_linkedin_worker",
        lambda user: (
            started.append(user["user_id"]) or {"state": "starting", "message": ""}
        ),
    )
    monkeypatch.setattr(app, "_worker_restart_attempts", {})
    user = {"user_id": "u1", "linkedin_port": _closed_port()}

    assert app._linkedin_status(user)["state"] == "starting"
    assert app._linkedin_status(user)["state"] == "disconnected"
    assert started == ["u1"]


def test_connect_resumes_saved_session_without_a_login_window(db, monkeypatch):
    monkeypatch.setattr(app, "AUTH_DB_PATH", db / "accounts.sqlite3")
    monkeypatch.setattr(app, "_profile_is_ready", lambda path: True)
    monkeypatch.setattr(
        app, "_start_linkedin_worker", lambda user: {"state": "starting", "message": ""}
    )
    opened = []
    monkeypatch.setattr(
        app.subprocess, "Popen", lambda *args, **kwargs: opened.append(args) or None
    )

    assert app._start_linkedin_login({"user_id": "u1"})["state"] == "starting"
    assert opened == []


def test_stop_worker_refuses_a_process_it_cannot_verify(db, monkeypatch):
    monkeypatch.setattr(app, "AUTH_DB_PATH", db / "accounts.sqlite3")
    with app._auth_connect() as connection:
        connection.execute(
            "INSERT INTO users (user_id, email, password_hash, data_path, linkedin_port, worker_pid, created_at) VALUES ('u1', 'a@example.com', 'x', 'p', 51234, 4242, 0)"
        )
    monkeypatch.setattr(
        app, "_process_command_line", lambda pid: "C:\\Windows\\notepad.exe"
    )
    killed = []
    monkeypatch.setattr(
        app.subprocess, "run", lambda *args, **kwargs: killed.append(args)
    )

    assert app._stop_linkedin_worker({"user_id": "u1"}) is False
    assert killed == []

    monkeypatch.setattr(
        app,
        "_process_command_line",
        lambda pid: (
            "python -m linkedin_mcp_server --transport streamable-http --host 127.0.0.1 --port 51234"
        ),
    )
    assert app._stop_linkedin_worker({"user_id": "u1"}) is True
    assert len(killed) == 1


def test_model_saying_no_reply_cannot_hide_a_sensitive_question(db, monkeypatch):
    """Regression: a small model marked a salary and visa question as needing no reply."""
    linkedin = FakeLinkedIn(
        {
            "auto-thread": "Priya Recruiter\nWhat are your salary expectations, and do you need visa sponsorship?\n9:15 AM"
        }
    )
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", fake_model(needs_reply=False))
    app._set_thread_policy("auto-thread", mode="auto")

    summary = app._run_messages_schedule({"max_items": 5})

    assert linkedin.sent == []
    assert "1 held for your review" in summary
    detail = app._thread_sends("auto-thread")[0]["detail"]
    assert "compensation" in detail and "work authorization" in detail


def test_model_saying_no_reply_cannot_skip_a_plain_question(db, monkeypatch):
    linkedin = FakeLinkedIn({"auto-thread": RECRUITER_TEXT})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", fake_model(needs_reply=False))
    app._set_thread_policy("auto-thread", mode="auto")

    app._run_messages_schedule({"max_items": 5})

    assert len(linkedin.sent) == 1


def test_auto_reply_waits_when_the_users_name_is_unknown(db, monkeypatch):
    app._save_profile_settings("", "", application_profile={})
    linkedin = FakeLinkedIn({"auto-thread": RECRUITER_TEXT})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", fake_model())
    app._set_thread_policy("auto-thread", mode="auto")

    app._run_messages_schedule({"max_items": 5})

    assert linkedin.sent == []
    assert "full name" in app._thread_sends("auto-thread")[0]["detail"]


def test_user_reply_outside_aira_is_respected(db, monkeypatch):
    linkedin = FakeLinkedIn(
        {
            "auto-thread": RECRUITER_TEXT
            + "\nJordan Example\nThanks Priya, Thursday works.\n11:00 AM"
        }
    )
    model = fake_model()
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", model)
    app._set_thread_policy("auto-thread", mode="auto")

    app._run_messages_schedule({"max_items": 5})

    assert linkedin.sent == [] and model.calls == []


def test_booting_worker_is_reported_as_starting_not_stopped(db, monkeypatch):
    """Regression: a status check during worker boot cleared the session record."""
    monkeypatch.setattr(app, "AUTH_DB_PATH", db / "accounts.sqlite3")
    monkeypatch.setattr(app, "_profile_is_ready", lambda path: True)
    restarted = []
    monkeypatch.setattr(
        app,
        "_start_linkedin_worker",
        lambda user: restarted.append(1) or {"state": "starting", "message": ""},
    )
    monkeypatch.setattr(app, "_worker_restart_attempts", {})

    class Booting:
        def poll(self):
            return None

    monkeypatch.setitem(app._worker_processes, "u1", Booting())
    user = {"user_id": "u1", "linkedin_port": _closed_port()}

    assert app._linkedin_status(user)["state"] == "starting"
    assert restarted == [], "a booting worker must not be restarted"


class RealFormatLinkedIn(FakeLinkedIn):
    """Returns the whole messaging page, like the real worker, with label-style names."""

    def __call__(self, name, arguments):
        if name == "get_conversation":
            return {
                "sections": {"conversation": self.threads[arguments["thread_id"]]},
                "references": {
                    "conversation": [
                        {
                            "kind": "person",
                            "text": "Open Sam Rivera Kolli\u2019s profile",
                        },
                        {"kind": "person", "text": "Jordan\u2019s profile"},
                    ]
                },
            }
        if name == "reply_to_conversation":
            self.sent.append(arguments)
            text = self.threads[arguments["thread_id"]]
            marker = "Reply to conversation with"
            head, _, tail = text.partition(marker)
            self.threads[arguments["thread_id"]] = (
                head
                + "Jordan Example sent the following message at 12:05 PM\n"
                + "View Jordan\u2019s profile\n"
                + "Jordan Example  (She/Her)  12:05 PM\n\n"
                + arguments["message"]
                + "\n\n"
                + marker
                + tail
            )
            return {"status": "sent", "sent": True, "message": "Reply sent."}
        return super().__call__(name, arguments)


def _real_page():
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "linkedin_thread_page.txt").read_text(
        encoding="utf-8"
    )


REAL_REPLY = "Thanks Sam, I saw your email and will reply there shortly. Jordan"


def test_real_page_format_auto_reply_end_to_end(db, monkeypatch):
    linkedin = RealFormatLinkedIn({"2-real": _real_page()})
    model = fake_model(reply=REAL_REPLY, needs_reply=False)
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(app, "_ollama_json", model)
    app._set_thread_policy("2-real", mode="auto")

    summary = app._run_messages_schedule({"max_items": 5})

    assert len(linkedin.sent) == 1, summary
    assert app._thread_sends("2-real")[0]["participant"] == "Sam Rivera Kolli"
    prompt = model.calls[0]
    assert "Dana" not in prompt and "Load more conversations" not in prompt
    assert "Sam Rivera Kolli (Today 11:39 AM):" in prompt

    # Our reply is now the newest message: nothing more is sent or drafted.
    app._run_messages_schedule({"max_items": 5})
    assert len(linkedin.sent) == 1 and len(model.calls) == 1


def test_inbox_list_changes_do_not_make_a_thread_look_new(db):
    page = _real_page()
    other = page.replace("Dana: May i know", "Dana: Following up, may i know")
    assert app._thread_digest(page) == app._thread_digest(other)
    changed = page.replace("revert me back", "call me back")
    assert app._thread_digest(page) != app._thread_digest(changed)


def test_empty_conversation_read_is_retried_once(db, monkeypatch):
    monkeypatch.setattr(app.time, "sleep", lambda seconds: None)
    linkedin = RealFormatLinkedIn({"2-real": _real_page()})
    calls = {"count": 0}

    def flaky(name, arguments):
        if name == "get_conversation":
            calls["count"] += 1
            if calls["count"] == 1:
                return {"sections": {"conversation": ""}, "references": {}}
        return linkedin(name, arguments)

    monkeypatch.setattr(app, "_call_mcp", flaky)
    monkeypatch.setattr(app, "_ollama_json", fake_model(reply=REAL_REPLY))

    summary = app._run_messages_schedule({"max_items": 5})

    assert "0 failed" in summary
    assert app._message_draft("2-real") is not None


def test_request_without_question_mark_gets_an_auto_reply(db, monkeypatch):
    """Regression from the live run: 'please check and revert' was treated as no reply needed."""
    page = _real_page().replace(
        "Can we connect today at 10 EST?", "Can we connect today at 10 EST"
    )
    linkedin = RealFormatLinkedIn({"2-real": page})
    monkeypatch.setattr(app, "_call_mcp", linkedin)
    monkeypatch.setattr(
        app, "_ollama_json", fake_model(reply=REAL_REPLY, needs_reply=False)
    )
    app._set_thread_policy("2-real", mode="auto")

    app._run_messages_schedule({"max_items": 5})

    assert len(linkedin.sent) == 1
