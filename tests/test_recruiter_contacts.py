"""Recruiter contacts: Find recruiters (review then save), the contact story, lists, and careful outreach.

Nothing here reaches LinkedIn, a model service or a mail server on the internet: LinkedIn is the messenger tests'
stand-in worker, the model is a stand-in function, and email goes to the local SMTP stub.
"""

# ruff: noqa: F811  (the imported fixtures are used by name as test arguments)
from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from email import message_from_string

import pytest
from smtp_stub import SmtpStub
from test_careers import api, env, give_profile, stub  # noqa: F401
from test_inbox_messenger import http_client, http_wait, sign_up, worker  # noqa: F401

import auto_apply_assistant as app
from aira import crm, recruiter_contacts as rc, recruiter_outreach as ro
from aira.email_intel import classify_email
from aira.schema import migrate
from aira.suite import kit

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- helpers


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "contacts.sqlite3")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    connection.commit()
    yield connection
    connection.close()


def add_thread(
    connection,
    thread_id: str,
    participant: str,
    *,
    raw_text: str = "",
    messages=(),
    is_recruiter: bool = True,
    last_message_at: str = "",
    snippet: str = "",
) -> None:
    connection.execute(
        """INSERT INTO inbox_threads (thread_id, participant, snippet, last_message_at, is_recruiter, raw_text, listed_at)
        VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (thread_id, participant, snippet, last_message_at, int(is_recruiter), raw_text),
    )
    for ordinal, (sender, own, sent_on, clock, text) in enumerate(messages):
        connection.execute(
            """INSERT INTO inbox_messages (thread_id, ordinal, msg_key, sender, own, sent_on, time_label, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                thread_id,
                ordinal,
                f"{thread_id}-{ordinal}",
                sender,
                int(own),
                sent_on,
                clock,
                text,
            ),
        )


