"""The inbox cache: list parsing, date labels, threads, messages, and sync runs."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from aira import inbox_store as store
from aira import schema

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_thread_page.txt"
NOW = int(datetime(2026, 10, 22, 14, 30).timestamp())
TODAY = date(2026, 10, 22)


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    store.ensure_schema(connection)
    yield connection
    connection.close()


def listing(thread_id, name, snippet="", label="11:42 AM", sender="them", **extra):
    return {
        "thread_id": thread_id,
        "participant": name,
        "snippet": snippet,
        "label": label,
        "at": "2026-10-22T11:42",
        "last_sender": sender,
        "needs_reply": sender == "them",
        "is_recruiter": False,
        **extra,
    }


def message(sender, text, *, own=False, date_label="Today", time="9:00 AM", **extra):
    return {
        "sender": sender,
        "own": own,
        "date": date_label,
        "time": time,
        "text": text,
        **extra,
    }


def texts(db, thread_id):
    return [m["text"] for m in store.get_messages(db, thread_id)]


# ---------------------------------------------------------------------------
# Dates and clocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("TODAY", "2026-10-22"),
        ("Today", "2026-10-22"),
        ("YESTERDAY", "2026-10-21"),
        ("WEDNESDAY", "2026-10-21"),  # 2026-10-22 is a Thursday
        ("Wed", "2026-10-21"),
        ("THURSDAY", "2026-10-15"),  # the same weekday is printed as TODAY
        ("Mon", "2026-10-19"),
        ("OCT 7", "2026-10-07"),
        ("Oct 7", "2026-10-07"),
        ("AUG 7, 2025", "2025-08-07"),
        ("Sept 3", "2026-09-03"),
        ("DEC 30", "2025-12-30"),  # a December date can only be last year's
        ("FEB 30", ""),
        ("", ""),
        ("Reply now", ""),
    ],
)
def test_resolve_date(label, expected):
    assert store.resolve_date(label, TODAY) == expected


def test_clock_parsing_and_message_time():
    assert store.parse_clock("12:59 PM") == (12, 59)
    assert store.parse_clock("12:05 AM") == (0, 5)
    assert store.parse_clock("9:30 pm") == (21, 30)
    assert store.parse_clock("25:00 PM") is None
    assert store.parse_clock("soon") is None
    assert store.resolve_message_time("TODAY", "8:44 AM", TODAY) == "2026-10-22T08:44"
    assert store.resolve_message_time("OCT 7", "", TODAY) == "2026-10-07"
    assert store.resolve_message_time("", "8:44 AM", TODAY) == ""


def test_list_labels_resolve_against_now():
    now = datetime(2026, 10, 22, 14, 30)
    assert store.resolve_list_time("12:59 PM", now) == "2026-10-22T12:59"
    # A clock later than now cannot be today: LinkedIn means yesterday evening.
    assert store.resolve_list_time("11:15 PM", now) == "2026-10-21T23:15"
    assert store.resolve_list_time("Oct 12", now) == "2026-10-12"
    assert store.resolve_list_time("Yesterday", now) == "2026-10-21"
    assert store.resolve_list_time("whenever", now) == ""


def test_stale_after_ten_minutes():
    assert store.is_stale(None, NOW)
    assert store.is_stale(0, NOW)
    assert not store.is_stale(NOW - 599, NOW)
    assert store.is_stale(NOW - 601, NOW)


# ---------------------------------------------------------------------------
# Inbox list text
# ---------------------------------------------------------------------------


def test_parse_inbox_list_from_a_real_page():
    rows = store.parse_inbox_list(FIXTURE.read_text(encoding="utf-8"))
    assert [(row.name, row.label) for row in rows] == [
        ("Dana Lee", "11:42 AM"),
        ("Sam Rivera Kolli", "11:39 AM"),
    ]
    assert rows[0].preview == "Dana: May i know your current work authorization in USA?"
    assert rows[1].who == "Sam Rivera Kolli and Jordan Example"


LIST_TEXT = """Messaging
Search messages
Focused
Conversation List
Status is online
Avery Stone
Oct 12
Oct 12

You: Thanks for the details

. Press return to go to conversation details
Open the options list in your conversation with Jordan Example and Avery Stone
Morgan Lane
Wed
Wed

Thanks: see you then

