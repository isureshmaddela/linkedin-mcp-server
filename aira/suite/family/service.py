"""Family's rules: the feed, chat rooms, the calendar, family dates and the public board.

Every function that reads or changes something private takes the person (``me``, from
:func:`aira.suite.life.current`) and checks it here, so the routes stay thin and the rules are tested directly.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aira.suite import kit, life
from aira.suite.family import store
from aira.suite.family.store import APP

# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------


def _person(
    names: dict[str, dict[str, Any]], profile_id: str, fallback: str = ""
) -> dict[str, Any]:
    found = names.get(profile_id)
    if found:
        return {
            "id": found["id"],
            "name": found["name"],
            "emoji": found["emoji"],
            "color": found["color"],
        }
    return {
        "id": profile_id,
        "name": fallback or "Someone",
        "emoji": "🙂",
        "color": "#64748b",
    }


def _joined(me: dict[str, Any]) -> str:
    """When this person joined, as a stamp (what was there before does not count as unread)."""
    try:
        return store.since_value(me.get("created_at") or "")
    except ValueError:
        return ""


def _adult_or_owner(me: dict[str, Any]) -> bool:
    return life.is_adult(me) or bool(me.get("owner"))


# ---------------------------------------------------------------------------
# feed
# ---------------------------------------------------------------------------


def _get_post(connection: sqlite3.Connection, post_id: str) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM fm_posts WHERE id = ?", (post_id,))
    if not row:
        raise LookupError("That post was not found.")
    return row


def _reactions(
    connection: sqlite3.Connection, post_id: str, who: str
) -> tuple[dict[str, int], list[str]]:
    counts = {emoji: 0 for emoji in store.REACTIONS}
    mine: list[str] = []
    for row in connection.execute(
        "SELECT who, emoji FROM fm_reactions WHERE post_id = ?", (post_id,)
    ):
        counts[row[1]] = counts.get(row[1], 0) + 1
        if row[0] == who:
            mine.append(row[1])
    return {k: v for k, v in counts.items() if v}, mine


def _comments(
    connection: sqlite3.Connection, post_id: str, names: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM fm_comments WHERE post_id = ? AND removed_at IS NULL ORDER BY created_at",
        (post_id,),
    )
    return [
        {
            "id": row["id"],
            "author": _person(names, row["author_id"], row["author_name"]),
            "text": row["text"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def post_view(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    *,
    who: str,
    names: dict[str, dict[str, Any]],
    public_view: bool = False,
) -> dict[str, Any]:
    reactions, mine = _reactions(connection, row["id"], who)
    author = _person(names, row["author_id"], row["author_name"])
    view = {
        "id": row["id"],
        "author": author,
        "source": row["source"],
        "text": row["text"] if row["status"] != "removed" else "",
        "photos": kit.loads(row["photos"], []) if row["status"] != "removed" else [],
        "milestone": row["milestone"],
        "pinned": bool(row["pinned"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "reactions": reactions,
        "mine": mine,
        "comments": _comments(connection, row["id"], names)
        if row["status"] != "removed"
        else [],
    }
    if public_view:
        view["author"] = {
            "name": author["name"],
            "emoji": author["emoji"],
            "color": author["color"],
        }
        view["comments"] = [
            {
                "author": {"name": c["author"]["name"], "emoji": c["author"]["emoji"]},
                "text": c["text"],
                "created_at": c["created_at"],
            }
            for c in view["comments"]
        ]
        view.pop("mine", None)
    return view


def list_posts(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    since: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """The feed, newest first (pinned on top); with ``since``, only what changed (removals come back as removed)."""
    names = store.people(connection)
    since = store.since_value(since)
    if since:
        rows = kit.fetch_all(
            connection,
            "SELECT * FROM fm_posts WHERE updated_at > ? AND status IN ('live', 'removed') ORDER BY created_at DESC LIMIT 200",
            (since,),
        )
    else:
        rows = kit.fetch_all(
            connection,
            "SELECT * FROM fm_posts WHERE status = 'live' ORDER BY pinned DESC, created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
    return {
        "posts": [
            post_view(connection, row, who=me["id"], names=names) for row in rows
        ],
        "now": store.stamp(),
        "unread": unread_posts(connection, me),
    }


def create_post(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    text = kit.text(raw.get("text"), "a message", max_len=2000, multiline=True)
    pictures = store.photos(raw.get("photos"))
    if not text and not pictures:
        raise ValueError("Write something or add a photo.")
    milestone = kit.choice(
        raw.get("milestone"), "the milestone", store.MILESTONES, default=""
    )
    moment = store.stamp()
    post_id = kit.new_id()
    connection.execute(
        """INSERT INTO fm_posts (id, author_id, author_name, source, text, photos, milestone, pinned, status, created_at, updated_at)
        VALUES (?, ?, ?, 'member', ?, ?, ?, 0, 'live', ?, ?)""",
        (
            post_id,
            me["id"],
            me["name"],
            text,
            kit.dumps(pictures),
            milestone,
            moment,
            moment,
        ),
    )
    kit.log(
        connection,
        APP,
        "post",
        f"{me['name']} shared a post",
        ref_type="post",
        ref_id=post_id,
        actor=me["name"],
    )
    connection.commit()
    return post_view(
        connection,
        _get_post(connection, post_id),
        who=me["id"],
        names=store.people(connection),
    )


def update_post(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    post_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    post = _get_post(connection, post_id)
    if post["author_id"] != me["id"] or post["status"] != "live":
        raise PermissionError("You can change your own posts only.")
    text = kit.text(
        raw.get("text", post["text"]), "a message", max_len=2000, multiline=True
    )
    pictures = (
        store.photos(raw["photos"])
        if "photos" in raw
        else kit.loads(post["photos"], [])
    )
    if not text and not pictures:
        raise ValueError("Write something or add a photo.")
    kit.update_row(
        connection,
        "fm_posts",
        post_id,
        {"text": text, "photos": kit.dumps(pictures), "updated_at": store.stamp()},
    )
    connection.commit()
    return post_view(
        connection,
        _get_post(connection, post_id),
        who=me["id"],
        names=store.people(connection),
    )


def pin_post(
    connection: sqlite3.Connection, me: dict[str, Any], post_id: str, pinned: bool
) -> dict[str, Any]:
    if not _adult_or_owner(me):
        raise PermissionError("Ask an adult to pin posts.")
    post = _get_post(connection, post_id)
    if post["status"] != "live":
        raise ValueError("Only a published post can be pinned.")
    kit.update_row(
        connection,
        "fm_posts",
        post_id,
        {"pinned": 1 if pinned else 0, "updated_at": store.stamp()},
    )
    connection.commit()
    return post_view(
        connection,
        _get_post(connection, post_id),
        who=me["id"],
        names=store.people(connection),
    )


def remove_post(
    connection: sqlite3.Connection, me: dict[str, Any], post_id: str
) -> None:
    post = _get_post(connection, post_id)
    if post["author_id"] != me["id"] and not _adult_or_owner(me):
        raise PermissionError("You can remove your own posts only.")
    kit.update_row(
        connection,
        "fm_posts",
        post_id,
        {"status": "removed", "pinned": 0, "updated_at": store.stamp()},
    )
    connection.commit()


def _touch(connection: sqlite3.Connection, post_id: str) -> None:
    connection.execute(
        "UPDATE fm_posts SET updated_at = ? WHERE id = ?", (store.stamp(), post_id)
    )


def react(
    connection: sqlite3.Connection, post_id: str, who: str, emoji: str
) -> dict[str, Any]:
    """Toggle one reaction (each person once per emoji)."""
    if emoji not in store.REACTIONS:
        raise ValueError("Choose one of " + " ".join(store.REACTIONS) + ".")
    post = _get_post(connection, post_id)
    if post["status"] != "live":
        raise LookupError("That post was not found.")
    removed = connection.execute(
        "DELETE FROM fm_reactions WHERE post_id = ? AND who = ? AND emoji = ?",
        (post_id, who, emoji),
    ).rowcount
    if not removed:
        connection.execute(
            "INSERT INTO fm_reactions (post_id, who, emoji, created_at) VALUES (?, ?, ?, ?)",
            (post_id, who, emoji, kit.now()),
        )
    _touch(connection, post_id)
    connection.commit()
    counts, mine = _reactions(connection, post_id, who)
    return {"post_id": post_id, "reactions": counts, "mine": mine}


def add_comment(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    post_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    post = _get_post(connection, post_id)
    if post["status"] != "live":
        raise LookupError("That post was not found.")
    text = kit.text(
        raw.get("text"), "a comment", max_len=1000, required=True, multiline=True
    )
    comment_id = kit.new_id()
    connection.execute(
        "INSERT INTO fm_comments (id, post_id, author_id, author_name, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, post_id, me["id"], me["name"], text, store.stamp()),
    )
    _touch(connection, post_id)
    connection.commit()
    return {
        "id": comment_id,
        "author": _person(store.people(connection), me["id"], me["name"]),
        "text": text,
    }


def remove_comment(
    connection: sqlite3.Connection, me: dict[str, Any], comment_id: str
) -> None:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM fm_comments WHERE id = ? AND removed_at IS NULL",
        (comment_id,),
    )
    if not row:
        raise LookupError("That comment was not found.")
    if row["author_id"] != me["id"] and not _adult_or_owner(me):
        raise PermissionError("You can remove your own comments only.")
    connection.execute(
        "UPDATE fm_comments SET removed_at = ? WHERE id = ?", (kit.now(), comment_id)
    )
    _touch(connection, row["post_id"])
    connection.commit()


def unread_posts(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    since = store.seen_at(connection, me["id"], "feed") or _joined(me)
    return kit.count(
        connection,
        "SELECT COUNT(*) FROM fm_posts WHERE status = 'live' AND author_id != ? AND created_at > ?",
        (me["id"], since),
    )


def pending_posts(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    if not _adult_or_owner(me):
        raise PermissionError("An adult approves posts from the board.")
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM fm_posts WHERE status = 'pending' ORDER BY created_at",
    )
    names = store.people(connection)
    return [post_view(connection, row, who=me["id"], names=names) for row in rows]


def approve_post(
    connection: sqlite3.Connection, me: dict[str, Any], post_id: str
) -> dict[str, Any]:
    if not _adult_or_owner(me):
        raise PermissionError("An adult approves posts from the board.")
    post = _get_post(connection, post_id)
    if post["status"] != "pending":
        raise ValueError("That post is not waiting for approval.")
    moment = store.stamp()
    kit.update_row(
        connection,
        "fm_posts",
        post_id,
        {"status": "live", "created_at": moment, "updated_at": moment},
    )
    kit.log(
        connection,
        APP,
        "approve",
        f"{me['name']} approved a post from {post['author_name']}",
        ref_type="post",
        ref_id=post_id,
        actor=me["name"],
    )
    connection.commit()
    return post_view(
        connection,
        _get_post(connection, post_id),
        who=me["id"],
        names=store.people(connection),
    )


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def group_room(connection: sqlite3.Connection) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM fm_rooms WHERE kind = 'group'")
    if row:
        return row
    room_id = kit.new_id()
    connection.execute(
        "INSERT OR IGNORE INTO fm_rooms (id, kind, member_a, member_b, created_at) VALUES (?, 'group', '', '', ?)",
        (room_id, kit.now()),
    )
    connection.commit()
    return (
        kit.fetch_one(connection, "SELECT * FROM fm_rooms WHERE kind = 'group'") or {}
    )


def direct_room(
    connection: sqlite3.Connection, me: dict[str, Any], other_id: str
) -> dict[str, Any]:
    if other_id == me["id"]:
        raise ValueError("Choose someone else to talk to.")
    life.get(connection, other_id)  # 404 when they are not in the household
    first, second = sorted((me["id"], other_id))
    row = kit.fetch_one(
        connection,
        "SELECT * FROM fm_rooms WHERE kind = 'direct' AND member_a = ? AND member_b = ?",
        (first, second),
    )
    if row:
        return row
    connection.execute(
        "INSERT OR IGNORE INTO fm_rooms (id, kind, member_a, member_b, created_at) VALUES (?, 'direct', ?, ?, ?)",
        (kit.new_id(), first, second, kit.now()),
    )
    connection.commit()
    return (
        kit.fetch_one(
            connection,
            "SELECT * FROM fm_rooms WHERE kind = 'direct' AND member_a = ? AND member_b = ?",
            (first, second),
        )
        or {}
    )


def room_for(
    connection: sqlite3.Connection, me: dict[str, Any], room_id: str
) -> dict[str, Any]:
    """The room, if this person is in it (a one-to-one room is the same 404 for anyone else)."""
    row = kit.fetch_one(connection, "SELECT * FROM fm_rooms WHERE id = ?", (room_id,))
    if not row or (
        row["kind"] == "direct" and me["id"] not in (row["member_a"], row["member_b"])
    ):
        raise LookupError("That conversation was not found.")
    return row


def _last_read(connection: sqlite3.Connection, room_id: str, profile_id: str) -> str:
    row = connection.execute(
        "SELECT last_read_at FROM fm_reads WHERE room_id = ? AND profile_id = ?",
        (room_id, profile_id),
    ).fetchone()
    return str(row[0]) if row else ""


def unread_in(connection: sqlite3.Connection, room_id: str, me: dict[str, Any]) -> int:
    since = _last_read(connection, room_id, me["id"]) or _joined(me)
    return kit.count(
        connection,
        "SELECT COUNT(*) FROM fm_messages WHERE room_id = ? AND author_id != ? AND deleted_at IS NULL AND created_at > ?",
        (room_id, me["id"], since),
    )


def _my_rooms(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    group_room(connection)
    return kit.fetch_all(
        connection,
        "SELECT * FROM fm_rooms WHERE kind = 'group' OR member_a = ? OR member_b = ? ORDER BY kind DESC, created_at",
        (me["id"], me["id"]),
    )


def list_rooms(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    names = store.people(connection)
    out = []
    for room in _my_rooms(connection, me):
        other = None
        if room["kind"] == "direct":
            other_id = (
                room["member_b"] if room["member_a"] == me["id"] else room["member_a"]
            )
            if other_id not in names:
                continue  # the other person left the household
            other = _person(names, other_id)
        last = kit.fetch_one(
            connection,
            "SELECT * FROM fm_messages WHERE room_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (room["id"],),
        )
        out.append(
            {
                "id": room["id"],
                "kind": room["kind"],
                "title": "Family chat"
                if room["kind"] == "group"
                else other["name"]
                if other
                else "Chat",
                "other": other,
                "unread": unread_in(connection, room["id"], me),
                "last": {
                    "author": _person(names, last["author_id"])["name"],
                    "text": last["text"] or ("a photo" if last["photo"] else ""),
                    "created_at": last["created_at"],
                }
                if last
                else None,
            }
        )
    group = [r for r in out if r["kind"] == "group"]
    direct = sorted(
        (r for r in out if r["kind"] == "direct"),
        key=lambda r: (r["last"] or {}).get("created_at", ""),
        reverse=True,
    )
    return group + direct


def _message_view(
    row: dict[str, Any], names: dict[str, dict[str, Any]], me_id: str
) -> dict[str, Any]:
    deleted = bool(row["deleted_at"])
    return {
        "id": row["id"],
        "room_id": row["room_id"],
        "author": _person(names, row["author_id"]),
        "mine": row["author_id"] == me_id,
        "text": "" if deleted else row["text"],
        "photo": "" if deleted else row["photo"],
        "deleted": deleted,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def messages(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    *,
    since: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    room = room_for(connection, me, room_id)
    names = store.people(connection)
    since = store.since_value(since)
    if since:
        rows = kit.fetch_all(
            connection,
            "SELECT * FROM fm_messages WHERE room_id = ? AND updated_at > ? ORDER BY created_at",
            (room["id"], since),
        )
    else:
        rows = list(
            reversed(
                kit.fetch_all(
                    connection,
                    "SELECT * FROM fm_messages WHERE room_id = ? ORDER BY created_at DESC LIMIT ?",
                    (room["id"], max(1, min(limit, 300))),
                )
            )
        )
    members = (
        [p for p in names if p != me["id"]]
        if room["kind"] == "group"
        else [room["member_b"] if room["member_a"] == me["id"] else room["member_a"]]
    )
    receipts = {pid: _last_read(connection, room["id"], pid) for pid in members}
    return {
        "room": {"id": room["id"], "kind": room["kind"]},
        "messages": [_message_view(row, names, me["id"]) for row in rows],
        "read_by": [
            {**_person(names, pid), "last_read_at": at}
            for pid, at in receipts.items()
            if at
        ],
        "now": store.stamp(),
    }


def send_message(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    room = room_for(connection, me, room_id)
    text = kit.text(raw.get("text"), "a message", max_len=2000, multiline=True)
    photo = store.photos([raw["photo"]])[0] if raw.get("photo") else ""
    if not text and not photo:
        raise ValueError("Write a message or add a photo.")
    moment = store.stamp()
    message_id = kit.new_id()
    connection.execute(
        "INSERT INTO fm_messages (id, room_id, author_id, text, photo, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (message_id, room["id"], me["id"], text, photo, moment, moment),
    )
    # Writing in a room means you have read it.
    connection.execute(
        """INSERT INTO fm_reads (room_id, profile_id, last_read_at) VALUES (?, ?, ?)
        ON CONFLICT(room_id, profile_id) DO UPDATE SET last_read_at = excluded.last_read_at""",
        (room["id"], me["id"], moment),
    )
    connection.commit()
    row = (
        kit.fetch_one(
            connection, "SELECT * FROM fm_messages WHERE id = ?", (message_id,)
        )
        or {}
    )
    return _message_view(row, store.people(connection), me["id"])


def mark_read(
    connection: sqlite3.Connection, me: dict[str, Any], room_id: str
) -> dict[str, Any]:
    room = room_for(connection, me, room_id)
    moment = store.stamp()
    connection.execute(
        """INSERT INTO fm_reads (room_id, profile_id, last_read_at) VALUES (?, ?, ?)
        ON CONFLICT(room_id, profile_id) DO UPDATE SET last_read_at = excluded.last_read_at""",
        (room["id"], me["id"], moment),
    )
    connection.commit()
    return {"room_id": room["id"], "last_read_at": moment, "unread": 0}


def delete_message(
    connection: sqlite3.Connection, me: dict[str, Any], message_id: str
) -> None:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM fm_messages WHERE id = ? AND deleted_at IS NULL",
        (message_id,),
    )
    if not row:
        raise LookupError("That message was not found.")
    room_for(connection, me, row["room_id"])
    if row["author_id"] != me["id"]:
        raise PermissionError("You can delete your own messages only.")
    moment = store.stamp()
    connection.execute(
        "UPDATE fm_messages SET deleted_at = ?, text = '', photo = '', updated_at = ? WHERE id = ?",
        (moment, moment, message_id),
    )
    connection.commit()


def unread_messages(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    return sum(
        unread_in(connection, room["id"], me) for room in _my_rooms(connection, me)
    )


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


def _invitees(connection: sqlite3.Connection, value: Any) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    names = store.people(connection)
    out = []
    for item in items:
        profile_id = str(item).strip()
        if not profile_id:
            continue
        if profile_id not in names:
            raise ValueError("Invite people from the household.")
        if profile_id not in out:
            out.append(profile_id)
    return out


def _get_event(connection: sqlite3.Connection, event_id: str) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM fm_events WHERE id = ?", (event_id,))
    if not row:
        raise LookupError("That event was not found.")
    return row


def _invited(event: dict[str, Any], me_id: str) -> bool:
    invitees = kit.loads(event["invitees"], []) or []
    return not invitees or me_id in invitees or event["created_by"] == me_id


def _event_values(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    all_day = kit.flag(merged.get("all_day"))
    if all_day:
        start_day = kit.day(
            str(merged.get("starts_at") or "")[:10], "the day", required=True
        )
        starts_at = f"{start_day}T00:00:00Z"
        end_day = (
            kit.day(str(merged.get("ends_at") or "")[:10], "the last day") or start_day
        )
        ends_at = f"{end_day}T23:59:00Z"
    else:
        starts_at = kit.moment(merged.get("starts_at"), "when it starts", required=True)
        ends_at = kit.moment(merged.get("ends_at"), "when it ends")
    if ends_at and ends_at < starts_at:
        raise ValueError("The event cannot end before it starts.")
    return {
        "title": kit.text(merged.get("title"), "a title", max_len=120, required=True),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "all_day": 1 if all_day else 0,
        "place": kit.text(merged.get("place"), "a place", max_len=200),
        "notes": kit.text(merged.get("notes"), "notes", max_len=2000, multiline=True),
        "invitees": kit.dumps(_invitees(connection, merged.get("invitees"))),
    }


def event_view(
    connection: sqlite3.Connection,
    event: dict[str, Any],
    me_id: str,
    names: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    answers: dict[str, list[dict[str, Any]]] = {
        answer: [] for answer in store.RSVP_ANSWERS
    }
    mine = ""
    for row in connection.execute(
        "SELECT profile_id, answer FROM fm_rsvps WHERE event_id = ? ORDER BY answered_at, profile_id",
        (event["id"],),
    ):
        if row[0] in names:
            answers.setdefault(row[1], []).append(_person(names, row[0]))
        if row[0] == me_id:
            mine = row[1]
    invitees = kit.loads(event["invitees"], []) or []
    return {
        "id": event["id"],
        "title": event["title"],
        "starts_at": event["starts_at"],
        "ends_at": event["ends_at"],
        "all_day": bool(event["all_day"]),
        "place": event["place"],
        "notes": event["notes"],
        "everyone": not invitees,
        "invitees": [_person(names, pid) for pid in invitees if pid in names],
        "created_by": _person(names, event["created_by"]),
        "answers": answers,
        "my_answer": mine,
        "cancelled": bool(event["cancelled_at"]),
        "mine": event["created_by"] == me_id,
    }


def create_event(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    values = _event_values(connection, raw)
    event_id = kit.new_id()
    connection.execute(
        """INSERT INTO fm_events (id, title, starts_at, ends_at, all_day, place, notes, invitees, created_by, created_at, updated_at)
        VALUES (:id, :title, :starts_at, :ends_at, :all_day, :place, :notes, :invitees, :created_by, :now, :now)""",
        {**values, "id": event_id, "created_by": me["id"], "now": kit.now()},
    )
    connection.execute(
        "INSERT INTO fm_rsvps (event_id, profile_id, answer, answered_at) VALUES (?, ?, 'yes', ?)",
        (event_id, me["id"], kit.now()),
    )
    kit.log(
        connection,
        APP,
        "event",
        f"{me['name']} added {values['title']} to the calendar",
        ref_type="event",
        ref_id=event_id,
        actor=me["name"],
    )
    connection.commit()
    return event_view(
        connection, _get_event(connection, event_id), me["id"], store.people(connection)
    )


def update_event(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    event_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    event = _get_event(connection, event_id)
    if event["created_by"] != me["id"] and not _adult_or_owner(me):
        raise PermissionError("Ask the person who added it, or an adult, to change it.")
    current = {
        **event,
        "all_day": bool(event["all_day"]),
        "invitees": kit.loads(event["invitees"], []),
    }
    values = _event_values(connection, raw, current)
    kit.update_row(
        connection, "fm_events", event_id, {**values, "updated_at": kit.now()}
    )
    connection.commit()
    return event_view(
        connection, _get_event(connection, event_id), me["id"], store.people(connection)
    )


def cancel_event(
    connection: sqlite3.Connection, me: dict[str, Any], event_id: str
) -> None:
    event = _get_event(connection, event_id)
    if event["created_by"] != me["id"] and not _adult_or_owner(me):
        raise PermissionError("Ask the person who added it, or an adult, to cancel it.")
    kit.update_row(
        connection,
        "fm_events",
        event_id,
        {"cancelled_at": kit.now(), "updated_at": kit.now()},
    )
    connection.commit()


def rsvp(
    connection: sqlite3.Connection, me: dict[str, Any], event_id: str, answer: str
) -> dict[str, Any]:
    event = _get_event(connection, event_id)
    if event["cancelled_at"]:
        raise ValueError("That event was cancelled.")
    if not _invited(event, me["id"]):
        raise LookupError("That event was not found.")
    answer = kit.choice(answer, "the answer", store.RSVP_ANSWERS)
    connection.execute(
        """INSERT INTO fm_rsvps (event_id, profile_id, answer, answered_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(event_id, profile_id) DO UPDATE SET answer = excluded.answer, answered_at = excluded.answered_at""",
        (event_id, me["id"], answer, kit.now()),
    )
    connection.commit()
    return event_view(connection, event, me["id"], store.people(connection))


def list_events(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    start: str = "",
    end: str = "",
    include_cancelled: bool = False,
) -> list[dict[str, Any]]:
    start = (
        kit.moment(start, "the start")
        if start
        else kit.iso(kit.utcnow() - timedelta(days=1))
    )
    end = (
        kit.moment(end, "the end")
        if end
        else kit.iso(
            kit.utcnow() + timedelta(days=5 * 366)
        )  # everything planned, up to five years ahead
    )
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM fm_events WHERE starts_at <= ? AND (CASE WHEN ends_at != '' THEN ends_at ELSE starts_at END) >= ? ORDER BY starts_at",
        (end, start),
    )
    names = store.people(connection)
    return [
        event_view(connection, row, me["id"], names)
        for row in rows
        if _invited(row, me["id"]) and (include_cancelled or not row["cancelled_at"])
    ]


# ---------------------------------------------------------------------------
# family dates
# ---------------------------------------------------------------------------


def add_date(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    name = kit.text(raw.get("name"), "a name", max_len=60, required=True)
    when = kit.day(raw.get("date"), "the date", required=True)
    kind = kit.choice(raw.get("kind"), "the kind", store.DATE_KINDS, default="birthday")
    year_known = kit.flag(raw.get("year_known", True))
    date_id = kit.new_id()
    connection.execute(
        "INSERT INTO fm_dates (id, name, date, kind, relation, year_known, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            date_id,
            name,
            when,
            kind,
            kit.text(raw.get("relation"), "the relation", max_len=40),
            1 if year_known else 0,
            me["id"],
            kit.now(),
        ),
    )
    connection.commit()
    return next(d for d in list_dates(connection) if d["id"] == date_id)


def delete_date(
    connection: sqlite3.Connection, me: dict[str, Any], date_id: str
) -> None:
    row = kit.require_row(connection, "fm_dates", date_id, "date")
    if row["created_by"] != me["id"] and not _adult_or_owner(me):
        raise PermissionError("Ask an adult to remove it.")
    connection.execute("DELETE FROM fm_dates WHERE id = ?", (date_id,))
    connection.commit()


def _next(born: date, today: date) -> date:
    for year in (today.year, today.year + 1):
        try:
            candidate = born.replace(year=year)
        except ValueError:  # 29 February
            candidate = date(year, 2, 28)
        if candidate >= today:
            return candidate
    return born.replace(year=today.year + 1)


def list_dates(
    connection: sqlite3.Connection,
    *,
    today: date | None = None,
    within: int | None = None,
) -> list[dict[str, Any]]:
    """Household birthdays (from profiles) and the dates the family added, soonest first."""
    today = today or datetime.now(timezone.utc).date()
    out: list[dict[str, Any]] = []
    for person in life.profiles(connection):
        if person["birthday"]:
            out.append(
                {
                    "id": f"profile:{person['id']}",
                    "name": person["name"],
                    "date": person["birthday"],
                    "kind": "birthday",
                    "relation": "household",
                    "year_known": True,
                    "profile_id": person["id"],
                    "emoji": person["emoji"],
                }
            )
    for row in kit.fetch_all(connection, "SELECT * FROM fm_dates"):
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "date": row["date"],
                "kind": row["kind"],
                "relation": row["relation"],
                "year_known": bool(row["year_known"]),
                "profile_id": "",
                "emoji": "🎂" if row["kind"] == "birthday" else "💐",
            }
        )
    for entry in out:
        born = date.fromisoformat(entry["date"])
        upcoming = _next(born, today)
        entry["next"] = upcoming.isoformat()
        entry["in_days"] = (upcoming - today).days
        entry["years"] = (
            upcoming.year - born.year
            if entry["year_known"] and born.year > 1900
            else None
        )
    out.sort(key=lambda e: (e["in_days"], e["name"]))
    if within is not None:
        out = [e for e in out if e["in_days"] <= within]
    return out


def wish(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    name = kit.text(raw.get("name"), "a name", max_len=60, required=True)
    kind = kit.choice(raw.get("kind"), "the kind", store.DATE_KINDS, default="birthday")
    text = {
        "birthday": f"Happy birthday, {name}! 🎂 Wishing you a wonderful day.",
        "anniversary": f"Happy anniversary, {name}! 💐",
        "other": f"Thinking of you today, {name}! 💛",
    }[kind]
    note = kit.text(raw.get("note"), "a note", max_len=500, multiline=True)
    return create_post(
        connection,
        me,
        {
            "text": f"{text}\n{note}".strip(),
            "milestone": "birthday" if kind == "birthday" else "other",
        },
    )


def wished_recently(
    connection: sqlite3.Connection, me: dict[str, Any], name: str
) -> bool:
    since = kit.iso(kit.utcnow() - timedelta(days=3))
    return bool(
        kit.count(
            connection,
            "SELECT COUNT(*) FROM fm_posts WHERE author_id = ? AND status = 'live' AND created_at > ? AND text LIKE ?",
            (me["id"], since, f"%{name}!%"),
        )
    )


# ---------------------------------------------------------------------------
# the public board
# ---------------------------------------------------------------------------

BOARD_REF = "family"
BOARD_DEFAULTS = {"title": "Our family", "auto_publish": False}


def board_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "board", {}) or {}
    return {
        **BOARD_DEFAULTS,
        **{k: v for k, v in stored.items() if k in BOARD_DEFAULTS},
    }


def save_board_settings(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    if not _adult_or_owner(me):
        raise PermissionError("Ask an adult to change the family board.")
    current = board_settings(connection)
    values = {
        "title": kit.text(raw.get("title", current["title"]), "a title", max_len=60)
        or BOARD_DEFAULTS["title"],
        "auto_publish": kit.flag(raw.get("auto_publish", current["auto_publish"])),
    }
    kit.set_setting(connection, APP, "board", values)
    connection.commit()
    return values


def visitor_key(visitor: dict[str, Any]) -> str:
    return (
        "visitor:"
        + hashlib.sha256(str(visitor.get("client") or "").encode()).hexdigest()[:16]
    )


def board_page(connection: sqlite3.Connection) -> dict[str, Any]:
    settings = board_settings(connection)
    names = store.people(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM fm_posts WHERE status = 'live' ORDER BY pinned DESC, created_at DESC LIMIT 50",
    )
    return {
        "title": settings["title"],
        "auto_publish": settings["auto_publish"],
        "reactions": list(store.REACTIONS),
        "people": [
            {"name": p["name"], "emoji": p["emoji"], "color": p["color"]}
            for p in names.values()
        ],
        "posts": [
            post_view(connection, row, who="", names=names, public_view=True)
            for row in rows
        ],
    }


def board_post(
    connection: sqlite3.Connection, payload: dict[str, Any], visitor: dict[str, Any]
) -> dict[str, Any]:
    name = kit.text(payload.get("name"), "your name", max_len=40, required=True)
    text = kit.text(
        payload.get("text"), "a message", max_len=1000, required=True, multiline=True
    )
    settings = board_settings(connection)
    status = "live" if settings["auto_publish"] else "pending"
    moment = store.stamp()
    post_id = kit.new_id()
    connection.execute(
        """INSERT INTO fm_posts (id, author_id, author_name, source, text, photos, milestone, pinned, status, created_at, updated_at)
        VALUES (?, '', ?, 'board', ?, '[]', '', 0, ?, ?, ?)""",
        (post_id, name, text, status, moment, moment),
    )
    kit.log(
        connection,
        APP,
        "board_post",
        f"{name} wrote on the family board"
        + ("" if status == "live" else " (waiting for approval)"),
        ref_type="post",
        ref_id=post_id,
        public=True,
    )
    connection.commit()
    return {"status": status, "id": post_id}


def board_react(
    connection: sqlite3.Connection, payload: dict[str, Any], visitor: dict[str, Any]
) -> dict[str, Any]:
    return react(
        connection,
        str(payload.get("post_id") or ""),
        visitor_key(visitor),
        str(payload.get("emoji") or ""),
    )


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


def _when(value: str, all_day: bool) -> str:
    moment = kit.parse_time(value)
    today = kit.utcnow().date()
    label = (
        "Today"
        if moment.date() == today
        else "Tomorrow"
        if moment.date() == today + timedelta(days=1)
        else moment.strftime("%a %d %b")
    )
    return label if all_day else f"{label} at {moment.strftime('%H:%M')} UTC"


def today_cards(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    cards = []
    lines = []
    for room in list_rooms(connection, me):
        if room["unread"]:
            where = (
                "in the family chat"
                if room["kind"] == "group"
                else f"from {room['title']}"
            )
            lines.append(
                {
                    "text": f"{room['unread']} new message{'s' if room['unread'] != 1 else ''} {where}",
                    "sub": (room["last"] or {}).get("text", "")[:80],
                }
            )
    posts = unread_posts(connection, me)
    if posts:
        lines.append(
            {
                "text": f"{posts} new post{'s' if posts != 1 else ''} in the family feed",
                "sub": "See what everyone shared",
            }
        )
    if lines:
        cards.append(
            life.card(
                APP,
                "Messages from family",
                lines=lines,
                link="#/family?tab=chat",
                tone="warn",
            )
        )
    soon = kit.iso(kit.utcnow() + timedelta(days=7))
    events = [
        e
        for e in list_events(
            connection, me, start=kit.iso(kit.utcnow() - timedelta(hours=2)), end=soon
        )
    ]
    if events:
        event_lines = []
        for event in events[:5]:
            line = {
                "text": event["title"],
                "sub": " · ".join(
                    part
                    for part in (
                        _when(event["starts_at"], event["all_day"]),
                        event["place"],
                    )
                    if part
                ),
                "done": event["my_answer"] == "yes",
            }
            if event["my_answer"] != "yes":
                line["action"] = life.action(
                    "Going",
                    f"/api/family/events/{event['id']}/rsvp",
                    body={"answer": "yes"},
                )
            event_lines.append(line)
        cards.append(
            life.card(
                APP,
                "Family calendar",
                lines=event_lines,
                link="#/family?tab=calendar",
                icon="family",
            )
        )
    dates = list_dates(connection, within=7)
    if dates:
        date_lines = []
        for entry in dates[:5]:
            if entry.get("profile_id") == me["id"]:
                date_lines.append(
                    {
                        "text": "Your birthday"
                        if entry["in_days"]
                        else "Happy birthday to you! 🎂",
                        "sub": "in %d days" % entry["in_days"]
                        if entry["in_days"]
                        else "Today",
                    }
                )
                continue
            when = (
                "today"
                if entry["in_days"] == 0
                else "tomorrow"
                if entry["in_days"] == 1
                else f"in {entry['in_days']} days"
            )
            turning = (
                f" (turning {entry['years']})"
                if entry["years"] and entry["kind"] == "birthday"
                else ""
            )
            done = wished_recently(connection, me, entry["name"])
            line = {
                "text": f"{entry['name']}'s {'birthday' if entry['kind'] == 'birthday' else entry['kind']} {when}{turning}",
                "sub": entry["relation"]
                if entry["relation"] not in ("", "household")
                else "",
                "done": done,
            }
            if not done:
                line["action"] = life.action(
                    "Send wishes",
                    "/api/family/wishes",
                    body={"name": entry["name"], "kind": entry["kind"]},
                )
            date_lines.append(line)
        cards.append(
            life.card(
                APP,
                "Birthdays and dates this week",
                lines=date_lines,
                link="#/family?tab=dates",
                icon="family",
            )
        )
    return cards


def pulse(
    connection: sqlite3.Connection, me: dict[str, Any], since: str
) -> dict[str, Any]:
    badge = unread_messages(connection, me) + unread_posts(connection, me)
    alerts = []
    recent = store.since_value(kit.iso(kit.utcnow() - timedelta(minutes=30)))
    names = store.people(connection)
    for room in _my_rooms(connection, me):
        if room["kind"] != "direct":
            continue
        last_read = _last_read(connection, room["id"], me["id"]) or _joined(me)
        rows = kit.fetch_all(
            connection,
            "SELECT * FROM fm_messages WHERE room_id = ? AND author_id != ? AND deleted_at IS NULL AND created_at > ? ORDER BY created_at DESC LIMIT 3",
            (room["id"], me["id"], max(last_read, recent)),
        )
        for row in rows:
            sender = _person(names, row["author_id"])["name"]
            alerts.append(
                {
                    "id": f"fm:msg:{row['id']}",
                    "title": f"Message from {sender}",
                    "body": row["text"][:160] or "Sent a photo",
                    "tone": "",
                    "speak": True,
                    "actions": [
                        life.action(
                            "Mark as read", f"/api/family/rooms/{room['id']}/read"
                        )
                    ],
                }
            )
    now = kit.utcnow()
    hour = kit.iso(now + timedelta(hours=1))
    for event in kit.fetch_all(
        connection,
        """SELECT e.* FROM fm_events e JOIN fm_rsvps r ON r.event_id = e.id AND r.profile_id = ? AND r.answer = 'yes'
        WHERE e.cancelled_at IS NULL AND e.all_day = 0 AND e.starts_at > ? AND e.starts_at <= ?""",
        (me["id"], kit.iso(now), hour),
    ):
        minutes = max(
            1, int((kit.parse_time(event["starts_at"]) - now).total_seconds() // 60)
        )
        alerts.append(
            {
                "id": f"fm:event:{event['id']}:{event['starts_at']}",
                "title": f"Starting soon: {event['title']}",
                "body": f"In {minutes} minutes"
                + (f" at {event['place']}" if event["place"] else ""),
                "tone": "warn",
                "speak": True,
                "actions": [],
            }
        )
    return {"badge": badge, "alerts": alerts[:6]}


def overview(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    names = store.people(connection)
    return {
        "me": life.mine(me),
        "people": list(names.values()),
        "is_adult": _adult_or_owner(me),
        "unread": {
            "messages": unread_messages(connection, me),
            "posts": unread_posts(connection, me),
        },
        "reactions": list(store.REACTIONS),
        "milestones": [m for m in store.MILESTONES if m],
        "board": board_settings(connection),
        "pending": kit.count(
            connection, "SELECT COUNT(*) FROM fm_posts WHERE status = 'pending'"
        )
        if _adult_or_owner(me)
        else 0,
    }
