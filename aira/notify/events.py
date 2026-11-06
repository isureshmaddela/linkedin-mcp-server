"""Where Social, Dating and Play tell people about something new: a live hint to the open pages of everyone it
concerns, and for messages and matches a notification (``messages``).

The routes call these after the change is saved. Nothing here may fail the action: errors are logged.
Notifications about Dating never name anyone (they may show on a lock screen).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from aira import notify

logger = logging.getLogger("aira.notify")


def _members(sc: sqlite3.Connection, chat_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in sc.execute(
            """SELECT m.account_id, m.muted, a.person_key, a.home FROM so_chat_members m
            JOIN so_accounts a ON a.id = m.account_id WHERE m.chat_id = ? AND m.left_at IS NULL""",
            (chat_id,),
        ).fetchall()
    ]


def chat_message(sc: sqlite3.Connection, author: dict[str, Any], chat_id: str) -> None:
    """A message in a Social chat: every member's pages refresh it; the others are told unless they muted it."""
    try:
        for member in _members(sc, chat_id):
            notify.publish(
                member["home"], "chat", {"chat": chat_id}, person=member["person_key"]
            )
            if member["account_id"] == author.get("id") or member["muted"]:
                continue
            notify.notify_home(
                member["home"],
                member["person_key"],
                "messages",
                f"New message from {author.get('display_name') or 'a friend'}",
                "Open Social to read it.",
                f"/#/social?tab=chats&chat={chat_id}",
                collapse=f"chat:{chat_id}",
            )
    except Exception:  # noqa: BLE001 - the message is sent; only the heads-up failed
        logger.exception("Telling chat members about a message failed")


def chat_changed(sc: sqlite3.Connection, chat_id: str) -> None:
    """Something else changed in a chat (read receipts, a deleted message): the open pages refresh."""
    try:
        for member in _members(sc, chat_id):
            notify.publish(
                member["home"], "chat", {"chat": chat_id}, person=member["person_key"]
            )
    except Exception:  # noqa: BLE001
        logger.exception("A chat update failed")


def _match_people(sc: sqlite3.Connection, match_id: str) -> list[dict[str, Any]]:
    found = sc.execute(
        "SELECT a_id, b_id FROM so_dating_matches WHERE id = ?", (match_id,)
    ).fetchone()
    if not found:
        return []
    return [
        dict(row)
        for row in sc.execute(
            "SELECT id, person_key, home FROM so_accounts WHERE id IN (?, ?)",
            (found[0], found[1]),
        ).fetchall()
    ]


def dating_match(sc: sqlite3.Connection, me: dict[str, Any], match_id: str) -> None:
    """A like back made a match: both see it at once, the other person is told."""
    try:
        for person in _match_people(sc, match_id):
            notify.publish(
                person["home"],
                "dating",
                {"match": match_id},
                person=person["person_key"],
            )
            if person["id"] == me.get("id"):
                continue
            notify.notify_home(
                person["home"],
                person["person_key"],
                "messages",
                "You have a new match",
                "Open Dating to say hello.",
                f"/#/social?tab=dating&dv=matches&match={match_id}",
                key=f"match:{match_id}",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Telling a new match failed")


def dating_message(sc: sqlite3.Connection, me: dict[str, Any], match_id: str) -> None:
    try:
        for person in _match_people(sc, match_id):
            notify.publish(
                person["home"],
                "dating",
                {"match": match_id},
                person=person["person_key"],
            )
            if person["id"] == me.get("id"):
                continue
            notify.notify_home(
                person["home"],
                person["person_key"],
                "messages",
                "New message in Dating",
                "Open Dating to read it.",
                f"/#/social?tab=dating&dv=matches&match={match_id}",
                collapse=f"dating:{match_id}",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Telling a Dating message failed")


def dating_changed(sc: sqlite3.Connection, match_id: str) -> None:
    try:
        for person in _match_people(sc, match_id):
            notify.publish(
                person["home"],
                "dating",
                {"match": match_id},
                person=person["person_key"],
            )
    except Exception:  # noqa: BLE001
        logger.exception("A Dating update failed")


def trivia(home: str, room_id: str) -> None:
    """A trivia room changed (someone joined, answered, the host moved on): everyone in the household who has it
    open refreshes. Rooms are the household's, so the hint is for the whole account."""
    notify.publish(home, "trivia", {"room": room_id})
