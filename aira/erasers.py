"""Every app's eraser for :mod:`aira.erasure` (registered when this module is imported).

A life app keys a person's rows by their profile id (``owner_id``, ``profile_id``, ``author_id``...): those rows are
deleted, and what the household shares (a grocery list they started, a meal they planned) stays without their name.
A business keys its customers by email and phone: what is only about the customer is deleted (a waitlist place, a
subscription, a chat), and what its books need stays anonymised (an order keeps its number, items and amounts; the
name becomes :data:`aira.erasure.ERASED_NAME`, the email, phone, address and notes go). Social and Videos keep one
database for the platform and erase there. ``sweep`` runs last but one: any column left anywhere that holds one of
the person's email addresses or phone numbers is blanked. ``life`` (the profile itself) runs last.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from aira import erasure
from aira.erasure import (
    ERASED_NAME,
    Person,
    Tally,
    blank,
    column_values,
    columns,
    delete,
    delete_all,
    drop_from_lists,
    ids,
    matching,
    scrub_ledger,
)

NAME = ERASED_NAME


def _rows(
    connection: sqlite3.Connection,
    table: str,
    person: Person,
    *,
    emails=(),
    phones=(),
    key: str = "id",
) -> list[str]:
    return [
        str(v)
        for v in matching(
            connection, table, person, emails=emails, phones=phones, key=key
        )
    ]


def _any(
    connection: sqlite3.Connection, table: str, column: str, values: list[str]
) -> set[str]:
    """Which of ``values`` appear in ``table.column``."""
    return set(ids(connection, table, column, values, key=column))


# ---------------------------------------------------------------------------
# Health (Care) and the life book (Journey)
# ---------------------------------------------------------------------------


def erase_care(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Medicines, doses, readings, the diary, appointments, the medical card and caregivers; a care worker's
    statements stay for the books without the client's details."""
    tally = Tally()
    me = [person.profile_id] if person.profile_id else []
    if me:
        medicines = ids(connection, "cr_medicines", "owner_id", me)
        appointments = ids(connection, "cr_appointments", "owner_id", me)
        for table in (
            "cr_doses",
            "cr_refills",
            "cr_schedules",
            "cr_missed_notices",
            "cr_medicines",
            "cr_appointments",
            "cr_cards",
            "cr_diary",
            "cr_readings",
            "cr_report_links",
            "cr_caregivers",
        ):
            tally.add(table, delete(connection, table, "owner_id", me))
        tally.add(
            "cr_caregivers", delete(connection, "cr_caregivers", "caregiver_id", me)
        )
        # mail and notifications about their medicines (a missed dose, to a caregiver)
        tally.add(
            "suite_outbox",
            delete(connection, "suite_outbox", "ref_id", medicines + appointments),
        )
        if medicines and columns(connection, "nt_items"):
            for medicine_id in medicines:
                tally.add(
                    "nt_items",
                    max(
                        connection.execute(
                            "DELETE FROM nt_items WHERE dedupe LIKE ?",
                            (f"%:{medicine_id}:%",),
                        ).rowcount,
                        0,
                    ),
                )
        # their own care work: statements are invoices (kept, without the client); visits on one stay as its lines
        statements = ids(connection, "cr_statements", "owner_id", me)
        clients = ids(connection, "cr_clients", "owner_id", me)
        billed = sorted(_any(connection, "cr_statements", "client_id", clients))
        tally.add(
            "cr_statements",
            blank(
                connection,
                "cr_statements",
                "id",
                statements,
                {"owner_id": "", "note": None, "client_paid_note": None},
            ),
        )
        visits = ids(connection, "cr_visits", "owner_id", me)
        on_statements = set(ids(connection, "cr_visits", "statement_id", statements))
        tally.add(
            "cr_visits",
            blank(
                connection,
                "cr_visits",
                "id",
                [v for v in visits if v in on_statements],
                {"owner_id": "", "notes": None, "tasks": None},
            ),
        )
        tally.add(
            "cr_visits",
            delete(
                connection,
                "cr_visits",
                "id",
                [v for v in visits if v not in on_statements],
            ),
        )
        tally.add(
            "cr_clients",
            blank(
                connection,
                "cr_clients",
                "id",
                billed,
                {"owner_id": "", "name": NAME, "email": "", "phone": "", "notes": None},
            ),
        )
        tally.add(
            "cr_clients",
            delete(
                connection, "cr_clients", "id", [c for c in clients if c not in billed]
            ),
        )
        tally.merge(scrub_ledger(connection, statements, person.names))
    # someone else's care client or caregiver who is this person
    found = _rows(
        connection, "cr_clients", person, emails=("email",), phones=("phone",)
    )
    tally.add(
        "cr_clients",
        blank(
            connection,
            "cr_clients",
            "id",
            found,
            {"name": NAME, "email": "", "phone": "", "notes": None},
        ),
    )
    tally.add(
        "cr_caregivers",
        delete(
            connection,
            "cr_caregivers",
            "id",
            _rows(connection, "cr_caregivers", person, emails=("email",)),
        ),
    )
    return tally.counts