def add_email(
    connection,
    message_id: str,
    *,
    subject: str,
    body: str,
    sender: str,
    sender_name: str,
    received_at: str,
    status: str = "pending",
    job_id: str | None = None,
) -> dict:
    details = classify_email(
        subject, body, sender=sender, sender_name=sender_name
    ).details.to_dict()
    connection.execute(
        """INSERT INTO email_messages (message_id, fingerprint, subject, sender_name, sender_email, received_at, details, status, body_excerpt, job_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id,
            message_id,
            subject,
            sender_name,
            sender,
            received_at,
            json.dumps(details),
            status,
            body,
            job_id,
            received_at,
        ),
    )
    return details


def add_interview(
    connection,
    interview_id: str,
    *,
    company: str,
    start_at: str,
    participants,
    job_id=None,
) -> None:
    connection.execute(
        "INSERT INTO interviews (interview_id, job_id, company, role, start_at, end_at, interview_type) VALUES (?, ?, ?, 'Solution Architect', ?, ?, 'recruiter_screen')",
        (interview_id, job_id, company, start_at, start_at),
    )
    for index, (name, role, email) in enumerate(participants):
        connection.execute(
            "INSERT INTO interview_participants (participant_id, interview_id, name, role, email) VALUES (?, ?, ?, ?, ?)",
            (f"{interview_id}-p{index}", interview_id, name, role, email),
        )


def add_job(
    connection,
    job_id: str,
    title: str,
    company: str,
    *,
    stage: str = "applied",
    applied_at: str | None = "2026-10-01T10:00:00+00:00",
) -> None:
    connection.execute(
        "INSERT INTO jobs (job_id, title, company, job_url, stage, applied_at, external_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            title,
            company,
            f"https://example.com/{job_id}",
            stage,
            applied_at,
            job_id,
        ),
    )


MARCO_PAGE = (
    "Marco Diaz\n(He/Him)\nTechnical Recruiter at Contoso Labs\n"
    "Marco Diaz sent the following message at 9:15 AM\nMarco Diaz   9:15 AM\n\n"
    "Hello Jordan, we have a contract Solution Architect opening. Are you available?\n"
)


def seed_people(connection) -> None:
    """Marco (LinkedIn headline + email), Priya (introduces herself, no email anywhere), a friend (not recruiting),
    the user themself, and an interview with a recruiter and an engineer."""
    add_thread(
        connection,
        "t-marco",
        "Marco Diaz",
        raw_text=MARCO_PAGE,
        messages=[
            (
                "Marco Diaz",
                False,
                "2026-10-20",
                "9:15 AM",
                "Hello Jordan, we have a contract Solution Architect opening. Are you available?",
            ),
            ("Jordan Example", True, "2026-10-20", "9:40 AM", "Yes, happy to talk."),
        ],
    )
    add_thread(
        connection,
        "t-priya",
        "Priya Shah",
        messages=[
            (
                "Priya Shah",
                False,
                "2026-10-12",
                "2:00 PM",
                "Hi! I'm a senior talent acquisition partner at Fabrikam. Would you like to chat about a role?",
            )
        ],
        is_recruiter=True,
    )
    add_thread(
        connection,
        "t-friend",
        "Alex Friend",
        messages=[("Alex Friend", False, "2026-10-15", "8:00 PM", "Dinner on Friday?")],
        is_recruiter=False,
    )
    add_thread(connection, "t-me", "Jordan Example", is_recruiter=True)
    add_email(
        connection,
        "e-marco",
        subject="Solution Architect at Contoso Labs",
        body="Hi Jordan,\n\nThanks for the chat. Let's set up a call.\n\nBest regards,\nMarco Diaz\nSenior Technical Recruiter\n",
        sender="marco.diaz@contosolabs.com",
        sender_name="Marco Diaz",
        received_at="2026-10-22T16:00:00+00:00",
    )
    add_interview(
        connection,
        "i-1",
        company="Northwind",
        start_at="2026-10-25T15:00:00+00:00",
        participants=[
            ("Lena Park", "Recruiter", "lena@northwind.example"),
            ("Sam Coder", "Staff Engineer", ""),
            ("Jordan Example", "", ""),
        ],
    )
    connection.commit()


def by_name(found: dict) -> dict[str, dict]:
    return {item["name"]: item for item in found["proposals"]}


# --------------------------------------------------------------------------- reading people out of text


def test_headline_is_read_only_under_the_name_and_before_the_messages():
    assert rc.headline(MARCO_PAGE, "Marco Diaz") == (
        "Technical Recruiter",
        "Contoso Labs",
    )
    # A sentence inside a message that reads like a headline is not one.
    inside = "Marco Diaz sent the following message at 9:15 AM\nMarco Diaz\nRecruiter at Contoso\n"
    assert rc.headline(inside, "Marco Diaz") == ("", "")
    assert rc.headline("Someone Else\nRecruiter at Contoso\n", "Marco Diaz") == ("", "")


def test_introductions_signatures_and_addresses_come_from_the_text():
    assert rc.introduction(
        "Hi! I'm a senior talent acquisition partner at Fabrikam. Would you"
    ) == (
        "Senior talent acquisition partner",
        "Fabrikam",
    )
    assert rc.introduction(
        "I am the Technical Recruiter with Contoso Labs and I saw your profile"
    ) == (
        "Technical Recruiter",
        "Contoso Labs",
    )
    assert rc.introduction("I'm a big fan of Contoso") == ("", "")
    assert rc.signature_title(
        "Best,\nMarco Diaz\nSenior Technical Recruiter | Contoso Labs\n", "Marco Diaz"
    ) == (
        "Senior Technical Recruiter",
        "Contoso Labs",
    )
    assert rc.signature_title("Best,\nMarco Diaz\n+1 415 555 0100\n", "Marco Diaz") == (
        "",
        "",
    )
    assert rc.emails_in("write to jane@acme.example or noreply@acme.example.") == [
        "jane@acme.example"
    ]
    assert rc.looks_like_person("Dana Lee") and not rc.looks_like_person(
        "LinkedIn contact"
    )


# --------------------------------------------------------------------------- Find recruiters


def test_find_proposes_people_from_every_source_and_invents_nothing(db):
    seed_people(db)
    found = rc.find_proposals(db, own_names=["Jordan Example"], now=NOW)
    people = by_name(found)
    assert set(people) == {"Marco Diaz", "Priya Shah", "Lena Park", "Sam Coder"}
    marco = people["Marco Diaz"]
    # The LinkedIn conversation and the email are one person: one proposal with both sources.
    assert marco["action"] == "new" and marco["source_kinds"] == [
        "linkedin_inbox",
        "email",
    ]
    assert (marco["title"], marco["company"], marco["email"]) == (
        "Technical Recruiter",
        "Contoso Labs",
        "marco.diaz@contosolabs.com",
    )
    assert (
        marco["evidence"]["title"] == "LinkedIn headline"
        and marco["evidence"]["email"] == "Email sender"
    )
    assert marco["last_contact_at"].startswith("2026-10-2")
    link = next(s["link"] for s in marco["sources"] if s["kind"] == "linkedin_inbox")
    assert link.startswith("#/recruiters?tab=messages&thread=t-marco")
    priya = people["Priya Shah"]
    assert (priya["title"], priya["company"]) == (
        "Senior talent acquisition partner",
        "Fabrikam",
    )
    assert (
        priya["email"] == "" and priya["evidence"]["title"] == "Their message"
    )  # nothing looked up or guessed
    lena, sam = people["Lena Park"], people["Sam Coder"]
    assert (
        lena["email"] == "lena@northwind.example"
        and lena["company"] == "Northwind"
        and lena["selected"]
    )
    # An engineer on the interview is proposed, but waits for a tick.
    assert sam["looks_like_recruiter"] is False and sam["selected"] is False
    assert found["scanned"] == {"linkedin_inbox": 4, "email": 1, "interview": 3}
    assert (
        found["skipped"] == 3
    )  # the friend, the user's own thread, the user on the interview


def test_saving_creates_contacts_with_their_sources_and_never_duplicates(db):
    seed_people(db)
    found = rc.find_proposals(db, own_names=["Jordan Example"], now=NOW)
    keys = [item["key"] for item in found["proposals"] if item["selected"]]
    saved = rc.save_proposals(db, keys, own_names=["Jordan Example"], now=NOW)
    db.commit()
    assert saved["created"] == 3 and saved["updated"] == 0 and saved["failed"] == []
    marco = crm.find_duplicate(db, {"email": "marco.diaz@contosolabs.com"})
    assert (
        marco["source"] == "linkedin"
        and marco["consent_note"] == "Found in your LinkedIn inbox, email"
    )
    assert {s["kind"] for s in rc.sources_of(db, marco["recruiter_id"])} == {
        "linkedin_inbox",
        "email",
    }
    linked = db.execute(
        "SELECT recruiter_id FROM message_threads WHERE thread_id = 't-marco'"
    ).fetchone()
    assert linked["recruiter_id"] == marco["recruiter_id"]
    # Looking again finds the same people already saved: nothing to propose but Sam, who was not ticked.
    again = rc.find_proposals(db, own_names=["Jordan Example"], now=NOW)
    assert [item["name"] for item in again["proposals"]] == ["Sam Coder"] and again[
        "up_to_date"
    ] == 3
    # Saving the old keys again changes nothing.
    repeat = rc.save_proposals(db, keys, own_names=["Jordan Example"], now=NOW)
    assert repeat["created"] == 0 and repeat["stale"] == 3
    assert (
        db.execute(
            "SELECT COUNT(*) FROM recruiters WHERE deleted_at IS NULL"
        ).fetchone()[0]
        == 3
    )


def test_existing_contacts_get_updates_that_only_fill_empty_fields(db):
    seed_people(db)
    kept = crm.create_recruiter(
        db, {"name": "Marco Diaz", "company": "Contoso Labs", "title": "Talent Partner"}
    )
    lena = crm.create_recruiter(
        db, {"name": "Lena P.", "email": "lena@northwind.example"}
    )
    priya = crm.create_recruiter(db, {"name": "Priya Shah", "company": ""})
    db.commit()
    people = by_name(rc.find_proposals(db, own_names=["Jordan Example"], now=NOW))
    marco = people["Marco Diaz"]
    assert (
        marco["action"] == "update"
        and marco["match"]["how"] == "name_company"
        and marco["selected"]
    )
    assert marco["changes"] == {
        "email": "marco.diaz@contosolabs.com"
    }  # the title stays as the user wrote it
    assert people["Lena Park"]["match"]["recruiter_id"] == lena["recruiter_id"]
    assert people["Lena Park"]["match"]["how"] == "email"
    weak = people["Priya Shah"]
    assert weak["match"]["how"] == "name" and weak["selected"] is False
    rc.save_proposals(
        db, [marco["key"], weak["key"]], own_names=["Jordan Example"], now=NOW
    )
    db.commit()
    after = crm.get_recruiter(db, kept["recruiter_id"])
    assert (
        after["title"] == "Talent Partner"
        and after["email"] == "marco.diaz@contosolabs.com"
    )
    assert crm.get_recruiter(db, priya["recruiter_id"])["company"] == "Fabrikam"
    assert db.execute("SELECT COUNT(*) FROM recruiters").fetchone()[0] == 3


def test_a_name_without_company_does_not_merge_two_different_people(db):
    add_thread(
        db,
        "t-a",
        "Chris Lee",
        messages=[("Chris Lee", False, "2026-10-01", "", "I'm a recruiter at Acme.")],
    )
    add_thread(
        db,
        "t-b",
        "Chris Lee",
        messages=[("Chris Lee", False, "2026-10-02", "", "I'm a recruiter at Globex.")],
    )
    found = rc.find_proposals(db, now=NOW)
    assert sorted(item["company"] for item in found["proposals"]) == ["Acme", "Globex"]


# --------------------------------------------------------------------------- the contact story


def test_the_story_merges_messages_emails_interviews_jobs_and_notes(db):
    seed_people(db)
    add_job(db, "job-1", "Solution Architect", "Contoso Labs")
    db.execute(
        "UPDATE email_messages SET job_id = 'job-1' WHERE message_id = 'e-marco'"
    )
    found = by_name(rc.find_proposals(db, own_names=["Jordan Example"], now=NOW))
    rc.save_proposals(
        db, [found["Marco Diaz"]["key"]], own_names=["Jordan Example"], now=NOW
    )
    marco = crm.find_duplicate(db, {"email": "marco.diaz@contosolabs.com"})
    crm.add_interaction(
        db,
        marco["recruiter_id"],
        {
            "kind": "call",
            "summary": "Phone screen went well",
            "occurred_at": "2026-10-23T10:00:00+00:00",
            "direction": "inbound",
        },
    )
    db.execute(
        "UPDATE interviews SET recruiter_id = ? WHERE interview_id = 'i-1'",
        (marco["recruiter_id"],),
    )
    db.commit()
    told = rc.story(db, marco["recruiter_id"])
    kinds = [item["kind"] for item in told["items"]]
    assert told["counts"] == {
        "linkedin": 2,
        "email": 1,
        "interview": 1,
        "job": 1,
        "interaction": 1,
        "outreach": 0,
        "linkedin_draft": 0,
    }
    assert kinds[0] == "interview"  # newest first: the interview on Oct 25
    stamps = [item["at"] for item in told["items"] if item["at"]]
    assert stamps == sorted(stamps, reverse=True)
    linkedin = [item for item in told["items"] if item["kind"] == "linkedin"]
    assert {item["direction"] for item in linkedin} == {"inbound", "outbound"}
    job = next(item for item in told["items"] if item["kind"] == "job")
    assert (
        job["title"] == "Solution Architect at Contoso Labs" and job["applied"] is True
    )
    prompt = rc.summary_prompt(told["recruiter"], told["items"])
    assert "untrusted" in prompt and "Phone screen went well" in prompt
    assert rc.parse_summary(
        {
            "relationship": "Talked twice; mail me at x@y.com",
            "next_step": "Send the follow-up.",
        }
    ) == {
        "relationship": "Talked twice; mail me at",
        "next_step": "Send the follow-up.",
    }
    with pytest.raises(RuntimeError):
        rc.parse_summary({})


# --------------------------------------------------------------------------- lists


def saved_contacts(db) -> dict[str, dict]:
    seed_people(db)
    found = rc.find_proposals(db, own_names=["Jordan Example"], now=NOW)
    rc.save_proposals(
        db,
        [item["key"] for item in found["proposals"] if item["selected"]],
        own_names=["Jordan Example"],
        now=NOW,
    )
    crm.create_recruiter(db, {"name": "=HYPERLINK(evil)", "company": "Northwind"})
    db.commit()
    return {row["name"]: dict(row) for row in db.execute("SELECT * FROM recruiters")}


def names(people) -> list[str]:
    return sorted(person["name"] for person in people)


def test_list_filters(db):
    saved_contacts(db)
    add_job(db, "job-n", "Architect", "Northwind")
    add_job(db, "job-f", "Architect", "Fabrikam", stage="saved", applied_at=None)
    db.commit()
    assert names(rc.filter_people(db, {"applied_company": True}, now=NOW)) == [
        "=HYPERLINK(evil)",
        "Lena Park",
    ]
    assert names(rc.filter_people(db, {"has_email": True}, now=NOW)) == [
        "Lena Park",
        "Marco Diaz",
    ]
    assert names(rc.filter_people(db, {"source": "linkedin_inbox"}, now=NOW)) == [
        "Marco Diaz",
        "Priya Shah",
    ]
    assert names(rc.filter_people(db, {"source": "manual"}, now=NOW)) == [
        "=HYPERLINK(evil)"
    ]
    assert names(rc.filter_people(db, {"q": "fabrik"}, now=NOW)) == ["Priya Shah"]
    # Marco: you wrote last on Oct 20 at 9:40, but his email came on Oct 22, so he answered. Priya wrote last.
    assert names(rc.filter_people(db, {"no_reply_days": 5}, now=NOW)) == []
    db.execute(
        "INSERT INTO inbox_messages (thread_id, ordinal, msg_key, sender, own, sent_on, time_label, text, created_at) VALUES ('t-priya', 5, 'p5', 'Jordan Example', 1, '2026-10-13', '9:00 AM', 'Yes please!', 0)"
    )
    assert names(rc.filter_people(db, {"no_reply_days": 14}, now=NOW)) == ["Priya Shah"]
    assert names(rc.filter_people(db, {"no_reply_days": 30}, now=NOW)) == []
    for bad in (
        {"no_reply_days": 0.5},
        {"no_reply_days": 900},
        {"source": "fax"},
        "nope",
    ):
        with pytest.raises(ValueError):
            rc.validate_filters(bad)


def test_lists_are_saved_filters_with_members_and_csv(db):
    saved_contacts(db)
    made = rc.create_list(
        db, {"name": "Has email", "filters": {"has_email": True, "junk": 1}}
    )
    assert made["filters"] == {"has_email": True}
    with pytest.raises(ValueError, match="already exists"):
        rc.create_list(db, {"name": "has EMAIL", "filters": {}})
    assert [(item["name"], item["count"]) for item in rc.list_lists(db, now=NOW)] == [
        ("Has email", 2)
    ]
    assert names(rc.members(db, made["list_id"], now=NOW)) == [
        "Lena Park",
        "Marco Diaz",
    ]
    rc.update_list(db, made["list_id"], {"filters": {}})
    text = rc.members_csv(rc.members(db, made["list_id"], now=NOW))
    lines = text.splitlines()
    assert lines[0].startswith(
        "Name,Title,Company,Email,Phone,LinkedIn URL,Sources,Last contact"
    )
    assert "'=HYPERLINK(evil)" in text  # spreadsheet formulas are neutralised
    assert any("LinkedIn inbox; Email" in line for line in lines)
    assert rc.delete_list(db, made["list_id"]) is True
    with pytest.raises(LookupError):
        rc.members(db, made["list_id"])


# --------------------------------------------------------------------------- outreach rules (unit)


def test_templates_fill_details_with_fallbacks_and_refuse_unknown_ones():
    values = {"first_name": "Lena", "company": ""}
    assert (
        ro.render("Hi {first_name} at {company|your team}", values)
        == "Hi Lena at your team"
    )
    with pytest.raises(ro.MissingDetail):
        ro.render("Hi {first_name} at {company}", values)
    with pytest.raises(ValueError, match="not a detail"):
        ro.validate_template("Hi {salary}", "email text", limit=100)
    with pytest.raises(ValueError, match="bracketed"):
        ro.validate_template("Hi [Recruiter Name]", "email text", limit=100)


def test_email_plan_skips_with_reasons_and_keeps_the_daily_limit(db):
    contacts = saved_contacts(db)
    everyone = rc.create_list(db, {"name": "Everyone", "filters": {}})
    db.commit()
    plan = ro.email_plan(
        db,
        everyone["list_id"],
        subject="Hello {first_name}",
        body="Hi {first_name}, {company|your team}.\n{my_name}",
        my_name="Jordan Example",
        now=NOW,
    )
    items = {item["name"]: item for item in plan["items"]}
    assert (
        items["Priya Shah"]["status"] == "skipped"
        and items["Priya Shah"]["linkedin_only"]
    )
    assert items["Priya Shah"]["reason"].startswith("LinkedIn only")
    assert (
        items["Marco Diaz"]["status"] == "ready"
        and items["Marco Diaz"]["subject"] == "Hello Marco"
    )
    assert items["Marco Diaz"]["body"].endswith(ro.OPT_OUT_LINE)
    assert plan["ready"] == 2 and plan["linkedin_only"] == 2
    # The daily limit: one today, the other waits.
    ro.save_settings(db, {"daily_limit": 1})
    plan = ro.email_plan(
        db,
        everyone["list_id"],
        subject="Hello",
        body="Hi {first_name}",
        my_name="Jordan Example",
        now=NOW,
    )
    assert [item["status"] for item in plan["items"] if item["email"]].count(
        "later"
    ) == 1
    # Do not contact: theirs, or their address on the Leads suppression list.
    ro.add_do_not_contact(
        db, contacts["Marco Diaz"]["recruiter_id"], "Asked me to stop"
    )
    assert db.execute(
        "SELECT 1 FROM lead_suppressions WHERE key = 'email:marco.diaz@contosolabs.com'"
    ).fetchone()
    plan = ro.email_plan(
        db,
        everyone["list_id"],
        subject="Hello",
        body="Hi {first_name}",
        my_name="Jordan Example",
        now=NOW,
    )
    marco = next(item for item in plan["items"] if item["name"] == "Marco Diaz")
    assert marco["status"] == "skipped" and "do-not-contact" in marco["reason"]
    ro.remove_do_not_contact(db, contacts["Marco Diaz"]["recruiter_id"])
    assert not db.execute(
        "SELECT 1 FROM lead_suppressions WHERE key = 'email:marco.diaz@contosolabs.com'"
    ).fetchone()
    # Asked to stop in their own email: on the list by itself.
    add_email(
        db,
        "e-stop",
        subject="Re: hello",
        body="Please unsubscribe me from these.",
        sender="lena@northwind.example",
        sender_name="Lena Park",
        received_at="2026-10-27T10:00:00+00:00",
    )
    plan = ro.email_plan(
        db,
        everyone["list_id"],
        subject="Hello",
        body="Hi {first_name}",
        my_name="Jordan Example",
        now=NOW,
    )
    lena = next(item for item in plan["items"] if item["name"] == "Lena Park")
    assert lena["status"] == "skipped" and "their email" in lena["reason"]


def test_sending_goes_through_the_shared_outbox_and_stops_after_a_reply(db):
    contacts = saved_contacts(db)
    with_email = rc.create_list(
        db, {"name": "Has email", "filters": {"has_email": True}}
    )
    kit.save_email_settings(
        db,
        {
            "from_email": "jordan@example.com",
            "smtp_host": "127.0.0.1",
            "smtp_port": 2525,
            "smtp_security": "none",
        },
    )
    sent: list = []
    marco_id = contacts["Marco Diaz"]["recruiter_id"]
    with pytest.raises(ValueError, match="at least one"):
        ro.send_emails(
            db,
            with_email["list_id"],
            [],
            subject="Hi",
            body="Hi",
            my_name="Jordan",
            password="",
        )
    result = ro.send_emails(
        db,
        with_email["list_id"],
        [marco_id],
        subject="Hello {first_name}",
        body="Hi {first_name},\nJordan",
        my_name="Jordan Example",
        password="",
        sender=lambda settings, password, message: sent.append(message),
        now=NOW,
    )
    assert result["sent"] == 1 and result["queued"] == 1 and len(sent) == 1
    assert (
        sent[0]["To"] == "marco.diaz@contosolabs.com"
        and ro.OPT_OUT_LINE in sent[0].get_content()
    )
    log = ro.emails_to(db, marco_id)
    assert [row["status"] for row in log] == ["sent"]
    assert any(item["kind"] == "outreach" for item in rc.story(db, marco_id)["items"])
    later = NOW + timedelta(days=1)
    plan = ro.email_plan(
        db, with_email["list_id"], subject="Hello", body="Hi", my_name="J", now=later
    )
    marco = next(item for item in plan["items"] if item["name"] == "Marco Diaz")
    assert marco["status"] == "skipped" and marco["reason"].startswith(
        "You emailed them on"
    )
    # He replies on LinkedIn: from now on the person writes to him themselves.
    db.execute(
        "INSERT INTO inbox_messages (thread_id, ordinal, msg_key, sender, own, sent_on, time_label, text, created_at) VALUES ('t-marco', 9, 'm9', 'Marco Diaz', 0, ?, '11:00 PM', 'Thanks, let us talk', 0)",
        ((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),),
    )
    plan = ro.email_plan(
        db,
        with_email["list_id"],
        subject="Hello",
        body="Hi",
        my_name="J",
        now=NOW + timedelta(days=30),
    )
    marco = next(item for item in plan["items"] if item["name"] == "Marco Diaz")
    assert marco["status"] == "skipped" and marco["reason"].startswith(
        "They replied on"
    )
    # Nothing from another app was sent along.
    kit.queue_email(
        db, "studio", "invoice", to="client@example.com", subject="Invoice", body="Hi"
    )
    db.execute("UPDATE suite_outbox SET status = 'approved' WHERE app = 'studio'")
    ro.send_emails(
        db,
        with_email["list_id"],
        [contacts["Lena Park"]["recruiter_id"]],
        subject="Hello",
        body="Hi {first_name}",
        my_name="J",
        password="",
        sender=lambda *a: sent.append(a[2]),
        now=NOW,
    )
    assert [m["To"] for m in sent] == [
        "marco.diaz@contosolabs.com",
        "lena@northwind.example",
    ]


def test_linkedin_drafts_fill_reply_boxes_and_never_overwrite_one(db):
    contacts = saved_contacts(db)
    inbox = rc.create_list(
        db, {"name": "Inbox", "filters": {"source": "linkedin_inbox"}}
    )
    db.execute(
        "INSERT INTO message_drafts (thread_id, participant, reply) VALUES ('t-priya', 'Priya Shah', 'My own words')"
    )
    plan = ro.linkedin_plan(
        db,
        inbox["list_id"],
        body="Hi {first_name}, any news at {company}? {my_first_name}",
        my_name="Jordan Example",
        now=NOW,
    )
    items = {item["name"]: item for item in plan["items"]}
    assert (
        items["Marco Diaz"]["status"] == "ready"
        and items["Marco Diaz"]["text"] == "Hi Marco, any news at Contoso Labs? Jordan"
    )
    assert (
        items["Priya Shah"]["status"] == "skipped"
        and "already waiting" in items["Priya Shah"]["reason"]
    )
    made = ro.make_linkedin_drafts(
        db,
        inbox["list_id"],
        [c["recruiter_id"] for c in contacts.values()],
        body="Hi {first_name}, any news at {company}? {my_first_name}",
        my_name="Jordan Example",
        now=NOW,
    )
    assert made["drafted"] == 1 and made["drafts"][0]["thread_id"] == "t-marco"
    drafts = dict(db.execute("SELECT thread_id, reply FROM message_drafts").fetchall())
    assert drafts == {
        "t-priya": "My own words",
        "t-marco": "Hi Marco, any news at Contoso Labs? Jordan",
    }
    assert any(
        item["kind"] == "linkedin_draft"
        for item in rc.story(db, contacts["Marco Diaz"]["recruiter_id"])["items"]
    )
    # Lena has no LinkedIn conversation saved.
    everyone = rc.create_list(db, {"name": "All", "filters": {}})
    lena = next(
        item
        for item in ro.linkedin_plan(
            db, everyone["list_id"], body="Hi {first_name}", my_name="J"
        )["items"]
        if item["name"] == "Lena Park"
    )
    assert lena["status"] == "skipped" and "No LinkedIn conversation" in lena["reason"]


# --------------------------------------------------------------------------- over HTTP


def seed_over_http(env, api) -> None:
    give_profile(api)
    api.post(
        "/api/email/import",
        {
            "subject": "Solution Architect at Contoso Labs",
            "body": "Hi Jordan,\n\nThanks for the chat.\n\nBest regards,\nMarco Diaz\nSenior Technical Recruiter\n",
            "sender": "marco.diaz@contosolabs.com",
            "sender_name": "Marco Diaz",
            "received_at": "2026-10-22T16:00:00+00:00",
        },
        expect=201,
    )
    api.post(
        "/api/interviews",
        {
            "company": "Northwind",
            "role": "Architect",
            "interview_type": "recruiter_screen",
            "start_at": "2026-10-25T10:00",
            "timezone": "UTC",
            "participants": [
                {
                    "name": "Lena Park",
                    "role": "Recruiter",
                    "email": "lena@northwind.example",
                }
            ],
        },
        expect=201,
    )
    with env.user_db() as connection:
        add_thread(
            connection,
            "t-marco",
            "Marco Diaz",
            raw_text=MARCO_PAGE,
            messages=[
                (
                    "Marco Diaz",
                    False,
                    "2026-10-20",
                    "9:15 AM",
                    "Hello Jordan, are you available?",
                )
            ],
        )
        add_thread(
            connection,
            "t-priya",
            "Priya Shah",
            messages=[
                (
                    "Priya Shah",
                    False,
                    "2026-10-12",
                    "2:00 PM",
                    "I'm a recruiter at Fabrikam.",
                )
            ],
        )
        connection.commit()


def raw_get(env, api, path: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(env.base + path, method="GET")
    request.add_header("X-Requested-With", "aira")
    with api.opener.open(request, timeout=30) as response:
        return response.status, dict(response.headers), response.read()


def test_find_save_story_and_lists_over_http(env, api, monkeypatch):
    seed_over_http(env, api)
    found = api.get("/api/recruiters/find")
    people = by_name(found)
    assert set(people) == {"Marco Diaz", "Priya Shah", "Lena Park"}
    assert people["Priya Shah"]["email"] == ""
    assert api.call("POST", "/api/recruiters/find/save", {"keys": "nope"})[0] == 400
    saved = api.post(
        "/api/recruiters/find/save",
        {"keys": [item["key"] for item in found["proposals"]]},
    )
    assert saved["created"] == 3
    assert api.get("/api/recruiters/find")["proposals"] == []
    listed = {r["name"]: r for r in api.get("/api/recruiters")["recruiters"]}
    assert set(listed["Marco Diaz"]["found_in"].split(",")) == {
        "linkedin_inbox",
        "email",
    }
    marco_id = listed["Marco Diaz"]["recruiter_id"]

    told = api.get(f"/api/recruiters/{marco_id}/story")
    assert told["model_ready"] is False and told["summary"] is None
    assert {item["kind"] for item in told["items"]} == {"linkedin", "email"}
    # No model: a calm answer, not an error.
    answer = api.post(f"/api/recruiters/{marco_id}/story/summary")
    assert (
        answer["summary"] is None
        and answer["model_ready"] is False
        and "No AI model" in answer["message"]
    )
    # A stand-in model through the same path (_ollama_json routes to Claude when chosen).
    prompts: list[str] = []
    monkeypatch.setattr(app, "_ollama_available", lambda: True)
    monkeypatch.setattr(
        app,
        "_ollama_json",
        lambda model, prompt: (
            prompts.append(prompt)
            or {
                "relationship": "Marco reached out about a contract role.",
                "next_step": "Reply with your availability.",
            }
        ),
    )
    answer = api.post(f"/api/recruiters/{marco_id}/story/summary")
    assert (
        answer["summary"]["next_step"] == "Reply with your availability."
        and "untrusted" in prompts[0]
    )
    assert api.get(f"/api/recruiters/{marco_id}/story")["summary"]["stale"] is False

    def broken(model, prompt):
        raise RuntimeError("Could not reach Ollama")

    monkeypatch.setattr(app, "_ollama_json", broken)
    answer = api.post(f"/api/recruiters/{marco_id}/story/summary")
    assert (
        answer["summary"] is None and "could not write a summary" in answer["message"]
    )

    presets = api.get("/api/recruiters/lists")["presets"]
    assert [p["name"] for p in presets] == [
        "At companies I applied to",
        "No reply in 14 days",
        "Has email",
        "From my LinkedIn inbox",
    ]
    assert (
        api.post("/api/recruiters/lists/preview", {"filters": {"has_email": True}})[
            "count"
        ]
        == 2
    )
    made = api.post(
        "/api/recruiters/lists",
        {"name": "From my LinkedIn inbox", "filters": {"source": "linkedin_inbox"}},
        expect=201,
    )["list"]
    assert (
        api.call("POST", "/api/recruiters/lists", {"name": "", "filters": {}})[0] == 400
    )
    members = api.get(f"/api/recruiters/lists/{made['list_id']}/members")["members"]
    assert names(members) == ["Marco Diaz", "Priya Shah"]
    status, headers, body = raw_get(
        env, api, f"/api/recruiters/lists/{made['list_id']}/export.csv"
    )
    assert status == 200 and headers["Content-Type"].startswith("text/csv")
    assert "recruiters-from-my-linkedin-inbox.csv" in headers["Content-Disposition"]
    text = body.decode("utf-8-sig")
    assert (
        text.splitlines()[0].startswith("Name,Title,Company,Email")
        and "Priya Shah" in text
    )
    api.ok(
        "PATCH",
        f"/api/recruiters/lists/{made['list_id']}",
        {"filters": {"has_email": True}},
    )
    assert api.get("/api/recruiters/lists")["lists"][0]["count"] == 2
    api.ok("DELETE", f"/api/recruiters/lists/{made['list_id']}")
    assert api.call("GET", f"/api/recruiters/lists/{made['list_id']}/members")[0] == 404


def test_email_a_list_through_the_smtp_stub(env, api):
    kit.forget_schema()
    seed_over_http(env, api)
    found = api.get("/api/recruiters/find")
    api.post(
        "/api/recruiters/find/save",
        {"keys": [item["key"] for item in found["proposals"]]},
    )
    everyone = api.post(
        "/api/recruiters/lists", {"name": "Everyone", "filters": {}}, expect=201
    )["list"]
    smtp = SmtpStub().start()
    try:
        view = api.get("/api/recruiters/outreach")
        assert view["settings"]["daily_limit"] == 20 and view["email"]["ready"] is False
        plan = api.post(
            f"/api/recruiters/lists/{everyone['list_id']}/email/preview", {}
        )
        assert (
            plan["ready"] == 2
            and plan["linkedin_only"] == 1
            and plan["email"]["ready"] is False
        )
        status, refused = api.call(
            "POST",
            f"/api/recruiters/lists/{everyone['list_id']}/email/send",
            {"recruiter_ids": [], "confirm": True},
        )
        assert status == 400
        assert (
            api.call(
                "POST",
                f"/api/recruiters/lists/{everyone['list_id']}/email/send",
                {"recruiter_ids": ["x"]},
            )[0]
            == 400
        )  # no confirm
        api.post(
            "/api/suite/email",
            {
                "from_email": "jordan@example.com",
                "from_name": "Jordan Example",
                "smtp_host": "127.0.0.1",
                "smtp_port": smtp.port,
                "smtp_security": "none",
            },
        )
        api.post("/api/recruiters/outreach", {"daily_limit": 1})
        plan = api.post(
            f"/api/recruiters/lists/{everyone['list_id']}/email/preview",
            {
                "subject": "Hello {first_name}",
                "body": "Hi {first_name},\n\nAny news at {company|your team}?\n\n{my_name}",
            },
        )
        ready = [item for item in plan["items"] if item["status"] == "ready"]
        assert len(ready) == 1 and plan["later"] == 1 and plan["remaining_today"] == 1
        result = api.post(
            f"/api/recruiters/lists/{everyone['list_id']}/email/send",
            {
                "subject": "Hello {first_name}",
                "body": "Hi {first_name},\n\nAny news at {company|your team}?\n\n{my_name}",
                "recruiter_ids": [item["recruiter_id"] for item in plan["items"]],
                "confirm": True,
            },
        )
        assert result["sent"] == 1 and result["failed"] == 0
        assert len(smtp.messages) == 1
        mail = message_from_string(smtp.messages[0]["data"])
        assert (
            mail["To"] == ready[0]["email"] and mail["Subject"] == ready[0]["subject"]
        )
        assert "unsubscribe" in mail.get_payload(decode=True).decode("utf-8")
        # The daily limit is reached: nothing more today.
        again = api.post(
            f"/api/recruiters/lists/{everyone['list_id']}/email/send",
            {
                "recruiter_ids": [item["recruiter_id"] for item in plan["items"]],
                "confirm": True,
            },
        )
        assert again["sent"] == 0 and len(smtp.messages) == 1
        story = api.get(f"/api/recruiters/{ready[0]['recruiter_id']}/story")
        assert [
            item["status"] for item in story["items"] if item["kind"] == "outreach"
        ] == ["sent"]
        # The template is remembered for next time.
        assert (
            api.get("/api/recruiters/outreach")["settings"]["email_subject"]
            == "Hello {first_name}"
        )
        # Do not contact, by hand.
        api.post(
            f"/api/recruiters/{ready[0]['recruiter_id']}/do-not-contact",
            {"reason": "Not now"},
        )
        assert [
            c["reason"] for c in api.get("/api/recruiters/do-not-contact")["contacts"]
        ] == ["Not now"]
        assert api.get(f"/api/recruiters/{ready[0]['recruiter_id']}/story")[
            "do_not_contact"
        ] == {"reason": "Not now"}
        api.ok("DELETE", f"/api/recruiters/{ready[0]['recruiter_id']}/do-not-contact")
        assert (
            api.call(
                "DELETE", f"/api/recruiters/{ready[0]['recruiter_id']}/do-not-contact"
            )[0]
            == 404
        )
    finally:
        smtp.stop()


def test_linkedin_drafts_are_sent_one_at_a_time_through_the_existing_reply_flow(
    http_client, worker
):
    """The stand-in LinkedIn worker: drafts never reach it; the person's own Send does, once."""
    sign_up(http_client)
    http_client.call("POST", "/api/inbox/sync", {})
    http_wait(http_client)
    status, found = http_client.call("GET", "/api/recruiters/find")
    assert status == 200
    dana = by_name(found)["Dana Lee"]
    assert dana["email"] == "" and dana["source_kinds"] == ["linkedin_inbox"]
    http_client.call("POST", "/api/recruiters/find/save", {"keys": [dana["key"]]})
    status, made = http_client.call(
        "POST",
        "/api/recruiters/lists",
        {"name": "Inbox", "filters": {"source": "linkedin_inbox"}},
    )
    assert status == 201
    list_id = made["list"]["list_id"]
    calls_before = list(worker.calls)
    status, plan = http_client.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/linkedin/preview",
        {
            "body": "Hi {first_name}, thanks for reaching out. I will reply with details soon. {my_first_name}"
        },
    )
    assert status == 200 and plan["ready"] == 1
    status, drafted = http_client.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/linkedin/drafts",
        {
            "body": "Hi {first_name}, thanks for reaching out. I will reply with details soon. {my_first_name}",
            "recruiter_ids": [plan["items"][0]["recruiter_id"]],
        },
    )
    assert status == 200 and drafted["drafted"] == 1
    assert (
        worker.calls == calls_before and worker.sent == []
    )  # drafting never touches LinkedIn
    status, conversation = http_client.call("GET", "/api/conversations/t-dana")
    draft = conversation["draft"]["reply"]
    assert (
        draft
        == "Hi Dana, thanks for reaching out. I will reply with details soon. Jordan"
    )
    # The person reads it and presses Send in Messages: the existing flow, with its guardrails, sends it once.
    status, sent = http_client.call(
        "POST",
        "/api/conversations/t-dana/send",
        {"reply": draft, "confirm": True, "participant": "Dana Lee"},
    )
    assert status == 200 and sent["send"]["status"] == "sent"
    assert [item["message"] for item in worker.sent] == [draft]


def test_hosted_policy_scopes_the_new_routes_and_refuses_recruiter_email():
    from aira_cloud.policy import POLICY

    names_ = {
        name
        for _m, _p, name, _a in app.ROUTES
        if name.startswith(("api_recruiters_", "api_recruiter_"))
    }
    assert names_ <= set(POLICY)
    for name in (
        "api_recruiter_outreach",
        "api_recruiter_outreach_save",
        "api_recruiter_list_email_preview",
        "api_recruiter_list_email_send",
    ):
        assert POLICY[name].scope == "deny", name
    for name in (
        "api_recruiters_find",
        "api_recruiter_story",
        "api_recruiter_list_members",
        "api_recruiter_list_linkedin_drafts",
    ):
        assert POLICY[name].scope == "candidate", name
    assert POLICY["api_recruiter_story"].permission == "messages.read"
    assert POLICY["api_recruiter_list_linkedin_drafts"].permission == "messages.send"
