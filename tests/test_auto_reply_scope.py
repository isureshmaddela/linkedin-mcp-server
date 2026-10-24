"""Auto-reply scope (selected / recruiters / all), the inbox check report, and the visible-browser toggle."""

from __future__ import annotations

import pytest

import auto_apply_assistant as app
from aira.messaging import effective_mode, recruiter_signal

RECRUITER = (
    "Sam Rivera Kolli sent the following message at 8:46 AM\n"
    "Sam Rivera Kolli   8:46 AM\n\n"
    "Hi Jordan, we are hiring for a Salesforce Developer role on W2. Can you share your resume?\n\n"
    "Reply to conversation with “Sure”"
)
FRIEND = (
    "Alex Morgan sent the following message at 9:10 AM\n"
    "Alex Morgan   9:10 AM\n\n"
    "Hey Jordan, are we still on for dinner Saturday?\n\n"
    "Reply to conversation with “Sure”"
)


class Inbox:
    def __init__(self, threads):
        self.threads = threads
        self.sent = []

    def __call__(self, name, arguments):
        if name == "get_inbox":
            return {
                "references": {
                    "inbox": [
                        {
                            "kind": "conversation",
                            "url": f"/messaging/thread/{tid}/",
                            "text": tid,
                        }
                        for tid in self.threads
                    ]
                }
            }
        if name == "get_conversation":
            participant = (
                "Sam Rivera Kolli"
                if arguments["thread_id"] == "recruiter"
                else "Alex Morgan"
            )
            return {
                "sections": {"conversation": self.threads[arguments["thread_id"]]},
                "references": {
                    "conversation": [
                        {"kind": "person", "text": f"Open {participant}’s profile"}
                    ]
                },
            }
        if name == "reply_to_conversation":
            self.sent.append(arguments)
            return {"status": "sent", "sent": True, "message": "Reply sent."}
        raise AssertionError(name)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "scope.sqlite3")
    monkeypatch.setattr(app, "ITEM_DELAY_SECONDS", 0)
    app._save_profile_settings(
        "", "", application_profile={"first_name": "Jordan", "last_name": "Example"}
    )
    inbox = Inbox({"recruiter": RECRUITER, "friend": FRIEND})
    monkeypatch.setattr(app, "_call_mcp", inbox)
    monkeypatch.setattr(
        app,
        "_ollama_json",
        lambda model, prompt: {
            "reply": "Thanks for reaching out, happy to talk more. Jordan",
            "needs_reply": True,
        },
    )
    return inbox


def test_recruiter_signal_and_reasons():
    assert recruiter_signal("We are hiring on W2, please share your resume")[0] is True
    assert recruiter_signal("Is this role still open for the position?")[0] is True
    assert recruiter_signal("Dinner on Saturday?")[0] is False
    assert "hiring" in recruiter_signal("We are hiring")[1]


def test_effective_mode_prefers_the_users_choice():
    assert (
        effective_mode(
            explicit_mode="manual", scope="all", is_recruiter=True, recruiter_reason=""
        )[0]
        == "manual"
    )
    assert (
        effective_mode(
            explicit_mode=None, scope="all", is_recruiter=False, recruiter_reason=""
        )[0]
        == "auto"
    )
    assert (
        effective_mode(
            explicit_mode=None,
            scope="recruiters",
            is_recruiter=True,
            recruiter_reason="r.",
        )[0]
        == "auto"
    )
    assert (
        effective_mode(
            explicit_mode=None,
            scope="recruiters",
            is_recruiter=False,
            recruiter_reason="r.",
        )[0]
        == "manual"
    )
    assert (
        effective_mode(
            explicit_mode=None, scope="selected", is_recruiter=True, recruiter_reason=""
        )[0]
        == "manual"
    )


def test_selected_scope_only_drafts(db):
    app._run_messages_schedule({"max_items": 5})
    assert db.sent == []
    report = app._latest_inbox_check()
    assert {item["outcome"] for item in report["items"]} == {"drafted"}


def test_recruiter_scope_replies_to_recruiters_only(db):
    app._save_settings({"auto_reply_scope": "recruiters"})
    app._run_messages_schedule({"max_items": 5})

    assert [item["thread_id"] for item in db.sent] == ["recruiter"]
    items = {item["thread_id"]: item for item in app._latest_inbox_check()["items"]}
    assert (
        items["recruiter"]["outcome"] == "sent" and items["recruiter"]["mode"] == "auto"
    )
    assert "recruiter outreach" in items["recruiter"]["mode_reason"]
    assert items["recruiter"]["participant"] == "Sam Rivera Kolli"
    assert items["recruiter"]["speaker"] == "them"
    assert (
        items["friend"]["outcome"] == "drafted" and items["friend"]["mode"] == "manual"
    )


def test_all_scope_and_per_conversation_override(db):
    app._save_settings({"auto_reply_scope": "all"})
    app._set_thread_policy("friend", mode="manual")
    app._run_messages_schedule({"max_items": 5})

    assert [item["thread_id"] for item in db.sent] == ["recruiter"]
    items = {item["thread_id"]: item for item in app._latest_inbox_check()["items"]}
    assert "you set this" in items["friend"]["mode_reason"]


def test_changing_scope_rechecks_conversations(db):
    app._run_messages_schedule({"max_items": 5})
    second = app._run_messages_schedule({"max_items": 5})
    assert "2 unchanged" in second
    app._save_settings({"auto_reply_scope": "recruiters"})
    app._run_messages_schedule({"max_items": 5})
    assert [item["thread_id"] for item in db.sent] == ["recruiter"]


def test_switching_a_conversation_to_auto_rechecks_it(db):
    app._run_messages_schedule({"max_items": 5})
    app._set_thread_policy("recruiter", mode="auto")
    app._run_messages_schedule({"max_items": 5})
    assert [item["thread_id"] for item in db.sent] == ["recruiter"]


def test_status_includes_scope_browser_and_last_check(db):
    app._save_settings(
        {"auto_reply_scope": "recruiters", "linkedin_show_browser": True}
    )
    app._run_messages_schedule({"max_items": 5})
    status = app._auto_reply_status()
    assert status["scope"] == "recruiters"
    assert status["show_browser"] is True
    assert len(status["last_check"]["items"]) == 2
    assert all(item["outcome_label"] for item in status["last_check"]["items"])


def test_invalid_scope_is_rejected(db):
    with pytest.raises(ValueError, match="selected, recruiters, or all"):
        app._save_settings({"auto_reply_scope": "everyone"})


def test_visible_browser_setting_controls_worker_headless(db, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(app, "_profile_is_ready", lambda path: True)
    launched = []

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        launched.append(kwargs["env"]["HEADLESS"])
        return FakeProcess()

    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(app, "_worker_processes", {})
    app._linkedin_profile_dir("u1").mkdir(parents=True)
    app._start_linkedin_worker({"user_id": "u1"})
    app._save_settings({"linkedin_show_browser": True})
    app._start_linkedin_worker({"user_id": "u1"})
    assert launched == ["1", "false"]