def erase_journey(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """The life book (growth, vaccines, check-ups, the lives they keep), end-of-life wishes, goals, habits, the
    journal, the timeline and documents."""
    tally = Tally()
    if not person.profile_id:
        return {}
    me = [person.profile_id]
    goals = ids(connection, "jr_goals", "owner_id", me)
    habits = ids(connection, "jr_habits", "owner_id", me)
    lives = ids(connection, "jr_lives", "owner_id", me)
    for table in ("jr_checkins", "jr_steps", "jr_cheers", "jr_pledges"):
        tally.add(table, delete(connection, table, "goal_id", goals))
    tally.add("jr_habit_days", delete(connection, "jr_habit_days", "habit_id", habits))
    tally.add("jr_life_items", delete(connection, "jr_life_items", "life_id", lives))
    for table in (
        "jr_records",
        "jr_lives",  # their own life and its wishes, and the lives of people they look after
        "jr_goals",
        "jr_habits",
        "jr_journal",
        "jr_events",
        "jr_documents",
        "jr_cheers",
        "jr_pledges",
    ):
        tally.add(table, delete(connection, table, "owner_id", me))
    return tally.counts


# ---------------------------------------------------------------------------
# Family, Planner
# ---------------------------------------------------------------------------


def erase_family(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    if not person.profile_id:
        return {}
    me = [person.profile_id]
    posts = ids(connection, "fm_posts", "author_id", me)
    tally.add("fm_comments", delete(connection, "fm_comments", "post_id", posts))
    tally.add("fm_reactions", delete(connection, "fm_reactions", "post_id", posts))
    tally.add("fm_posts", delete(connection, "fm_posts", "id", posts))
    rooms = list(
        dict.fromkeys(
            ids(connection, "fm_rooms", "member_a", me)
            + ids(connection, "fm_rooms", "member_b", me)
        )
    )
    tally.add("fm_messages", delete(connection, "fm_messages", "room_id", rooms))
    tally.add("fm_reads", delete(connection, "fm_reads", "room_id", rooms))
    tally.add("fm_rooms", delete(connection, "fm_rooms", "id", rooms))
    for table, column in (
        ("fm_comments", "author_id"),
        ("fm_reactions", "who"),
        ("fm_messages", "author_id"),
        ("fm_reads", "profile_id"),
        ("fm_rsvps", "profile_id"),
        ("fm_seen", "profile_id"),
        ("fm_wishes", "owner_id"),
    ):
        tally.add(table, delete(connection, table, column, me))
    tally.add(
        "fm_wishes",
        blank(
            connection,
            "fm_wishes",
            "claimed_by",
            me,
            {"claimed_by": "", "claimed_name": ""},
        ),
    )
    tally.add(
        "fm_wishes",
        blank(connection, "fm_wishes", "created_by", me, {"created_by": ""}),
    )
    tally.add(
        "fm_dates", blank(connection, "fm_dates", "created_by", me, {"created_by": ""})
    )
    tally.add(
        "fm_events",
        blank(connection, "fm_events", "created_by", me, {"created_by": ""}),
    )
    tally.add(
        "fm_events",
        drop_from_lists(connection, "fm_events", "invitees", person.profile_id),
    )
    # their own birthday on the family calendar
    if person.names and columns(connection, "fm_dates"):
        marks = ", ".join("?" for _ in person.names)
        tally.add(
            "fm_dates",
            max(
                connection.execute(
                    f"DELETE FROM fm_dates WHERE lower(name) IN ({marks})",  # noqa: S608 - placeholders only
                    tuple(n.lower() for n in person.names),
                ).rowcount,
                0,
            ),
        )
    return tally.counts


def erase_planner(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Their notes, points, pocket money and rewards go; the household's lists, meals, bills and budget stay
    without their name (money lines keep their amounts)."""
    tally = Tally()
    me = [person.profile_id] if person.profile_id else []
    if me:
        for table, column in (
            ("pn_notes", "owner_id"),
            ("pn_points", "profile_id"),
            ("pn_payouts", "profile_id"),
            ("pn_reward_requests", "profile_id"),
            ("pn_task_done", "profile_id"),
        ):
            tally.add(table, delete(connection, table, column, me))
        tally.add(
            "pn_entries",
            blank(
                connection,
                "pn_entries",
                "profile_id",
                me,
                {"profile_id": "", "note": ""},
            ),
        )
        tally.add(
            "pn_goal_moves",
            blank(
                connection,
                "pn_goal_moves",
                "profile_id",
                me,
                {"profile_id": "", "note": ""},
            ),
        )
        # their things for sale: unsold ones go, sold ones stay for the budget
        selling = ids(connection, "pn_sale_items", "created_by", me)
        sold = set(ids(connection, "pn_sale_items", "status", ["sold"]))
        tally.add(
            "pn_sale_requests",
            delete(
                connection,
                "pn_sale_requests",
                "item_id",
                [i for i in selling if i not in sold],
            ),
        )
        tally.add(
            "pn_sale_items",
            delete(
                connection, "pn_sale_items", "id", [i for i in selling if i not in sold]
            ),
        )
        for table, column in (
            ("pn_tasks", "assignee"),
            ("pn_tasks", "created_by"),
            ("pn_tasks", "done_by"),
            ("pn_lists", "created_by"),
            ("pn_items", "added_by"),
            ("pn_items", "checked_by"),
            ("pn_meals", "created_by"),
            ("pn_meals", "cook"),
            ("pn_recipes", "created_by"),
            ("pn_goals", "created_by"),
            ("pn_rewards", "created_by"),
            ("pn_reward_requests", "decided_by"),
            ("pn_bill_payments", "paid_by"),
            ("pn_payouts", "paid_by"),
            ("pn_jobs", "done_by"),
            ("pn_sale_items", "created_by"),
        ):
            tally.add(table, blank(connection, table, column, me, {column: ""}))
        tally.add(
            "pn_tasks",
            drop_from_lists(connection, "pn_tasks", "rotation", person.profile_id),
        )
    # someone who asked to buy something, by their email or phone
    tally.add(
        "pn_sale_requests",
        delete(
            connection,
            "pn_sale_requests",
            "id",
            _rows(
                connection,
                "pn_sale_requests",
                person,
                emails=("contact",),
                phones=("contact",),
            ),
        ),
    )
    return tally.counts


# ---------------------------------------------------------------------------
# Learn, Play, News, Stocks, AI chat
# ---------------------------------------------------------------------------


def erase_learn(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    me = [person.profile_id] if person.profile_id else []
    if me:
        decks = ids(connection, "ln_decks", "owner_id", me)
        for table in ("ln_progress", "ln_reviews", "ln_quizzes"):
            tally.add(
                table, delete(connection, table, "deck_id", decks)
            )  # everyone's progress on their decks
        tally.add("ln_cards", delete(connection, "ln_cards", "deck_id", decks))
        tally.add("ln_decks", delete(connection, "ln_decks", "id", decks))
        for table in (
            "ln_books",
            "ln_days",
            "ln_people",
            "ln_progress",
            "ln_quizzes",
            "ln_reading",
            "ln_reviews",
            "ln_sellers",
        ):
            tally.add(table, delete(connection, table, "profile_id", me))
        # decks they sold: the sale stays for the books, without the seller or the buyer
        tally.add(
            "ln_sales",
            blank(
                connection,
                "ln_sales",
                "owner_id",
                me,
                {
                    "owner_id": "",
                    "buyer_name": NAME,
                    "buyer_email": "",
                    "code_hash": None,
                },
            ),
        )
    bought = _rows(connection, "ln_sales", person, emails=("buyer_email",))
    names = column_values(connection, "ln_sales", "buyer_name", "id", bought)
    tally.add(
        "ln_sales",
        blank(
            connection,
            "ln_sales",
            "id",
            bought,
            {"buyer_name": NAME, "buyer_email": "", "code_hash": None},
        ),
    )
    tally.merge(scrub_ledger(connection, bought, [*names, *person.names]))
    return tally.counts


def erase_play(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    if not person.profile_id:
        return {}
    me = [person.profile_id]
    rooms = ids(connection, "pl_rooms", "host_id", me)
    players = ids(connection, "pl_room_players", "room_id", rooms) + ids(
        connection, "pl_room_players", "profile_id", me
    )
    tally.add(
        "pl_room_answers", delete(connection, "pl_room_answers", "player_id", players)
    )
    tally.add(
        "pl_room_guest_clients",
        delete(connection, "pl_room_guest_clients", "player_id", players),
    )
    tally.add("pl_room_players", delete(connection, "pl_room_players", "id", players))
    for table in (
        "pl_room_answers",
        "pl_room_extras",
        "pl_room_writers",
        "pl_room_guest_clients",
    ):
        tally.add(table, delete(connection, table, "room_id", rooms))
    # entry fees paid stay for the books: a player id that names nobody any more, and an amount
    tally.add("pl_rooms", delete(connection, "pl_rooms", "id", rooms))
    for table, column in (
        ("pl_sessions", "profile_id"),
        ("pl_challenges", "from_id"),
        ("pl_challenges", "to_id"),
        ("pl_quizzes", "owner_id"),
        ("pl_room_writers", "writer_id"),
        ("pl_watch_status", "profile_id"),
    ):
        tally.add(table, delete(connection, table, column, me))
    tally.add(
        "pl_watch", blank(connection, "pl_watch", "added_by", me, {"added_by": ""})
    )
    tally.add(
        "pl_watch_plans",
        blank(connection, "pl_watch_plans", "created_by", me, {"created_by": ""}),
    )
    tally.add(
        "pl_watch_plans",
        drop_from_lists(connection, "pl_watch_plans", "who", person.profile_id),
    )
    return tally.counts


def erase_news(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    me = [person.profile_id] if person.profile_id else []
    if me:
        for table in (
            "nw_keywords",
            "nw_muted",
            "nw_read",
            "nw_saved",
            "nw_settings",
            "nw_sources",
        ):
            tally.add(table, delete(connection, table, "profile_id", me))
        letters = ids(connection, "nw_letters", "owner_id", me)
        tally.add("nw_picks", delete(connection, "nw_picks", "letter_id", letters))
        tally.add("nw_issues", delete(connection, "nw_issues", "letter_id", letters))
        # their newsletter's readers (payments stay for the books, naming nobody)
        tally.add(
            "nw_subscribers", delete(connection, "nw_subscribers", "letter_id", letters)
        )
        tally.add("nw_letters", delete(connection, "nw_letters", "id", letters))
    tally.merge(_subscriber(connection, person, "nw_subscribers", "nw_payments"))
    return tally.counts


def _subscriber(
    connection: sqlite3.Connection, person: Person, table: str, payments: str
) -> dict[str, int]:
    """A reader or subscriber who is this person: kept without their name and email while payments refer to them."""
    tally = Tally()
    found = _rows(connection, table, person, emails=("email",))
    paid = _any(connection, payments, "subscriber_id", found)
    tally.add(
        table,
        blank(
            connection,
            table,
            "id",
            sorted(paid),
            {"name": NAME, "email": "", "note": None, "code_hash": None},
        ),
    )
    tally.add(
        table, delete(connection, table, "id", [f for f in found if f not in paid])
    )
    return tally.counts


def erase_stocks(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    me = [person.profile_id] if person.profile_id else []
    if me:
        ideas = ids(connection, "stk_ideas", "owner_id", me)
        lists = ids(connection, "stk_watchlists", "owner_id", me)
        tally.add(
            "stk_idea_disclosures",
            delete(connection, "stk_idea_disclosures", "idea_id", ideas),
        )
        tally.add(
            "stk_watch_items", delete(connection, "stk_watch_items", "list_id", lists)
        )
        for table in (
            "stk_txns",
            "stk_accounts",
            "stk_watch_items",
            "stk_watchlists",
            "stk_alerts",
            "stk_predictions",
            "stk_ideas",
            "stk_analysts",
            "stk_disclosures",
        ):
            tally.add(table, delete(connection, table, "owner_id", me))
        # their subscribers (payments stay for the books, naming nobody)
        tally.add(
            "stk_subscribers", delete(connection, "stk_subscribers", "analyst_id", me)
        )
    tally.merge(_subscriber(connection, person, "stk_subscribers", "stk_payments"))
    return tally.counts


def erase_ai(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Chat history, memories, routines, actions waiting for a yes, and the helpers they turned on."""
    tally = Tally()
    if not person.profile_id:
        return {}
    me = [person.profile_id]
    sessions = ids(connection, "ai_sessions", "owner_id", me)
    tally.add("ai_messages", delete(connection, "ai_messages", "session_id", sessions))
    for table in ("ai_sessions", "ai_memories", "ai_ops", "ai_routines", "ai_agents"):
        tally.add(table, delete(connection, table, "owner_id", me))
    return tally.counts


# ---------------------------------------------------------------------------
# notifications, mail, the profile
# ---------------------------------------------------------------------------


def _notify_keys(person: Person) -> list[str]:
    keys = list(person.keys)
    if person.owner:
        keys.append(
            "owner"
        )  # the account itself (Careers before the household had profiles) is the owner
    return keys


def erase_notify(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Their notifications, choices and every browser's push subscription."""
    tally = Tally()
    keys = _notify_keys(person)
    for table in ("nt_items", "nt_prefs", "nt_push_subs"):
        tally.add(table, delete(connection, table, "person", keys))
    if person.whole_account:
        tally.add(
            "notifications", delete_all(connection, "notifications")
        )  # Careers' notifications
    return tally.counts


def erase_outbox(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Mail the apps prepared or sent to them, or about them; mail they received in the shared mailbox."""
    tally = Tally()
    tally.add(
        "suite_outbox",
        delete(
            connection,
            "suite_outbox",
            "rowid",
            _rows(
                connection, "suite_outbox", person, emails=("recipient",), key="rowid"
            ),
        ),
    )
    if person.profile_id:
        tally.add(
            "suite_outbox",
            delete(connection, "suite_outbox", "ref_id", [person.profile_id]),
        )
    tally.add(
        "suite_inbound",
        delete(
            connection,
            "suite_inbound",
            "rowid",
            _rows(
                connection, "suite_inbound", person, emails=("from_email",), key="rowid"
            ),
        ),
    )
    return tally.counts


LIFE_FEEDS = (
    "life",
    "family",
    "care",
    "journey",
    "planner",
    "learn",
    "play",
    "news",
    "stocks",
    "ai",
    "social",
    "videos",
)


def erase_activity(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """The household's activity lines keep what happened without the person's name ("Someone joined the
    household"). The business apps' lines are handled by each app, by the record they are about."""
    have = columns(connection, "suite_activity")
    if not have or not person.names:
        return {}
    marks = erasure.placeholders(LIFE_FEEDS)
    changed = 0
    for row in connection.execute(
        f"SELECT rowid, title, detail, actor FROM suite_activity WHERE app IN ({marks})",  # noqa: S608 - placeholders
        LIFE_FEEDS,
    ).fetchall():
        title = erasure.scrub_names(row[1], person.names, "Someone")
        detail = erasure.scrub_names(row[2], person.names, "Someone")
        actor = (
            ""
            if row[3] and row[3].strip().lower() in {n.lower() for n in person.names}
            else row[3]
        )
        if (title, detail, actor) != (row[1], row[2], row[3]):
            connection.execute(
                "UPDATE suite_activity SET title = ?, detail = ?, actor = ? WHERE rowid = ?",
                (title, detail, actor, row[0]),
            )
            changed += 1
    return {"suite_activity": changed}


def erase_life(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """The profile itself (and its unlocks and parental controls): last, so a retry still knows who it was."""
    tally = Tally()
    profiles = [person.profile_id] if person.profile_id else []
    if person.member_id:
        profiles += ids(connection, "life_profiles", "member_ref", [person.member_id])
    profiles = list(dict.fromkeys(profiles))
    tally.add(
        "life_unlocks", delete(connection, "life_unlocks", "profile_id", profiles)
    )
    tally.add(
        "life_controls", delete(connection, "life_controls", "profile_id", profiles)
    )
    tally.add(
        "life_controls",
        blank(connection, "life_controls", "updated_by", profiles, {"updated_by": ""}),
    )
    tally.add("life_profiles", delete(connection, "life_profiles", "id", profiles))
    return tally.counts


# ---------------------------------------------------------------------------
# Social and Videos (one database for the platform)
# ---------------------------------------------------------------------------


def erase_social(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Posts, comments, messages, stories, friends, the Dating profile, matches and photos. Evidence an open report
    needs stays under a legal hold (``held:`` counts, aira.suite.social.service.erase_account)."""
    if not person.keys:
        return {}
    from aira.suite.social import db as social_db
    from aira.suite.social import service as social

    tally = Tally()
    with closing(social_db.connect()) as sc:
        for account_id in social.accounts_for(sc, person_keys=person.keys):
            tally.merge(social.erase_account(sc, account_id))
    return tally.counts


def erase_videos(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """The channel, uploads and files, comments, likes, history and payout details; tips and memberships paid to
    others stay with them without the person's name."""
    if not person.keys:
        return {}
    from aira.suite.videos import db as videos_db
    from aira.suite.videos import service as videos

    tally = Tally()
    with closing(videos_db.connect()) as sc:
        channels = videos.channels_for(sc, person_keys=person.keys)
        for table, column in (
            ("vi_videos", "channel_id"),
            ("vi_comments", "author_id"),
            ("vi_files", "owner_key"),
            ("vi_uploads", "owner_key"),
            ("vi_watch", "viewer_id"),
            ("vi_likes", "viewer_id"),
            ("vi_subscriptions", "subscriber_id"),
            ("vi_playlists", "channel_id"),
        ):
            found = len(ids(sc, table, column, channels, key="rowid"))
            tally.add(table, found)
        tally.add("vi_channels", len(channels))
        videos.erase_channels(sc, channels)
    return tally.counts


# ---------------------------------------------------------------------------
# Careers, recruiter contacts and leads
# ---------------------------------------------------------------------------

CAREERS_TABLES = (
    "activity_log",
    "application_events",
    "application_preparations",
    "application_runs",
    "application_submissions",
    "automation_runs",
    "autopilot_passes",
    "autopilot_plans",
    "careers_alert_jobs",
    "careers_alerts",
    "careers_reports",
    "custom_answers",
    "document_audit",
    "documents",
    "email_applications",
    "email_messages",
    "inbox_check_items",
    "inbox_messages",
    "inbox_sync_runs",
    "inbox_threads",
    "integration_connections",
    "interview_participants",
    "interviews",
    "job_offers",
    "job_source_records",
    "jobs",
    "message_checks",
    "message_drafts",
    "message_sends",
    "message_threads",
    "schedules",
    "settings",
    "source_sync_runs",
    "standard_answers",
    "tasks",
    "workday_accounts",
    "workday_preferences",
    "airaapi_monitors",
    "airaapi_saved",
    "airaapi_searches",
    "airaapi_search_text",
    "airaapi_rank_checks",
    "airaapi_rank_keywords",
    "airaapi_rank_projects",
    "airaapi_cache",
    "airaapi_secrets",
)


RECRUITER_TABLES = (
    "recruiters",
    "recruiter_interactions",
    "recruiter_sources",
    "recruiter_summaries",
    "recruiter_do_not_contact",
    "recruiter_lists",
)


def erase_careers(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Only in the person's own workspace (``whole_account``): their resume and profile, jobs, applications,
    interviews, the LinkedIn inbox, saved answers and searches, sign-ins to job sites and documents (their files
    on disk go with the workspace). Elsewhere: their email in the mail Careers read and in interviews."""
    tally = Tally()
    if person.whole_account:
        for table in CAREERS_TABLES:
            tally.add(table, delete_all(connection, table))
        return tally.counts
    tally.add(
        "email_messages",
        delete(
            connection,
            "email_messages",
            "rowid",
            _rows(
                connection,
                "email_messages",
                person,
                emails=("sender_email",),
                key="rowid",
            ),
        ),
    )
    tally.add(
        "interview_participants",
        delete(
            connection,
            "interview_participants",
            "rowid",
            _rows(
                connection,
                "interview_participants",
                person,
                emails=("email",),
                key="rowid",
            ),
        ),
    )
    return tally.counts


def erase_recruiters(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Recruiter contacts the person stored (all of them in their own workspace); elsewhere, a recruiter record
    that is this person."""
    tally = Tally()
    if person.whole_account:
        found = [str(r) for (r,) in _all(connection, "recruiters", "recruiter_id")]
    else:
        found = _rows(
            connection,
            "recruiters",
            person,
            emails=("email",),
            phones=("phone",),
            key="recruiter_id",
        )
    for table in (
        "recruiter_interactions",
        "recruiter_sources",
        "recruiter_summaries",
        "recruiter_do_not_contact",
    ):
        tally.add(table, delete(connection, table, "recruiter_id", found))
    for table in ("tasks", "interviews", "message_threads"):
        tally.add(
            table,
            blank(connection, table, "recruiter_id", found, {"recruiter_id": None}),
        )
    tally.add("recruiters", delete(connection, "recruiters", "recruiter_id", found))
    if person.whole_account:
        tally.add("recruiter_lists", delete_all(connection, "recruiter_lists"))
    return tally.counts


def _all(connection: sqlite3.Connection, table: str, column: str) -> list[tuple[Any]]:
    if column not in columns(connection, table):
        return []
    return connection.execute(f'SELECT "{column}" FROM "{table}"').fetchall()  # noqa: S608 - checked names


def erase_leads(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Leads the person stored (all of them in their own workspace), or a lead that is this customer, with its
    calls, messages and sequence steps."""
    tally = Tally()
    if person.whole_account:
        for table in (
            "lead_calls",
            "lead_messages",
            "lead_enrollments",
            "lead_keys",
            "leads",
            "lead_lists",
            "lead_searches",
            "lead_runs",
            "lead_sequences",
        ):
            tally.add(table, delete_all(connection, table))
        return tally.counts
    found = _rows(connection, "leads", person, emails=("email",), phones=("phone",))
    for table in ("lead_calls", "lead_messages", "lead_enrollments", "lead_keys"):
        tally.add(table, delete(connection, table, "lead_id", found))
    tally.add("leads", delete(connection, "leads", "id", found))
    tally.add(
        "lead_messages",
        delete(
            connection,
            "lead_messages",
            "id",
            _rows(connection, "lead_messages", person, emails=("to_address",)),
        ),
    )
    tally.add(
        "lead_enrollments",
        delete(
            connection,
            "lead_enrollments",
            "id",
            _rows(connection, "lead_enrollments", person, emails=("email",)),
        ),
    )
    return tally.counts


# ---------------------------------------------------------------------------
# the business apps: a customer by email or phone
# ---------------------------------------------------------------------------


def erase_shop(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    orders = _rows(
        connection,
        "sh_orders",
        person,
        emails=("customer_email",),
        phones=("customer_phone",),
    )
    names = column_values(connection, "sh_orders", "customer_name", "id", orders)
    tally.add(
        "sh_orders",
        blank(
            connection,
            "sh_orders",
            "id",
            orders,
            {
                "customer_name": NAME,
                "customer_email": "",
                "customer_phone": "",
                "address": None,
                "note": None,
                "internal_notes": None,
                "client_key": None,
            },
        ),
    )
    trade = _rows(
        connection, "sh_trade_accounts", person, emails=("email",), phones=("phone",)
    )
    names += column_values(connection, "sh_trade_accounts", "contact_name", "id", trade)
    tally.add(
        "sh_trade_accounts",
        blank(
            connection,
            "sh_trade_accounts",
            "id",
            trade,
            {
                "contact_name": NAME,
                "email": "",
                "phone": "",
                "address": None,
                "note": None,
                "code_hash": None,
            },
        ),
    )
    tally.add(
        "sh_restock_alerts",
        delete(
            connection,
            "sh_restock_alerts",
            "id",
            _rows(connection, "sh_restock_alerts", person, emails=("email",)),
        ),
    )
    referrers = _rows(connection, "sh_referrers", person, emails=("email",))
    tally.add(
        "sh_referral_rewards",
        delete(connection, "sh_referral_rewards", "referrer_id", referrers),
    )
    tally.add("sh_referrers", delete(connection, "sh_referrers", "id", referrers))
    tally.merge(scrub_ledger(connection, orders + trade, [*names, *person.names]))
    return tally.counts


def erase_bookings(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    customers = _rows(
        connection, "bk_customers", person, emails=("email",), phones=("phone",)
    )
    names = column_values(connection, "bk_customers", "name", "id", customers)
    tally.add(
        "bk_customers",
        blank(
            connection,
            "bk_customers",
            "id",
            customers,
            {"name": NAME, "email": "", "phone": "", "notes": None, "tags": None},
        ),
    )
    bookings = list(
        dict.fromkeys(
            _rows(
                connection,
                "bk_bookings",
                person,
                emails=("customer_email",),
                phones=("customer_phone",),
            )
            + ids(connection, "bk_bookings", "customer_id", customers)
        )
    )
    names += column_values(connection, "bk_bookings", "customer_name", "id", bookings)
    tally.add(
        "bk_bookings",
        blank(
            connection,
            "bk_bookings",
            "id",
            bookings,
            {
                "customer_name": NAME,
                "customer_email": "",
                "customer_phone": "",
                "note": None,
                "internal_note": None,
                "cancel_reason": None,
            },
        ),
    )
    tally.add(
        "bk_waitlist",
        delete(
            connection,
            "bk_waitlist",
            "id",
            _rows(
                connection, "bk_waitlist", person, emails=("email",), phones=("phone",)
            ),
        ),
    )
    packages = ids(connection, "bk_customer_packages", "customer_id", customers)
    tally.merge(
        scrub_ledger(
            connection, bookings + customers + packages, [*names, *person.names]
        )
    )
    return tally.counts


def erase_marketing(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    subscribers = _rows(connection, "mk_subscribers", person, emails=("email",))
    tally.add(
        "mk_newsletter_recipients",
        delete(connection, "mk_newsletter_recipients", "subscriber_id", subscribers),
    )
    tally.add("mk_subscribers", delete(connection, "mk_subscribers", "id", subscribers))
    submissions = _rows(
        connection, "mk_submissions", person, emails=("email",), phones=("phone",)
    )
    names = column_values(connection, "mk_submissions", "name", "id", submissions)
    paid: set[str] = set()
    if submissions and {"paid_at", "value_cents"} <= set(
        columns(connection, "mk_submissions")
    ):
        paid = {  # a paid enquiry is revenue: it stays without the person
            str(r[0])
            for r in connection.execute(
                f"SELECT id FROM mk_submissions WHERE (paid_at IS NOT NULL OR value_cents > 0) AND id IN ({erasure.placeholders(submissions)})",  # noqa: S608
                tuple(submissions),
            )
        }
    tally.add(
        "mk_submissions",
        blank(
            connection,
            "mk_submissions",
            "id",
            sorted(paid),
            {
                "name": NAME,
                "email": "",
                "phone": "",
                "company": "",
                "message": "",
                "answer": "",
                "notes": None,
            },
        ),
    )
    tally.add(
        "mk_submissions",
        delete(
            connection,
            "mk_submissions",
            "id",
            [s for s in submissions if s not in paid],
        ),
    )
    tally.merge(scrub_ledger(connection, submissions, [*names, *person.names]))
    return tally.counts


def erase_service(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    cases = _rows(
        connection,
        "sv_cases",
        person,
        emails=("customer_email",),
        phones=("customer_phone",),
    )
    names = column_values(connection, "sv_cases", "customer_name", "id", cases)
    charged = _any(connection, "sv_charges", "case_id", cases)
    tally.add("sv_messages", delete(connection, "sv_messages", "case_id", cases))
    tally.add(
        "sv_cases",
        blank(
            connection,
            "sv_cases",
            "id",
            sorted(charged),
            {
                "customer_name": NAME,
                "customer_email": "",
                "customer_phone": "",
                "subject": "Erased at the customer's request",
                "description": "",
                "csat_comment": None,
                "tags": None,
            },
        ),
    )
    tally.add(
        "sv_cases",
        delete(connection, "sv_cases", "id", [c for c in cases if c not in charged]),
    )
    chats = _rows(connection, "sv_chats", person, emails=("visitor_email",))
    names += column_values(connection, "sv_chats", "visitor_name", "id", chats)
    tally.add(
        "sv_chat_messages", delete(connection, "sv_chat_messages", "chat_id", chats)
    )
    tally.add("sv_chats", delete(connection, "sv_chats", "id", chats))
    tally.merge(
        scrub_ledger(
            connection,
            cases + chats + sorted(_charges(connection, cases)),
            [*names, *person.names],
        )
    )
    return tally.counts


def _charges(connection: sqlite3.Connection, cases: list[str]) -> set[str]:
    return set(ids(connection, "sv_charges", "case_id", cases))


def erase_events(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    orders = _rows(
        connection,
        "ev_orders",
        person,
        emails=("buyer_email",),
        phones=("buyer_phone",),
    )
    names = column_values(connection, "ev_orders", "buyer_name", "id", orders)
    tally.add(
        "ev_orders",
        blank(
            connection,
            "ev_orders",
            "id",
            orders,
            {"buyer_name": NAME, "buyer_email": "", "buyer_phone": "", "notes": None},
        ),
    )
    tally.add(
        "ev_tickets",
        blank(connection, "ev_tickets", "order_id", orders, {"holder_name": NAME}),
    )
    tally.add(
        "ev_waitlist",
        delete(
            connection,
            "ev_waitlist",
            "id",
            _rows(
                connection, "ev_waitlist", person, emails=("email",), phones=("phone",)
            ),
        ),
    )
    sponsors = _rows(connection, "ev_sponsors", person, emails=("contact_email",))
    names += column_values(connection, "ev_sponsors", "contact_name", "id", sponsors)
    tally.add(
        "ev_sponsors",
        blank(
            connection,
            "ev_sponsors",
            "id",
            sponsors,
            {"contact_name": "", "contact_email": "", "notes": None},
        ),
    )
    tally.merge(scrub_ledger(connection, orders + sponsors, [*names, *person.names]))
    return tally.counts


def erase_academy(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    tally = Tally()
    enrolments = _rows(connection, "ac_enrolments", person, emails=("email",))
    names = column_values(connection, "ac_enrolments", "name", "id", enrolments)
    for table in ("ac_progress", "ac_attempts", "ac_reviews"):
        tally.add(table, delete(connection, table, "enrolment_id", enrolments))
    tally.add(
        "ac_enrolments",
        blank(
            connection,
            "ac_enrolments",
            "id",
            enrolments,
            {"name": NAME, "email": "", "notes": None},
        ),
    )
    for email in person.emails:
        tally.add(
            "ac_announcements",
            drop_from_lists(connection, "ac_announcements", "recipients", email),
        )
    partners = _rows(connection, "xp_partners", person, emails=("email",))
    names += column_values(connection, "xp_partners", "name", "id", partners)
    tally.add(
        "xp_partners",
        blank(
            connection,
            "xp_partners",
            "id",
            partners,
            {"name": NAME, "email": "", "notes": None},
        ),
    )
    everyone = [*names, *person.names]
    if everyone and columns(connection, "xp_referrals"):
        marks = erasure.placeholders(everyone)
        tally.add(
            "xp_referrals",
            max(
                connection.execute(
                    f"UPDATE xp_referrals SET customer = '' WHERE lower(customer) IN ({marks})",  # noqa: S608
                    tuple(n.lower() for n in everyone),
                ).rowcount,
                0,
            ),
        )
    tally.merge(scrub_ledger(connection, enrolments, everyone))
    return tally.counts


def erase_studio(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """A client of the studio: invoices and proposals stay for the books, under an anonymised client."""
    tally = Tally()
    clients = _rows(
        connection, "st_clients", person, emails=("email",), phones=("phone",)
    )
    names = column_values(connection, "st_clients", "name", "id", clients)
    tally.add(
        "st_clients",
        blank(
            connection,
            "st_clients",
            "id",
            clients,
            {"name": NAME, "email": "", "phone": "", "notes": None, "tags": None},
        ),
    )
    contacts = list(
        dict.fromkeys(
            _rows(
                connection, "st_contacts", person, emails=("email",), phones=("phone",)
            )
            + ids(connection, "st_contacts", "client_id", clients)
        )
    )
    names += column_values(connection, "st_contacts", "name", "id", contacts)
    tally.add(
        "st_deals",
        blank(connection, "st_deals", "contact_id", contacts, {"contact_id": ""}),
    )
    tally.add("st_contacts", delete(connection, "st_contacts", "id", contacts))
    tally.add(
        "st_proposals",
        blank(
            connection,
            "st_proposals",
            "client_id",
            clients,
            {"accepted_name": None, "accepted_from": None},
        ),
    )
    invoices = ids(connection, "st_invoices", "client_id", clients)
    proposals = ids(connection, "st_proposals", "client_id", clients)
    tally.merge(
        scrub_ledger(
            connection, clients + invoices + proposals, [*names, *person.names]
        )
    )
    return tally.counts


def erase_sites(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Websites the person built stay the business's, without their name on them."""
    tally = Tally()
    if person.profile_id:
        for table in ("sb_projects", "sb_deploys"):
            tally.add(
                table,
                blank(
                    connection,
                    table,
                    "created_by",
                    [person.profile_id],
                    {"created_by": ""},
                ),
            )
    return tally.counts


def erase_payments(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Online payment requests keep their amounts and references, without the customer's details."""
    tally = Tally()
    found = _rows(
        connection,
        "payment_requests",
        person,
        emails=("customer_email",),
        phones=("customer_phone",),
    )
    tally.add(
        "payment_requests",
        blank(
            connection,
            "payment_requests",
            "id",
            found,
            {"customer_name": "", "customer_email": "", "customer_phone": ""},
        ),
    )
    return tally.counts


def sweep(connection: sqlite3.Connection, person: Person) -> dict[str, int]:
    """Any column left, in any table, that holds one of the person's email addresses or phone numbers (an app
    added later, a contacts table) is blanked."""
    tally = Tally()
    careers = {*CAREERS_TABLES, *RECRUITER_TABLES}
    for table, column, kind in erasure.contact_columns(connection):
        if not person.keys and table in careers:
            continue  # a business erasing a customer leaves the account's own job search alone
        rowids = matching(connection, table, person, **{kind: (column,)})
        if not rowids:
            continue
        try:
            tally.add(table, blank(connection, table, "rowid", rowids, {column: None}))
        except sqlite3.IntegrityError:  # a unique column: leave it to its app's eraser
            erasure.logger.info("The sweep could not blank %s.%s", table, column)
    return tally.counts


for _app, _fn, _order, _customer in (
    ("care", erase_care, 10, True),
    ("journey", erase_journey, 10, False),
    ("family", erase_family, 10, False),
    ("planner", erase_planner, 10, True),
    ("learn", erase_learn, 10, True),
    ("play", erase_play, 10, False),
    ("news", erase_news, 10, True),
    ("stocks", erase_stocks, 10, True),
    ("ai", erase_ai, 10, False),
    ("social", erase_social, 20, False),
    ("videos", erase_videos, 20, False),
    ("careers", erase_careers, 30, False),
    ("recruiters", erase_recruiters, 30, False),
    ("leads", erase_leads, 30, True),
    ("shop", erase_shop, 40, True),
    ("bookings", erase_bookings, 40, True),
    ("marketing", erase_marketing, 40, True),
    ("service", erase_service, 40, True),
    ("events", erase_events, 40, True),
    ("academy", erase_academy, 40, True),
    ("studio", erase_studio, 40, True),
    ("sites", erase_sites, 40, False),
    ("payments", erase_payments, 40, True),
    ("outbox", erase_outbox, 80, True),
    ("notify", erase_notify, 80, False),
    ("activity", erase_activity, 85, False),
    ("sweep", sweep, 95, True),
    ("life", erase_life, 99, False),
):
    erasure.register(_app, _fn, order=_order, customer=_customer)