. Press return to go to conversation details
Open the options list in your conversation with Jordan Example and Morgan Lane
Jamie Fox
Sent an attachment
. Press return to go to conversation details
Open the options list in your conversation with Jordan Example and Jamie Fox
Load more conversations
Avery Stone
Status is online
Active now
Open the options list in your conversation with Avery Stone and Jordan Example
"""


def test_parse_inbox_list_is_best_effort():
    rows = store.parse_inbox_list(LIST_TEXT)
    # Jamie Fox has no time label, so it is skipped; the open thread after
    # "Load more conversations" never becomes a row.
    assert [row.name for row in rows] == ["Avery Stone", "Morgan Lane"]
    assert rows[0].label == "Oct 12"
    assert rows[1].label == "Wed"
    assert store.parse_inbox_list("") == []
    assert store.parse_inbox_list("nothing useful here") == []


def test_inbox_list_without_load_more_ignores_the_open_thread_header():
    text = LIST_TEXT.split("Load more conversations")[0] + (
        "Avery Stone\nStatus is online\nActive now\n"
        "Open the options list in your conversation with Avery Stone and Jordan Example\n"
    )
    assert [row.name for row in store.parse_inbox_list(text)] == [
        "Avery Stone",
        "Morgan Lane",
    ]


@pytest.mark.parametrize(
    ("preview", "participant", "expected"),
    [
        ("You: I am interested", "Mohana Priya G", ("us", "I am interested")),
        ("Prem: If not, let me know", "Prem Kumar", ("them", "If not, let me know")),
        ("Sagar Reddy: Call me", "Sagar Reddy Kolli", ("them", "Call me")),
        ("Jordan: Yes please", "Prem Kumar", ("us", "Yes please")),
        ("Anitha: W2 only?", "Anitha .", ("them", "W2 only?")),
        # A colon inside a message is not a sender label.
        ("Thanks: see you then", "Morgan Lane", ("unknown", "Thanks: see you then")),
        ("Sent an attachment", "Jamie Fox", ("unknown", "Sent an attachment")),
        ("", "Jamie Fox", ("unknown", "")),
    ],
)
def test_classify_preview(preview, participant, expected):
    assert store.classify_preview(preview, participant, ["Jordan Example"]) == expected


def test_rows_match_conversations_by_name_then_by_options_text():
    rows = store.parse_inbox_list(FIXTURE.read_text(encoding="utf-8"))
    conversations = [
        {"thread_id": "t-sam", "participant": "Sam Rivera Kolli"},
        {"thread_id": "t-dana", "participant": "Dana Lee"},
        {"thread_id": "t-none", "participant": "Conversation"},
        {"thread_id": "t-missing", "participant": "Nobody Here"},
    ]
    matched = store.match_rows(conversations, rows)
    assert matched["t-sam"].label == "11:39 AM"
    assert matched["t-dana"].label == "11:42 AM"
    assert set(matched) == {"t-sam", "t-dana"}


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


def test_listing_orders_by_the_latest_sync_then_position(db):
    store.upsert_listing(db, [listing("a", "Ann"), listing("b", "Ben")], NOW - 100)
    store.upsert_listing(db, [listing("c", "Cal"), listing("a", "Ann")], NOW)
    ordered = [t["thread_id"] for t in store.list_threads(db)]
    # c and a were in the newest list (position 0 and 1); b was not listed again.
    assert ordered == ["c", "a", "b"]
    assert store.latest_listing_time(db) == NOW


def test_listing_keeps_details_derived_from_fetched_messages(db):
    store.upsert_listing(db, [listing("a", "Ann", "Hello there")], NOW - 100)
    store.store_conversation(
        db,
        "a",
        participant="Ann",
        messages=[message("Ann", "Hello there. Are you free for a call?")],
        raw_text="",
        last_sender="them",
        needs_reply=True,
        is_recruiter=True,
        now=NOW - 90,
    )
    # The preview is unchanged since the messages were fetched: keep the
    # more precise values derived from the full thread.
    store.upsert_listing(
        db,
        [listing("a", "Ann", "Hello there", sender="unknown", needs_reply=False)],
        NOW,
    )
    thread = store.get_thread(db, "a")
    assert thread is not None
    # The list signature changed (sender unknown now), so the list wins.
    assert thread["last_sender"] == "unknown"
    store.upsert_listing(db, [listing("a", "Ann", "Hello there")], NOW + 10)
    store.store_conversation(
        db,
        "a",
        participant="Ann",
        messages=[message("Ann", "Hello there. Are you free for a call?")],
        raw_text="",
        last_sender="them",
        needs_reply=True,
        is_recruiter=True,
        now=NOW + 20,
    )
    store.upsert_listing(db, [listing("a", "Ann", "Hello there")], NOW + 30)
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert thread["needs_reply"] and thread["is_recruiter"]
    assert thread["last_sender"] == "them"


def test_listing_never_replaces_a_name_with_the_placeholder(db):
    store.upsert_listing(db, [listing("a", "Ann Lee")], NOW - 5)
    store.upsert_listing(db, [listing("a", "Conversation")], NOW)
    thread = store.get_thread(db, "a")
    assert thread is not None and thread["participant"] == "Ann Lee"


def test_needs_reply_only_applies_when_they_wrote_last(db):
    store.upsert_listing(db, [listing("a", "Ann", sender="us", needs_reply=True)], NOW)
    thread = store.get_thread(db, "a")
    assert thread is not None and thread["needs_reply"] is False


def test_search_matches_names_previews_and_message_text(db):
    store.upsert_listing(
        db,
        [listing("a", "Ann Lee", "Hello"), listing("b", "Ben Ray", "100% remote_role")],
        NOW,
    )
    store.store_conversation(
        db,
        "a",
        participant="Ann Lee",
        messages=[message("Ann Lee", "We use Terraform and Kubernetes")],
        raw_text="",
        last_sender="them",
        needs_reply=True,
        is_recruiter=False,
        now=NOW,
    )

    def found(q):
        return [t["thread_id"] for t in store.list_threads(db, q=q)]

    assert found("ann") == ["a"]
    assert found("terraform") == ["a"]
    assert found("remote_role") == ["b"]
    assert found("100%") == ["b"]
    assert found("%") == ["b"], "wildcards in the search text are literal"
    assert found("zzz") == []
    assert found("") == ["a", "b"]


def test_unread_until_the_thread_is_opened(db):
    store.upsert_listing(db, [listing("a", "Ann")], NOW)
    thread = store.get_thread(db, "a")
    assert thread is not None and store.is_unread(thread)
    store.mark_read(db, "a")
    thread = store.get_thread(db, "a")
    assert thread is not None and not store.is_unread(thread)
    store.upsert_listing(db, [listing("a", "Ann", "A newer message")], NOW + 5)
    thread = store.get_thread(db, "a")
    assert thread is not None and store.is_unread(thread)
    # Your own latest message is never unread.
    store.upsert_listing(db, [listing("a", "Ann", "Thanks", sender="us")], NOW + 10)
    thread = store.get_thread(db, "a")
    assert thread is not None and not store.is_unread(thread)


def test_a_thread_opened_before_it_was_listed_counts_as_read(db):
    store.store_conversation(
        db,
        "late",
        participant="Late Lee",
        messages=[message("Late Lee", "Hi")],
        raw_text="",
        last_sender="them",
        needs_reply=True,
        is_recruiter=False,
        now=NOW,
    )
    store.mark_read(db, "late")
    store.upsert_listing(db, [listing("late", "Late Lee", "Hi")], NOW + 5)
    thread = store.get_thread(db, "late")
    assert thread is not None and not store.is_unread(thread)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def fetch(db, thread_id="a", *, messages, now=NOW, **extra):
    options = {
        "participant": "Ann",
        "raw_text": "",
        "last_sender": "them",
        "needs_reply": True,
        "is_recruiter": False,
        "now": now,
        **extra,
    }
    return store.store_conversation(db, thread_id, messages=messages, **options)


def test_repeating_a_fetch_never_duplicates_messages(db):
    batch = [
        message("Ann", "Hello", time="9:00 AM"),
        message("Jordan Example", "Hi Ann", own=True, time="9:05 AM"),
        message("Ann", "Call me", time="9:10 AM"),
    ]
    for offset in range(3):
        fetch(db, messages=batch, now=NOW + offset)
    assert texts(db, "a") == ["Hello", "Hi Ann", "Call me"]
    thread = store.get_thread(db, "a")
    assert thread is not None and thread["message_count"] == 3


def test_identical_messages_in_one_fetch_are_both_kept(db):
    batch = [
        message("Ann", "Thanks!", time="9:00 AM"),
        message("Ann", "Thanks!", time="9:00 AM"),
    ]
    fetch(db, messages=batch)
    fetch(db, messages=batch)
    assert texts(db, "a") == ["Thanks!", "Thanks!"]


def test_a_fetch_replaces_what_linkedin_no_longer_shows(db):
    fetch(db, messages=[message("Ann", "old one"), message("Ann", "old two")])
    fetch(db, messages=[message("Ann", "old two"), message("Ann", "brand new")])
    assert texts(db, "a") == ["old two", "brand new"]


def test_a_fetch_without_parsed_messages_keeps_the_cache(db):
    fetch(db, messages=[message("Ann", "keep me")])
    kept = fetch(db, messages=[], raw_text="unparseable page text")
    assert kept is False
    assert texts(db, "a") == ["keep me"]
    thread = store.get_thread(db, "a")
    assert thread is not None and thread["raw_text"] == "unparseable page text"


def test_a_thread_with_only_raw_text_is_stored(db):
    fetch(
        db,
        "raw",
        messages=[],
        raw_text="Some page text",
        last_sender="unknown",
        needs_reply=False,
    )
    thread = store.get_thread(db, "raw")
    assert thread is not None
    assert thread["raw_text"] == "Some page text" and thread["message_count"] == 0


def test_message_dates_become_absolute(db):
    fetch(
        db,
        messages=[
            message("Ann", "first", date_label="OCT 7", time="7:40 PM"),
            message("Ann", "second", date_label="TODAY", time="8:44 AM"),
        ],
    )
    rows = store.get_messages(db, "a")
    assert [r["sent_on"] for r in rows] == [
        "2026-10-07",
        datetime.fromtimestamp(NOW).date().isoformat(),
    ]
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert thread["last_message_at"].endswith("T08:44")


def test_snippet_falls_back_to_the_last_cached_message(db):
    fetch(db, messages=[message("Ann", "Are you open to a role?\n\nPlease reply.")])
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert thread["snippet"] == "Are you open to a role? Please reply."


def test_a_sent_reply_is_appended_at_once_and_reconciled_on_the_next_fetch(db):
    fetch(db, messages=[message("Ann", "Are you open to a role?", time="9:00 AM")])
    moment = datetime(2026, 10, 22, 9, 30)
    store.append_local_message(
        db,
        "a",
        text="Yes, tell me more.",
        sender="Jordan Example",
        participant="Ann",
        moment=moment,
        now=NOW,
        send_id="send-1",
    )
    rows = store.get_messages(db, "a")
    assert [(r["text"], r["own"], r["source"]) for r in rows] == [
        ("Are you open to a role?", False, "linkedin"),
        ("Yes, tell me more.", True, "aira"),
    ]
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert thread["last_sender"] == "us" and not thread["needs_reply"]
    assert thread["snippet"] == "Yes, tell me more." and thread["message_count"] == 2

    # LinkedIn has not listed the reply yet: it stays.
    fetch(
        db,
        messages=[message("Ann", "Are you open to a role?", time="9:00 AM")],
        now=NOW + 5,
    )
    assert texts(db, "a") == ["Are you open to a role?", "Yes, tell me more."]

    # Once LinkedIn shows it, the local copy gives way: no duplicate.
    shown = [
        message("Ann", "Are you open to a role?", time="9:00 AM"),
        message("Jordan Example", "Yes,  tell me more.", own=True, time="9:30 AM"),
    ]
    fetch(db, messages=shown, last_sender="us", needs_reply=False, now=NOW + 10)
    rows = store.get_messages(db, "a")
    assert [(r["text"], r["source"]) for r in rows] == [
        ("Are you open to a role?", "linkedin"),
        ("Yes,  tell me more.", "linkedin"),
    ]


def test_each_shown_reply_accounts_for_one_local_reply(db):
    fetch(db, messages=[message("Ann", "Hi")])
    for index in range(2):
        store.append_local_message(
            db,
            "a",
            text="Thanks!",
            sender="Jordan",
            participant="Ann",
            moment=datetime(2026, 10, 22, 9, 30 + index),
            now=NOW + index,
            send_id=f"s{index}",
        )
    fetch(
        db,
        messages=[message("Ann", "Hi"), message("Jordan", "Thanks!", own=True)],
        last_sender="us",
        needs_reply=False,
        now=NOW + 10,
    )
    assert texts(db, "a") == ["Hi", "Thanks!", "Thanks!"]
    assert [r["source"] for r in store.get_messages(db, "a")] == [
        "linkedin",
        "linkedin",
        "aira",
    ]


def test_an_unconfirmed_local_reply_expires(db):
    fetch(db, messages=[message("Ann", "Hi")])
    store.append_local_message(
        db,
        "a",
        text="Never shown",
        sender="Jordan",
        participant="Ann",
        moment=datetime(2026, 10, 22, 9, 30),
        now=NOW,
        send_id="s1",
    )
    fetch(
        db,
        messages=[message("Ann", "Hi")],
        now=NOW + store.LOCAL_MESSAGE_TTL_SECONDS + 1,
    )
    assert texts(db, "a") == ["Hi"]


def test_a_reply_to_an_unlisted_thread_creates_it(db):
    store.append_local_message(
        db,
        "fresh",
        text="Hello!",
        sender="You",
        participant="New Person",
        moment=datetime(2026, 10, 22, 9, 30),
        now=NOW,
        send_id="s1",
    )
    thread = store.get_thread(db, "fresh")
    assert thread is not None
    assert thread["participant"] == "New Person" and thread["message_count"] == 1
    assert not store.is_unread(thread)
    store.append_local_message(
        db,
        "fresh",
        text="   ",
        sender="You",
        participant="",
        moment=datetime(2026, 10, 22, 9, 31),
        now=NOW,
        send_id="s2",
    )
    assert texts(db, "fresh") == ["Hello!"]


def test_message_history_is_capped(db):
    batch = [
        message("Ann", f"message {i}", time="9:00 AM")
        for i in range(store.MAX_MESSAGES_PER_THREAD + 25)
    ]
    fetch(db, messages=batch)
    rows = texts(db, "a")
    assert len(rows) == store.MAX_MESSAGES_PER_THREAD
    assert rows[-1] == f"message {store.MAX_MESSAGES_PER_THREAD + 24}"


# ---------------------------------------------------------------------------
# Sync runs and the migration
# ---------------------------------------------------------------------------


def test_sync_run_ledger(db):
    first = store.start_sync_run(db, "manual", NOW)
    store.update_sync_run(db, first, phase="threads", total=10)
    store.update_sync_run(db, first, done=4, failed=1)
    run = store.latest_sync_run(db)
    assert run is not None
    assert (run["phase"], run["total"], run["done"], run["failed"]) == (
        "threads",
        10,
        4,
        1,
    )
    assert run["status"] == "running" and run["finished_at"] is None

    # A new run means the earlier one died: it is closed as interrupted.
    second = store.start_sync_run(db, "tab", NOW + 60)
    assert second > first
    store.finish_sync_run(db, second, status="ok", error="", now=NOW + 90)
    rows = {r["run_id"]: r for r in db.execute("SELECT * FROM inbox_sync_runs")}
    assert rows[first]["status"] == "interrupted"
    latest = store.latest_sync_run(db)
    assert latest is not None
    assert latest["run_id"] == second and latest["status"] == "ok"
    assert latest["finished_at"] == NOW + 90 and latest["phase"] == "done"


def test_the_migration_is_additive_and_idempotent():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    assert schema.migrate(connection) == list(range(1, schema.LATEST_VERSION + 1))
    assert schema.migrate(connection) == []
    names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"inbox_threads", "inbox_messages", "inbox_sync_runs"} <= names

    # An older database upgrades in place and keeps its rows.
    connection.execute(
        "INSERT INTO inbox_threads (thread_id, participant) VALUES ('keep', 'Kept')"
    )
    version = next(m.version for m in schema.MIGRATIONS if m.name == "inbox_cache")
    connection.execute("DELETE FROM schema_migrations WHERE version >= ?", (version,))
    assert schema.migrate(connection) == list(range(version, schema.LATEST_VERSION + 1))
    kept = connection.execute(
        "SELECT participant FROM inbox_threads WHERE thread_id = 'keep'"
    ).fetchone()
    assert kept["participant"] == "Kept"
    store.ensure_schema(connection)
    store.ensure_schema(connection)


# ---------------------------------------------------------------------------
# Reconciling replies, unreadable list rows, retention, search
# ---------------------------------------------------------------------------


def append(db, text, *, now=NOW, send_id="s1", moment=None):
    store.append_local_message(
        db,
        "a",
        text=text,
        sender="Jordan Example",
        participant="Ann",
        moment=moment or datetime(2026, 10, 22, 9, 30),
        now=now,
        send_id=send_id,
    )


def test_an_older_identical_reply_does_not_stand_in_for_the_new_one(db):
    reply = "Thanks, that works for me."
    shown = [
        message("Ann", "Can you do Friday?", date_label="Mon", time="9:00 AM"),
        message("Jordan Example", reply, own=True, date_label="Mon", time="9:05 AM"),
    ]
    fetch(db, messages=shown, last_sender="us", needs_reply=False)
    append(db, reply)
    # LinkedIn has not listed the new copy: the fetch still shows only the old one.
    fetch(db, messages=shown, last_sender="us", needs_reply=False, now=NOW + 5)
    assert texts(db, "a") == ["Can you do Friday?", reply, reply]
    assert [r["source"] for r in store.get_messages(db, "a")][-1] == "aira"
    # Once a second copy is listed the local one gives way.
    both = [*shown, message("Jordan Example", reply, own=True, time="9:30 AM")]
    fetch(db, messages=both, last_sender="us", needs_reply=False, now=NOW + 10)
    assert [r["source"] for r in store.get_messages(db, "a")] == ["linkedin"] * 3


def test_an_old_copy_listed_for_the_first_time_is_not_a_delivery(db):
    reply = "Thanks!"
    append(db, reply)
    old = [
        message("Ann", "Hello", date_label="Oct 1", time="9:00 AM"),
        message("Jordan Example", reply, own=True, date_label="Oct 1", time="9:05 AM"),
    ]
    # No fetch came before: the date still tells last month's "Thanks!" apart.
    fetch(db, messages=old, last_sender="us", needs_reply=False, now=NOW + 5)
    assert [r["source"] for r in store.get_messages(db, "a")] == [
        "linkedin",
        "linkedin",
        "aira",
    ]


def test_a_fetch_that_started_before_a_send_does_not_undo_it(db):
    fetch(db, messages=[message("Ann", "Call at 5?", time="9:00 AM")])
    append(db, "Sure, call at 5")
    # The fetch read LinkedIn before the send and is stored after it.
    fetch(db, messages=[message("Ann", "Call at 5?", time="9:00 AM")], now=NOW + 3)
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert thread["last_sender"] == "us" and thread["needs_reply"] is False
    assert thread["snippet"] == "Sure, call at 5"
    assert texts(db, "a") == ["Call at 5?", "Sure, call at 5"]


def test_an_unreadable_list_row_keeps_what_was_known(db):
    store.upsert_listing(db, [listing("a", "Ann", "Can you call me?")], NOW - 100)
    fetch(db, messages=[message("Ann", "Can you call me?")], now=NOW - 90)
    store.mark_read(db, "a")
    store.upsert_listing(
        db,
        [listing("a", "Ann", "", label="", sender="unknown", needs_reply=False)],
        NOW,
    )
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert thread["snippet"] == "Can you call me?"
    assert thread["last_sender"] == "them" and thread["needs_reply"] is True
    assert not store.is_unread(thread)
    # The row says nothing about whether the thread changed: never "up to date".
    assert thread["signature"] == ""
    assert store.confirmed_at(thread) == NOW - 90
    # A readable row afterwards restores the fingerprint and the unread logic.
    store.upsert_listing(db, [listing("a", "Ann", "Can you call me?")], NOW + 5)
    thread = store.get_thread(db, "a")
    assert thread is not None and thread["signature"]
    assert not store.is_unread(thread)


def test_a_row_with_no_preview_has_no_fingerprint():
    assert store.signature_of("", "unknown", "") == ""
    assert store.signature_of("9:00 AM", "unknown", "") != ""
    assert store.signature_of("", "them", "Hello") != ""


def test_list_rows_fall_back_to_the_newest_cached_message(db):
    store.upsert_listing(db, [listing("a", "Ann", "", label="")], NOW - 100)
    fetch(db, messages=[message("Ann", "First"), message("Ann", "Second one")])
    thread = store.list_threads(db)[0]
    assert thread["latest_text"] == "Second one"


def test_a_list_read_confirms_a_thread_whose_preview_did_not_change(db):
    store.upsert_listing(db, [listing("a", "Ann", "Hello")], NOW - 3600)
    fetch(db, messages=[message("Ann", "Hello")], now=NOW - 3600)
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert store.is_stale(store.confirmed_at(thread), NOW)
    store.upsert_listing(db, [listing("a", "Ann", "Hello")], NOW - 30)
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert store.confirmed_at(thread) == NOW - 30
    assert not store.is_stale(store.confirmed_at(thread), NOW)
    # A changed preview means new activity: the fetch is out of date.
    store.upsert_listing(db, [listing("a", "Ann", "Hello again")], NOW - 10)
    thread = store.get_thread(db, "a")
    assert thread is not None
    assert store.confirmed_at(thread) == NOW - 3600
    assert store.confirmed_at(None) == 0


def test_a_failed_sync_does_not_count_as_a_fresh_copy(db):
    store.upsert_listing(db, [listing("a", "Ann", "Hello")], NOW - 7200)
    assert store.last_synced_at(db) == NOW - 7200
    run = store.start_sync_run(db, "manual", NOW - 10)
    store.upsert_listing(db, [listing("a", "Ann", "Hello")], NOW - 8)
    assert store.last_synced_at(db) == NOW - 8
    store.finish_sync_run(db, run, status="error", error="timed out", now=NOW - 5)
    # The list that run saved is inside its window and is not proof of freshness.
    assert store.last_synced_at(db) == 0
    run = store.start_sync_run(db, "manual", NOW - 4)
    store.finish_sync_run(db, run, status="ok", error="", now=NOW - 2)
    assert store.last_synced_at(db) == NOW - 2


def test_a_short_name_does_not_claim_a_longer_name_row():
    rows = [
        store.InboxRow(
            "Joanne Smith", "Jordan and Joanne Smith", "9:05 AM", "Joanne: hi"
        ),
        store.InboxRow("Ann Lee", "Jordan and Ann Lee", "9:00 AM", "Ann: hello"),
    ]
    matched = store.match_rows([{"thread_id": "A", "participant": "Ann"}], rows)
    assert matched["A"].name == "Ann Lee"
    assert store.match_rows([{"thread_id": "A", "participant": "Ann"}], rows[:1]) == {}


def test_search_ignores_case_for_non_ascii_letters(db):
    store.upsert_listing(
        db,
        [listing("a", "José Álvarez", "Hola"), listing("b", "Émilie", "Salut")],
        NOW,
    )
    assert [t["thread_id"] for t in store.list_threads(db, q="josé álvarez")] == ["a"]
    assert [t["thread_id"] for t in store.list_threads(db, q="ÉMILIE")] == ["b"]
    assert [t["thread_id"] for t in store.list_threads(db, q="émilie")] == ["b"]


def test_prune_drops_idle_threads_and_expired_local_replies(db):
    store.upsert_listing(db, [listing("old", "Old Person", "Hi")], NOW - 100 * 86400)
    fetch(db, "old", messages=[message("Old Person", "Hi")], now=NOW - 100 * 86400)
    store.upsert_listing(db, [listing("a", "Ann", "Hi")], NOW)
    fetch(db, messages=[message("Ann", "Hi")], now=NOW)
    append(db, "Unlisted", now=NOW - store.LOCAL_MESSAGE_TTL_SECONDS - 10)
    removed = store.prune(db, NOW)
    assert removed == {"threads": 1, "expired_replies": 1}
    assert store.get_thread(db, "old") is None
    assert store.get_messages(db, "old") == []
    assert texts(db, "a") == ["Hi"]
    thread = store.get_thread(db, "a")
    assert thread is not None and thread["message_count"] == 1


def test_purge_all_deletes_the_whole_copy(db):
    store.upsert_listing(db, [listing("a", "Ann", "Hi")], NOW)
    fetch(db, messages=[message("Ann", "Hi"), message("Ann", "There")])
    store.start_sync_run(db, "manual", NOW)
    assert store.purge_all(db) == {"threads": 1, "messages": 2}
    assert store.list_threads(db) == [] and store.latest_sync_run(db) is None


def test_the_migration_ddl_is_frozen_in_schema():
    connection = sqlite3.connect(":memory:")
    schema.inbox_cache_v1(connection)
    schema.inbox_cache_v1(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(inbox_threads)")}
    assert {"thread_id", "signature", "fetched_signature", "read_signature"} <= columns
