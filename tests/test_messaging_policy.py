from datetime import datetime, timedelta, timezone

import pytest

from aira.messaging import (
    evaluate_auto_reply,
    latest_message_is_ours,
    reply_hash,
    validate_manual_reply,
    validate_mode,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
CONVERSATION = (
    "Priya Recruiter\nHi Jordan, I came across your profile and think you'd be a great fit for a Cloud Architect "
    "role on our platform team. Would you be open to a quick chat this week?\n10:42 AM"
)
GOOD_REPLY = "Hi Priya, thanks for reaching out. I'd be glad to learn more about the Cloud Architect role."


def decide(**overrides):
    params = dict(
        conversation=CONVERSATION,
        reply=GOOD_REPLY,
        needs_reply=True,
        now=NOW,
        speaker="them",
    )
    params.update(overrides)
    return evaluate_auto_reply(**params)


def test_clean_reply_is_sent():
    decision = decide()
    assert decision.action == "send" and decision.allowed and decision.reasons == ()


def test_no_reply_needed_is_skipped():
    assert decide(needs_reply=False).action == "skip"


def test_our_reply_still_newest_is_skipped():
    conversation = CONVERSATION + "\nYou\n" + GOOD_REPLY + "\n10:50 AM · Seen"
    decision = decide(conversation=conversation, last_sent_reply=GOOD_REPLY)
    assert decision.action == "skip"


def test_new_inbound_after_our_reply_is_answered():
    conversation = (
        CONVERSATION
        + "\nYou\n"
        + GOOD_REPLY
        + "\nPriya Recruiter\n"
        + ("Great! I will send over a calendar invite for Thursday afternoon. " * 4)
    )
    assert latest_message_is_ours(conversation, GOOD_REPLY) is False
    assert (
        decide(conversation=conversation, last_sent_reply=GOOD_REPLY).action == "send"
    )


@pytest.mark.parametrize(
    ("overrides", "reason_fragment"),
    [
        ({"paused": True}, "paused"),
        ({"sent_today": 20, "daily_limit": 20}, "Daily automatic reply limit"),
        ({"daily_limit": 0}, "Daily automatic reply limit"),
        ({"last_sent_at": NOW - timedelta(hours=1)}, "within the last 6 hours"),
    ],
)
def test_account_protection_holds(overrides, reason_fragment):
    decision = decide(**overrides)
    assert decision.action == "hold"
    assert any(reason_fragment in reason for reason in decision.reasons)


def test_cooldown_expires():
    assert decide(last_sent_at=NOW - timedelta(hours=7)).action == "send"


@pytest.mark.parametrize(
    ("conversation_suffix", "reply", "reason_fragment"),
    [
        ("\nWhat are your salary expectations?", GOOD_REPLY, "compensation"),
        ("\nDo you require visa sponsorship?", GOOD_REPLY, "work authorization"),
        (
            "\nCan you share your date of birth for the form?",
            GOOD_REPLY,
            "personal identifiers",
        ),
        ("\nWe'd like to send you an offer letter.", GOOD_REPLY, "offer or contract"),
        ("\nAre you open to relocation?", GOOD_REPLY, "relocation"),
        (
            "",
            "Thanks! My expected rate is $85 per hour and I can start soon.",
            "money amount",
        ),
        ("", "Thanks, here is my calendar: https://cal.example.com/me", "link"),
        (
            "",
            "Thanks, call me at +1 555 010 2030 any time this week.",
            "contact details",
        ),
        ("", "Thanks for reaching out, [Your Name] would love to chat.", "placeholder"),
        ("", "Thanks!", "too short"),
    ],
)
def test_sensitive_or_risky_content_is_held(
    conversation_suffix, reply, reason_fragment
):
    decision = decide(conversation=CONVERSATION + conversation_suffix, reply=reply)
    assert decision.action == "hold"
    assert any(reason_fragment in reason for reason in decision.reasons), (
        decision.reasons
    )


def test_mode_and_manual_reply_validation():
    assert validate_mode("AUTO") == "auto"
    with pytest.raises(ValueError):
        validate_mode("always")
    assert validate_manual_reply("  Thanks!  ") == "Thanks!"
    with pytest.raises(ValueError, match="placeholder"):
        validate_manual_reply("Hi [Recruiter Name], thanks")
    with pytest.raises(ValueError):
        validate_manual_reply("   ")


def test_reply_hash_ignores_whitespace_and_case():
    assert reply_hash("t1", "Hello  there") == reply_hash("t1", "hello there")
    assert reply_hash("t1", "Hello") != reply_hash("t2", "Hello")


# ---------------------------------------------------------------------------
# Who wrote last
# ---------------------------------------------------------------------------
from aira.messaging import inbound_asks_question, last_speaker  # noqa: E402

THREAD = (
    "Marco Diaz\nRecruiter at Contoso\n"
    "Hello Jordan, we have a contract Solution Architect opening. Are you available?\n9:15 AM"
)


def test_recruiter_wrote_last():
    assert last_speaker(THREAD, "Marco Diaz", ["Jordan Example"]) == "them"
    assert inbound_asks_question(THREAD, "Marco Diaz") is True


def test_user_wrote_last_by_name_or_you_label():
    by_name = THREAD + "\nJordan Example\nThanks Marco, happy to chat.\n9:20 AM"
    by_label = THREAD + "\nYou\nThanks Marco, happy to chat.\n9:20 AM"
    assert last_speaker(by_name, "Marco Diaz", ["Jordan Example"]) == "us"
    assert last_speaker(by_label, "Marco Diaz", []) == "us"


def test_first_name_in_a_greeting_is_not_a_sender_label():
    # "Hello Jordan" inside Marco's message must not count as the user writing.
    assert last_speaker(THREAD, "Marco Diaz", ["Jordan", "Jordan Example"]) == "them"


def test_unknown_own_name_cannot_rule_out_an_unlabeled_reply():
    assert last_speaker(THREAD, "Marco Diaz", []) is None
    assert last_speaker(THREAD, "LinkedIn contact", ["Jordan Example"]) is None


def test_question_check_only_reads_the_latest_inbound_group():
    answered = (
        THREAD
        + "\nJordan Example\nYes. Does Tuesday work?\nMarco Diaz\nTuesday works, talk then.\n9:30 AM"
    )
    assert inbound_asks_question(answered, "Marco Diaz") is False


def test_unknown_speaker_is_held_not_guessed():
    decision = decide(speaker=None)
    assert decision.action == "hold"
    assert "full name" in decision.reasons[0]


def test_speaker_us_is_skipped_even_if_model_wants_to_reply():
    assert decide(speaker="us").action == "skip"


# ---------------------------------------------------------------------------
# Real LinkedIn page format (names changed)
# ---------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

from aira.messaging import (  # noqa: E402
    clean_person_label,
    latest_inbound_text,
    parse_thread,
    thread_participant,
    thread_speaker,
)

PAGE = (Path(__file__).parent / "fixtures" / "linkedin_thread_page.txt").read_text(
    encoding="utf-8"
)


def test_parser_keeps_only_the_active_thread():
    messages = parse_thread(PAGE)
    assert [m.sender for m in messages] == [
        "Sam Rivera Kolli",
        "Jordan Example",
        "Sam Rivera Kolli",
        "Sam Rivera Kolli",
    ]
    assert messages[0].date == "Aug 7, 2025"
    assert messages[-1].text == "Dropped an email, please check and revert me back"
    assert all("Dana" not in m.text for m in messages), "inbox list must not leak in"
    assert all("Reply to conversation" not in m.text for m in messages)


def test_profile_labels_become_names():
    assert (
        clean_person_label("Open Sam Rivera Kolli\u2019s profile") == "Sam Rivera Kolli"
    )
    assert clean_person_label("View Jordan's profile") == "Jordan"


def test_participant_speaker_and_inbound_from_real_page():
    messages = parse_thread(PAGE)
    participant = thread_participant(
        messages, ["Jordan Example"], "Open Sam Rivera Kolli\u2019s profile"
    )
    assert participant == "Sam Rivera Kolli"
    assert thread_speaker(messages, participant, ["Jordan Example"]) == "them"
    assert thread_speaker(messages, participant, []) == "them"
    inbound = latest_inbound_text(messages, ["Jordan Example"], participant)
    assert inbound == (
        "Can we connect today at 10 EST?\nDropped an email, please check and revert me back"
    )


def test_own_earlier_visa_mention_does_not_hold_the_reply():
    messages = parse_thread(PAGE)
    inbound = latest_inbound_text(messages, ["Jordan Example"], "Sam Rivera Kolli")
    decision = evaluate_auto_reply(
        conversation=PAGE,
        reply="Thanks Sam, I saw your email and will reply there shortly. Jordan",
        needs_reply=True,
        speaker="them",
        inbound_text=inbound,
        now=NOW,
    )
    assert decision.action == "send", decision.reasons


def test_unparseable_text_returns_no_messages():
    assert parse_thread("Messaging\nSearch messages\nNo thread here") == []


def test_requests_without_question_marks_need_a_reply():
    from aira.messaging import inbound_needs_reply

    assert (
        inbound_needs_reply("Dropped an email, please check and revert me back") is True
    )
    assert inbound_needs_reply("Can we connect today at 10 EST") is True
    assert inbound_needs_reply("Thanks!") is False
    assert inbound_needs_reply("Ok\nThank you so much") is False
    assert inbound_needs_reply("👍") is False
    assert inbound_needs_reply("") is False
