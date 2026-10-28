"""Social's rules: who sees what, who may talk to whom, and what happens when someone posts, chats or reports.

Everything here takes ``sc`` (a connection to the platform-wide social database, db.py) and an account dict.

Privacy, in one place:
* Blocking hides everything both ways: posts, comments, stories, profiles, search, chats, friend requests.
* A post is visible to its author; ``only_me`` to nobody else; ``friends`` to friends; ``public`` to anyone; a group
  post to the group's members (anyone, if the group is public); a page post to anyone. Hidden (moderated) or
  deleted posts are visible to nobody, and a suspended account's content disappears.
* Children (age group ``child``) are private, cannot be found by strangers, never post publicly, receive messages
  only from friends, cannot create groups or pages and join only private groups an admin approves. Teens cannot
  open their messages to strangers.
* Chats are for their members only; a direct chat starts between friends (or with an adult who opened their
  messages); a blocked person can never message.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from aira.suite import kit, life
from aira.suite.social import store
from aira.suite.social.store import new_id, row, rows, stamp

REACTIONS = ("❤️", "👍", "😂", "😮", "😢", "🎉")
AUDIENCES = ("public", "friends", "only_me")
VISIBILITIES = ("public", "private")
REPORT_TARGETS = ("post", "comment", "message", "account", "story", "listing")
LIMITS = {
    "post": 30,
    "comment": 120,
    "message": 400,
    "friend_request": 40,
    "report": 30,
    "story": 20,
    "group": 10,
    "page": 10,
}
DEFAULT_BLOCKLIST = ("fuck", "shit", "bitch", "bastard", "cunt", "motherfucker")
HANDLE = re.compile(r"^[a-z][a-z0-9_.]{2,29}$")
MENTION = re.compile(r"(?<![\w@])@([a-z][a-z0-9_.]{2,29})")
TAG = re.compile(r"(?<![\w#])#(\w{2,40})")
DELETE_WINDOW_HOURS = 1
STORY_HOURS = 24


class SetupRequired(LookupError):
    """The person has no Social profile yet (the screen asks for a handle)."""

    code = "social_setup"


# ---------------------------------------------------------------------------
# identity and accounts
# ---------------------------------------------------------------------------


def person_key(profile: dict[str, Any]) -> str:
    """One per person on the platform: the hosted member, or the household profile (ids are random)."""
    member = profile.get("member_ref")
    return f"m:{member}" if member else f"p:{profile['id']}"


def identity(request: Any, connection: sqlite3.Connection) -> dict[str, Any]:
    profile = life.current(request, connection)
    user = getattr(request, "user", None) or {}
    hosted = bool(profile.get("member_ref"))
    return {
        "key": person_key(profile),
        "profile": profile,
        "home": str(user.get("org_id") or f"local:{user.get('user_id') or ''}"),
        "moderator": bool(profile.get("owner")) and not hosted,
        "hosted": hosted,
    }


def minor(account: dict[str, Any]) -> bool:
    return account.get("age_group") == "child"


def account_by_key(sc: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    return _account(row(sc, "SELECT * FROM so_accounts WHERE person_key = ?", (key,)))


def account(sc: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    found = _account(
        row(
            sc,
            "SELECT * FROM so_accounts WHERE id = ?",
            (store.check_id(account_id, "person"),),
        )
    )
    if not found:
        raise LookupError("That person was not found.")
    return found


def _account(found: dict[str, Any] | None) -> dict[str, Any] | None:
    if not found:
        return None
    found["skills"] = store.loads(found.get("skills"), [])
    found["experience"] = store.loads(found.get("experience"), [])
    found["open_messages"] = bool(found.get("open_messages"))
    found["open_to_work"] = bool(found.get("open_to_work"))
    return found


def me(sc: sqlite3.Connection, ident: dict[str, Any]) -> dict[str, Any]:
    """The person's account (kept in step with their household profile), or SetupRequired."""
    found = account_by_key(sc, ident["key"])
    if not found:
        raise SetupRequired("Choose your Social name first.")
    group = ident["profile"].get("age_group") or "adult"
    changes: dict[str, Any] = {"last_seen_at": stamp()}
    if group != found["age_group"]:
        changes["age_group"] = group
    if group == "child":
        changes.update({"visibility": "private", "open_messages": 0})
    elif group == "teen" and found["open_messages"]:
        changes["open_messages"] = 0
    _update(sc, "so_accounts", found["id"], changes)
    sc.commit()
    return account(sc, found["id"])


def suggest_handle(sc: sqlite3.Connection, name: str) -> str:
    base = re.sub(r"[^a-z0-9]", "", str(name or "").lower())[:20]
    if not base or not base[0].isalpha():
        base = f"friend{base}"[:20]
    base = base if len(base) >= 3 else f"{base}aira"
    candidate = base
    number = 1
    while row(sc, "SELECT 1 AS x FROM so_accounts WHERE handle = ?", (candidate,)):
        number += 1
        candidate = f"{base}{number}"
    return candidate


def _clean_handle(value: Any) -> str:
    handle = str(value or "").strip().lstrip("@").lower()
    if not HANDLE.fullmatch(handle):
        raise ValueError(
            "A Social name is 3 to 30 letters, numbers, dots or underscores, starting with a letter."
        )
    return handle


def _avatar(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if text.lower().startswith("https://"):
        return kit.url(text, "the picture link")
    if len(text) > 8:
        raise ValueError("Use an emoji or an https:// picture link.")
    return text


def create_account(
    sc: sqlite3.Connection, ident: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    if account_by_key(sc, ident["key"]):
        raise ValueError("You already have a Social profile.")
    profile = ident["profile"]
    handle = _clean_handle(raw.get("handle"))
    if row(sc, "SELECT 1 AS x FROM so_accounts WHERE handle = ?", (handle,)):
        raise ValueError(f"@{handle} is taken. Try {suggest_handle(sc, handle)}.")
    group = profile.get("age_group") or "adult"
    young = group in ("child", "teen")
    visibility = (
        "private"
        if young
        else kit.choice(
            raw.get("visibility"),
            "who can see your profile",
            VISIBILITIES,
            default="public",
        )
    )
    account_id = new_id()
    moment = stamp()
    sc.execute(
        """INSERT INTO so_accounts (id, person_key, handle, display_name, avatar, bio, age_group, visibility, open_messages,
        home, created_at, updated_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
        (
            account_id,
            ident["key"],
            handle,
            kit.text(
                raw.get("display_name") or profile.get("name"),
                "your name",
                max_len=60,
                required=True,
            ),
            _avatar(raw.get("avatar"), profile.get("emoji") or "🙂"),
            kit.text(raw.get("bio"), "a bio", max_len=300, multiline=True),
            group,
            visibility,
            ident["home"],
            moment,
            moment,
            moment,
        ),
    )
    sc.commit()
    return account(sc, account_id)


def update_account(
    sc: sqlite3.Connection, acct: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "handle" in raw and str(raw["handle"]).lstrip("@").lower() != acct["handle"]:
        handle = _clean_handle(raw["handle"])
        if row(
            sc,
            "SELECT 1 AS x FROM so_accounts WHERE handle = ? AND id != ?",
            (handle, acct["id"]),
        ):
            raise ValueError(f"@{handle} is taken.")
        values["handle"] = handle
    if "display_name" in raw:
        values["display_name"] = kit.text(
            raw["display_name"], "your name", max_len=60, required=True
        )
    if "avatar" in raw:
        values["avatar"] = _avatar(raw["avatar"], acct["avatar"])
    if "bio" in raw:
        values["bio"] = kit.text(raw["bio"], "a bio", max_len=300, multiline=True)
    if "visibility" in raw:
        wanted = kit.choice(raw["visibility"], "who can see your profile", VISIBILITIES)
        if wanted == "public" and acct["age_group"] in ("child", "teen"):
            raise PermissionError("Young people's profiles stay private.")
        values["visibility"] = wanted
    if "open_messages" in raw:
        wanted = kit.flag(raw["open_messages"])
        if wanted and acct["age_group"] in ("child", "teen"):
            raise PermissionError("Only friends can message young people.")
        values["open_messages"] = 1 if wanted else 0
    if "headline" in raw:
        values["headline"] = kit.text(raw["headline"], "a headline", max_len=120)
    if "open_to_work" in raw:
        values["open_to_work"] = 1 if kit.flag(raw["open_to_work"]) else 0
    if "skills" in raw:
        skills = (
            raw["skills"]
            if isinstance(raw["skills"], list)
            else str(raw["skills"] or "").split(",")
        )
        clean = []
        for skill in skills[:30]:
            text = kit.text(skill, "a skill", max_len=40)
            if text and text.lower() not in [s.lower() for s in clean]:
                clean.append(text)
        values["skills"] = store.dumps(clean)
    if "experience" in raw:
        items = raw["experience"] if isinstance(raw["experience"], list) else []
        clean_items = []
        for item in items[:20]:
            if not isinstance(item, dict):
                raise ValueError("Each experience is a title, a company and dates.")
            clean_items.append(
                {
                    "title": kit.text(
                        item.get("title"), "a job title", max_len=80, required=True
                    ),
                    "company": kit.text(item.get("company"), "a company", max_len=80),
                    "start": _month(item.get("start")),
                    "end": _month(item.get("end")),
                    "about": kit.text(
                        item.get("about"), "what you did", max_len=500, multiline=True
                    ),
                }
            )
        values["experience"] = store.dumps(clean_items)
    if values:
        values["updated_at"] = stamp()
        _update(sc, "so_accounts", acct["id"], values)
        sc.commit()
    return account(sc, acct["id"])


def _month(value: Any) -> str:
    text = str(value or "").strip()
    if text and not re.fullmatch(r"\d{4}(-\d{2})?", text):
        raise ValueError("Use a year (2024) or a month (2024-05).")
    return text


def _update(
    sc: sqlite3.Connection, table: str, row_id: str, values: dict[str, Any]
) -> None:
    if not values:
        return
    columns = [column for column in values if re.fullmatch(r"[a-z_]+", column)]
    sc.execute(
        f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",  # noqa: S608 - names are checked
        (*[values[c] for c in columns], row_id),
    )


# ---------------------------------------------------------------------------
# relationships
# ---------------------------------------------------------------------------


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def blocked(sc: sqlite3.Connection, a: str, b: str) -> bool:
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)",
            (a, b, b, a),
        )
    )


def friendship(sc: sqlite3.Connection, a: str, b: str) -> dict[str, Any] | None:
    x, y = _pair(a, b)
    return row(sc, "SELECT * FROM so_friends WHERE a_id = ? AND b_id = ?", (x, y))


def are_friends(sc: sqlite3.Connection, a: str, b: str) -> bool:
    found = friendship(sc, a, b)
    return bool(found and found["status"] == "accepted")


def friend_ids(sc: sqlite3.Connection, account_id: str) -> set[str]:
    found = rows(
        sc,
        "SELECT a_id, b_id FROM so_friends WHERE status = 'accepted' AND (a_id = ? OR b_id = ?)",
        (account_id, account_id),
    )
    return {r["b_id"] if r["a_id"] == account_id else r["a_id"] for r in found}


def blocked_ids(sc: sqlite3.Connection, account_id: str) -> set[str]:
    found = rows(
        sc,
        "SELECT blocker_id, blocked_id FROM so_blocks WHERE blocker_id = ? OR blocked_id = ?",
        (account_id, account_id),
    )
    return {
        r["blocked_id"] if r["blocker_id"] == account_id else r["blocker_id"]
        for r in found
    }


def can_see_account(
    sc: sqlite3.Connection, viewer: dict[str, Any], target: dict[str, Any]
) -> bool:
    """Whether the viewer may find and open this person at all."""
    if viewer["id"] == target["id"]:
        return True
    if target.get("suspended_at") or blocked(sc, viewer["id"], target["id"]):
        return False
    if minor(target):
        # a friend, a request between them, or someone from the child's own household
        return bool(friendship(sc, viewer["id"], target["id"])) or _same_household(
            viewer, target
        )
    return True


def visible_account(
    sc: sqlite3.Connection, viewer: dict[str, Any], target_id: str
) -> dict[str, Any]:
    target = account(sc, target_id)
    if not can_see_account(sc, viewer, target):
        raise LookupError("That person was not found.")
    return target


def mini(acct: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": acct["id"],
        "handle": acct["handle"],
        "name": acct["display_name"],
        "avatar": acct["avatar"],
    }


def relation(
    sc: sqlite3.Connection, viewer: dict[str, Any], target: dict[str, Any]
) -> str:
    if viewer["id"] == target["id"]:
        return "self"
    found = friendship(sc, viewer["id"], target["id"])
    if found and found["status"] == "accepted":
        return "friends"
    if _guardian_entry(sc, viewer["id"], target["id"]):
        return "awaiting_parent"
    if found:
        return (
            "request_sent"
            if found["requested_by"] == viewer["id"]
            else "request_received"
        )
    return "none"


def profile_view(
    sc: sqlite3.Connection, viewer: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    rel = relation(sc, viewer, target)
    full = rel in ("self", "friends") or target["visibility"] == "public"
    following = bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_follows WHERE follower_id = ? AND target_type = 'account' AND target_id = ?",
            (viewer["id"], target["id"]),
        )
    )
    view = {
        **mini(target),
        "visibility": target["visibility"],
        "relation": rel,
        "following": following,
        "friends": len(friend_ids(sc, target["id"])),
        "followers": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_follows WHERE target_type = 'account' AND target_id = ?",
            (target["id"],),
        ),
        "can_message": rel == "self" or can_message(sc, viewer, target),
        "young": target["age_group"] in ("child", "teen"),
    }
    if full:
        view.update(
            {
                "bio": target["bio"],
                "headline": target["headline"],
                "open_to_work": target["open_to_work"],
                "skills": target["skills"],
                "experience": target["experience"],
            }
        )
    return view


def search(sc: sqlite3.Connection, me_: dict[str, Any], q: str) -> dict[str, Any]:
    query = kit.text(q, "a search", max_len=60)
    if len(query) < 2:
        return {"people": [], "groups": [], "pages": [], "posts": []}
    if query.startswith("#"):
        return {
            "people": [],
            "groups": [],
            "pages": [],
            "posts": tag_posts(sc, me_, query[1:]),
        }
    like = f"%{query.lstrip('@').lower()}%"
    hidden = blocked_ids(sc, me_["id"])
    people = []
    for found in rows(
        sc,
        "SELECT * FROM so_accounts WHERE id != ? AND suspended_at IS NULL AND (handle LIKE ? OR lower(display_name) LIKE ?) ORDER BY display_name LIMIT 40",
        (me_["id"], like, like),
    ):
        candidate = _account(found) or {}
        if candidate["id"] in hidden or not can_see_account(sc, me_, candidate):
            continue
        people.append(
            {
                **mini(candidate),
                "relation": relation(sc, me_, candidate),
                "headline": candidate["headline"]
                if candidate["visibility"] == "public"
                else "",
            }
        )
    groups = rows(
        sc,
        "SELECT id, name, about, visibility FROM so_groups WHERE archived_at IS NULL AND visibility = 'public' AND lower(name) LIKE ? ORDER BY name LIMIT 20",
        (like,),
    )
    pages = rows(
        sc,
        "SELECT id, name, about, category FROM so_pages WHERE archived_at IS NULL AND lower(name) LIKE ? ORDER BY name LIMIT 20",
        (like,),
    )
    return {"people": people[:20], "groups": groups, "pages": pages, "posts": []}


def request_friend(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    other_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any]:
    """Ask to be friends, or accept their request. Returns the relation now.

    A child's new friend from outside their household waits for a grown-up of the household to say yes
    (``awaiting_parent``); :func:`answer_guardian` then sends or accepts the request for the child."""
    other = visible_account(sc, me_, other_id)
    if other["id"] == me_["id"]:
        raise ValueError("You cannot be friends with yourself.")
    found = friendship(sc, me_["id"], other["id"])
    moment = stamp()
    if found and found["status"] == "accepted":
        return {"relation": "friends"}
    if minor(me_) and not approved and not _same_household(me_, other):
        if found and found["requested_by"] == me_["id"]:
            return {"relation": "request_sent"}
        return _ask_guardian(sc, me_, other, "accept" if found else "request")
    if found and found["requested_by"] != me_["id"]:
        x, y = _pair(me_["id"], other["id"])
        sc.execute(
            "UPDATE so_friends SET status = 'accepted', accepted_at = ? WHERE a_id = ? AND b_id = ?",
            (moment, x, y),
        )
        notify(
            sc,
            other["id"],
            "friend_accepted",
            me_["id"],
            "account",
            me_["id"],
            f"{me_['display_name']} accepted your friend request.",
        )
        sc.commit()
        return {"relation": "friends"}
    if found:
        return {"relation": "request_sent"}
    if minor(other) and not minor(me_):
        # a stranger adult cannot send a child a request: only someone in the child's household can
        if not _same_household(me_, other):
            raise PermissionError("Only family can send a friend request to a child.")
    rate(sc, me_, "friend_request")
    x, y = _pair(me_["id"], other["id"])
    sc.execute(
        "INSERT INTO so_friends (a_id, b_id, status, requested_by, created_at) VALUES (?, ?, 'pending', ?, ?)",
        (x, y, me_["id"], moment),
    )
    notify(
        sc,
        other["id"],
        "friend_request",
        me_["id"],
        "account",
        me_["id"],
        f"{me_['display_name']} wants to be friends.",
    )
    sc.commit()
    return {"relation": "request_sent"}


def _same_household(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bool(a.get("home")) and a.get("home") == b.get("home")


# ---------------------------------------------------------------------------
# a grown-up says yes to a child's new friends
# ---------------------------------------------------------------------------


def _guardian_entry(
    sc: sqlite3.Connection, child_id: str, other_id: str
) -> dict[str, Any] | None:
    return row(
        sc,
        "SELECT * FROM so_guardian WHERE child_id = ? AND other_id = ?",
        (child_id, other_id),
    )


def guardians(sc: sqlite3.Connection, child: dict[str, Any]) -> list[dict[str, Any]]:
    """The grown-ups of the child's household (or organization) who have Social."""
    if not child.get("home"):
        return []
    found = [
        _account(r)
        for r in rows(
            sc,
            "SELECT * FROM so_accounts WHERE home = ? AND id != ? AND age_group IN ('adult', 'senior') AND suspended_at IS NULL",
            (child["home"], child["id"]),
        )
    ]
    return [item for item in found if item]


def is_guardian(
    sc: sqlite3.Connection, me_: dict[str, Any], child: dict[str, Any]
) -> bool:
    return (
        minor(child)
        and me_["age_group"] in ("adult", "senior")
        and not me_.get("suspended_at")
        and _same_household(me_, child)
    )


def _ask_guardian(
    sc: sqlite3.Connection, child: dict[str, Any], other: dict[str, Any], action: str
) -> dict[str, Any]:
    grown_ups = guardians(sc, child)
    if not grown_ups:
        raise PermissionError(
            "Ask a grown-up in your family to join Social first: they say yes to new friends."
        )
    rate(sc, child, "friend_request")
    sc.execute(
        """INSERT INTO so_guardian (child_id, other_id, action, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(child_id, other_id) DO UPDATE SET action = excluded.action""",
        (child["id"], other["id"], action, stamp()),
    )
    for grown_up in grown_ups:
        notify(
            sc,
            grown_up["id"],
            "guardian_request",
            child["id"],
            "guardian",
            other["id"],
            f"{child['display_name']} wants to be friends with {other['display_name']} (@{other['handle']}). "
            "Say yes or no under Friends.",
        )
    sc.commit()
    return {"relation": "awaiting_parent"}


def guardian_requests(
    sc: sqlite3.Connection, me_: dict[str, Any]
) -> list[dict[str, Any]]:
    """The household's children's new friends waiting for a grown-up's yes (empty for anyone else)."""
    if me_["age_group"] not in ("adult", "senior") or not me_.get("home"):
        return []
    out = []
    for item in rows(
        sc,
        """SELECT g.* FROM so_guardian g JOIN so_accounts c ON c.id = g.child_id
        WHERE c.home = ? AND c.age_group = 'child' ORDER BY g.created_at""",
        (me_["home"],),
    ):
        try:
            child, other = account(sc, item["child_id"]), account(sc, item["other_id"])
        except LookupError:
            continue
        if other.get("suspended_at"):
            continue
        out.append(
            {
                "child": mini(child),
                "other": {
                    **mini(other),
                    "young": other["age_group"] in ("child", "teen"),
                    "friends_with_you": are_friends(sc, me_["id"], other["id"]),
                    "mutual_friends": len(
                        friend_ids(sc, other["id"]) & friend_ids(sc, me_["id"])
                    ),
                },
                "action": item["action"],
                "created_at": item["created_at"],
            }
        )
    return out


def answer_guardian(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    child_id: str,
    other_id: str,
    approve: bool,
) -> dict[str, Any]:
    """A grown-up says yes (the request is sent, or accepted, for the child) or no (nothing is sent)."""
    child = account(sc, child_id)
    store.check_id(other_id, "person")
    entry = _guardian_entry(sc, child["id"], other_id)
    if not entry or not is_guardian(sc, me_, child):
        raise LookupError("That request was not found.")
    sc.execute(
        "DELETE FROM so_guardian WHERE child_id = ? AND other_id = ?",
        (child["id"], other_id),
    )
    other = account(sc, other_id)
    if approve:
        result = request_friend(sc, child, other_id, approved=True)
        text = (
            f"{me_['display_name']} said yes: you and {other['display_name']} are friends now."
            if result["relation"] == "friends"
            else f"{me_['display_name']} said yes: your friend request went to {other['display_name']}."
        )
    else:
        if entry["action"] == "accept":
            x, y = _pair(child["id"], other_id)
            sc.execute(
                "DELETE FROM so_friends WHERE a_id = ? AND b_id = ? AND status = 'pending'",
                (x, y),
            )
        result = {"relation": "none"}
        text = f"{me_['display_name']} said not this time for {other['display_name']}."
    notify(sc, child["id"], "guardian_answer", me_["id"], "account", other_id, text)
    sc.commit()
    return result


def child_can_be_found(
    sc: sqlite3.Connection, viewer: dict[str, Any], child: dict[str, Any]
) -> bool:
    return _same_household(viewer, child) or bool(
        friendship(sc, viewer["id"], child["id"])
    )


def remove_friend(
    sc: sqlite3.Connection, me_: dict[str, Any], other_id: str
) -> dict[str, Any]:
    """Unfriend, cancel a request or decline one."""
    store.check_id(other_id, "person")
    x, y = _pair(me_["id"], other_id)
    sc.execute("DELETE FROM so_friends WHERE a_id = ? AND b_id = ?", (x, y))
    sc.execute(
        "DELETE FROM so_guardian WHERE child_id = ? AND other_id = ?",
        (me_["id"], other_id),
    )
    sc.commit()
    return {"relation": "none"}


def friends_overview(sc: sqlite3.Connection, me_: dict[str, Any]) -> dict[str, Any]:
    found = rows(
        sc,
        "SELECT * FROM so_friends WHERE a_id = ? OR b_id = ?",
        (me_["id"], me_["id"]),
    )
    friends, incoming, outgoing, waiting = [], [], [], []
    asked = {
        r["other_id"]: r["action"]
        for r in rows(
            sc,
            "SELECT other_id, action FROM so_guardian WHERE child_id = ?",
            (me_["id"],),
        )
    }
    for item in found:
        other_id = item["b_id"] if item["a_id"] == me_["id"] else item["a_id"]
        try:
            other = account(sc, other_id)
        except LookupError:
            continue
        if other.get("suspended_at") or blocked(sc, me_["id"], other_id):
            continue
        entry = {**mini(other), "since": item["accepted_at"] or item["created_at"]}
        if item["status"] == "accepted":
            friends.append(entry)
        elif item["requested_by"] == me_["id"]:
            outgoing.append(entry)
        elif asked.pop(other_id, None):
            waiting.append(entry)
        else:
            incoming.append(entry)
    for other_id in asked:
        try:
            other = account(sc, other_id)
        except LookupError:
            continue
        if not other.get("suspended_at") and not blocked(sc, me_["id"], other_id):
            waiting.append({**mini(other), "since": ""})
    return {
        "friends": sorted(friends, key=lambda f: f["name"].lower()),
        "incoming": incoming,
        "outgoing": outgoing,
        "awaiting_parent": waiting,
        "children_asking": guardian_requests(sc, me_),
        "suggestions": suggestions(sc, me_),
    }


def suggestions(
    sc: sqlite3.Connection, me_: dict[str, Any], limit: int = 8
) -> list[dict[str, Any]]:
    """Friends of friends, then people from the same household or organization."""
    mine = friend_ids(sc, me_["id"])
    known = mine | {me_["id"]} | blocked_ids(sc, me_["id"])
    pending = {
        r["b_id"] if r["a_id"] == me_["id"] else r["a_id"]
        for r in rows(
            sc,
            "SELECT a_id, b_id FROM so_friends WHERE a_id = ? OR b_id = ?",
            (me_["id"], me_["id"]),
        )
    }
    scores: dict[str, int] = {}
    for friend in mine:
        for other in friend_ids(sc, friend):
            if other not in known and other not in pending:
                scores[other] = scores.get(other, 0) + 2
    for other in rows(
        sc,
        "SELECT id FROM so_accounts WHERE home = ? AND id != ?",
        (me_["home"], me_["id"]),
    ):
        if other["id"] not in known and other["id"] not in pending:
            scores[other["id"]] = scores.get(other["id"], 0) + 1
    out = []
    for other_id, _score in sorted(scores.items(), key=lambda item: -item[1]):
        candidate = account(sc, other_id)
        if candidate.get("suspended_at") or (
            minor(candidate) and not child_can_be_found(sc, me_, candidate)
        ):
            continue
        out.append(mini(candidate))
        if len(out) >= limit:
            break
    return out


def follow(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    target_type: str,
    target_id: str,
    on: bool,
) -> dict[str, Any]:
    if target_type == "account":
        target = visible_account(sc, me_, target_id)
        if target["id"] == me_["id"]:
            raise ValueError("You cannot follow yourself.")
        if target["visibility"] != "public" and on:
            raise PermissionError("This profile is private: ask to be friends instead.")
    elif target_type == "page":
        page(sc, target_id)
    else:
        raise ValueError("Follow a person or a page.")
    if on:
        sc.execute(
            "INSERT OR IGNORE INTO so_follows (follower_id, target_type, target_id, created_at) VALUES (?, ?, ?, ?)",
            (me_["id"], target_type, target_id, stamp()),
        )
        if target_type == "account":
            notify(
                sc,
                target_id,
                "follow",
                me_["id"],
                "account",
                me_["id"],
                f"{me_['display_name']} started following you.",
            )
    else:
        sc.execute(
            "DELETE FROM so_follows WHERE follower_id = ? AND target_type = ? AND target_id = ?",
            (me_["id"], target_type, target_id),
        )
    sc.commit()
    return {"following": on}


def block(sc: sqlite3.Connection, me_: dict[str, Any], other_id: str) -> dict[str, Any]:
    other = account(sc, other_id)
    if other["id"] == me_["id"]:
        raise ValueError("You cannot block yourself.")
    sc.execute(
        "INSERT OR IGNORE INTO so_blocks (blocker_id, blocked_id, created_at) VALUES (?, ?, ?)",
        (me_["id"], other["id"], stamp()),
    )
    x, y = _pair(me_["id"], other["id"])
    sc.execute("DELETE FROM so_friends WHERE a_id = ? AND b_id = ?", (x, y))
    sc.execute(
        "DELETE FROM so_guardian WHERE (child_id = ? AND other_id = ?) OR (child_id = ? AND other_id = ?)",
        (me_["id"], other["id"], other["id"], me_["id"]),
    )
    sc.execute(
        "DELETE FROM so_follows WHERE target_type = 'account' AND ((follower_id = ? AND target_id = ?) OR (follower_id = ? AND target_id = ?))",
        (me_["id"], other["id"], other["id"], me_["id"]),
    )
    sc.commit()
    return {"blocked": True}


def unblock(
    sc: sqlite3.Connection, me_: dict[str, Any], other_id: str
) -> dict[str, Any]:
    store.check_id(other_id, "person")
    sc.execute(
        "DELETE FROM so_blocks WHERE blocker_id = ? AND blocked_id = ?",
        (me_["id"], other_id),
    )
    sc.commit()
    return {"blocked": False}


def blocks(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rows(
        sc,
        "SELECT blocked_id FROM so_blocks WHERE blocker_id = ? ORDER BY created_at DESC",
        (me_["id"],),
    ):
        try:
            out.append(mini(account(sc, item["blocked_id"])))
        except LookupError:
            continue
    return out


# ---------------------------------------------------------------------------
# safety: rate limits and the word filter
# ---------------------------------------------------------------------------


def rate(sc: sqlite3.Connection, me_: dict[str, Any], kind: str) -> None:
    window = stamp()[:13]  # the hour
    found = row(
        sc,
        "SELECT window, count FROM so_rate WHERE account_id = ? AND kind = ?",
        (me_["id"], kind),
    )
    current = found["count"] if found and found["window"] == window else 0
    if current >= LIMITS[kind]:
        raise PermissionError(
            "You are doing that too often. Take a break and try again later."
        )
    sc.execute(
        """INSERT INTO so_rate (account_id, kind, window, count) VALUES (?, ?, ?, 1)
        ON CONFLICT(account_id, kind) DO UPDATE SET count = CASE WHEN window = excluded.window THEN count + 1 ELSE 1 END,
        window = excluded.window""",
        (me_["id"], kind, window),
    )


def blocklist(sc: sqlite3.Connection) -> list[str]:
    return list(store.setting(sc, "blocklist", list(DEFAULT_BLOCKLIST)))


def save_blocklist(sc: sqlite3.Connection, words: Iterable[Any]) -> list[str]:
    clean = []
    for word in list(words)[:200]:
        text = kit.text(word, "a word", max_len=40).lower()
        if text and text not in clean:
            clean.append(text)
    store.set_setting(sc, "blocklist", clean)
    sc.commit()
    return clean


def filtered(sc: sqlite3.Connection, text: str) -> str:
    out = text
    for word in blocklist(sc):
        out = re.sub(rf"(?i)\b{re.escape(word)}\b", "*" * len(word), out)
    return out


def _photos(value: Any, limit: int = 6) -> list[str]:
    items = (
        value if isinstance(value, list) else [p for p in str(value or "").split() if p]
    )
    if len(items) > limit:
        raise ValueError(f"At most {limit} photos.")
    out = []
    for item in items:
        link = kit.url(item, "a photo link")
        if not link.lower().startswith("https://"):
            raise ValueError("Photo links must start with https://.")
        out.append(link)
    return out


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------


def notify(
    sc: sqlite3.Connection,
    account_id: str,
    kind: str,
    actor_id: str,
    ref_type: str,
    ref_id: str,
    text: str,
) -> None:
    if account_id == actor_id or blocked(sc, account_id, actor_id):
        return
    sc.execute(
        """INSERT INTO so_notifications (id, account_id, kind, actor_id, ref_type, ref_id, text, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (new_id(), account_id, kind, actor_id, ref_type, ref_id, text[:300], stamp()),
    )


def notifications(
    sc: sqlite3.Connection, me_: dict[str, Any], limit: int = 50
) -> dict[str, Any]:
    found = rows(
        sc,
        "SELECT * FROM so_notifications WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
        (me_["id"], limit),
    )
    out = []
    for item in found:
        actor = None
        if item["actor_id"]:
            try:
                candidate = account(sc, item["actor_id"])
                if not blocked(sc, me_["id"], candidate["id"]):
                    actor = mini(candidate)
            except LookupError:
                actor = None
        out.append({**item, "actor": actor, "read": bool(item["read_at"])})
    return {"notifications": out, "unread": unread_notifications(sc, me_)}


def unread_notifications(sc: sqlite3.Connection, me_: dict[str, Any]) -> int:
    return kit.count(
        sc,
        "SELECT COUNT(*) FROM so_notifications WHERE account_id = ? AND read_at IS NULL",
        (me_["id"],),
    )


def read_notifications(sc: sqlite3.Connection, me_: dict[str, Any]) -> int:
    changed = sc.execute(
        "UPDATE so_notifications SET read_at = ? WHERE account_id = ? AND read_at IS NULL",
        (stamp(), me_["id"]),
    ).rowcount
    sc.commit()
    return changed


# ---------------------------------------------------------------------------
# posts
# ---------------------------------------------------------------------------


def _group_member(sc: sqlite3.Connection, group_id: str, account_id: str) -> bool:
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_group_members WHERE group_id = ? AND account_id = ? AND status = 'member'",
            (group_id, account_id),
        )
    )


def _page_admin(sc: sqlite3.Connection, page_id: str, account_id: str) -> bool:
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_page_admins WHERE page_id = ? AND account_id = ?",
            (page_id, account_id),
        )
    )


def can_see_post(
    sc: sqlite3.Connection, viewer: dict[str, Any], post: dict[str, Any]
) -> bool:
    if post.get("deleted_at") or post.get("hidden_at"):
        return False
    # a deleted group or page takes its posts with it, for their authors too
    if post.get("group_id") and not row(
        sc,
        "SELECT 1 AS x FROM so_groups WHERE id = ? AND archived_at IS NULL",
        (post["group_id"],),
    ):
        return False
    if post.get("page_id") and not row(
        sc,
        "SELECT 1 AS x FROM so_pages WHERE id = ? AND archived_at IS NULL",
        (post["page_id"],),
    ):
        return False
    if post["author_id"] == viewer["id"]:
        return True
    try:
        author = account(sc, post["author_id"])
    except LookupError:
        return False
    if author.get("suspended_at") or blocked(sc, viewer["id"], author["id"]):
        return False
    if post["group_id"]:
        found = row(
            sc,
            "SELECT visibility, archived_at FROM so_groups WHERE id = ?",
            (post["group_id"],),
        )
        if not found or found["archived_at"]:
            return False
        return found["visibility"] == "public" or _group_member(
            sc, post["group_id"], viewer["id"]
        )
    if post["page_id"]:
        return bool(
            row(
                sc,
                "SELECT 1 AS x FROM so_pages WHERE id = ? AND archived_at IS NULL",
                (post["page_id"],),
            )
        )
    if post["audience"] == "only_me":
        return False
    if post["audience"] == "friends":
        return are_friends(sc, viewer["id"], author["id"])
    return True  # public


def _post_row(sc: sqlite3.Connection, post_id: str) -> dict[str, Any]:
    found = row(
        sc, "SELECT * FROM so_posts WHERE id = ?", (store.check_id(post_id, "post"),)
    )
    if not found:
        raise LookupError("That post was not found.")
    return found


def visible_post(
    sc: sqlite3.Connection, viewer: dict[str, Any], post_id: str
) -> dict[str, Any]:
    post = _post_row(sc, post_id)
    if not can_see_post(sc, viewer, post):
        raise LookupError("That post was not found.")
    return post


def create_post(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    text = kit.text(raw.get("text"), "something to share", max_len=2000, multiline=True)
    photos = _photos(raw.get("photos"))
    shared = str(raw.get("shared_post_id") or "")
    if not text and not photos and not shared:
        raise ValueError("Write something or add a photo.")
    group_id = str(raw.get("group_id") or "")
    page_id = str(raw.get("page_id") or "")
    audience = kit.choice(
        raw.get("audience"), "who can see it", AUDIENCES, default="friends"
    )
    if group_id:
        group = group_row(sc, group_id)
        if not _group_member(sc, group["id"], me_["id"]):
            raise PermissionError("Join the group to post in it.")
        audience = "group"
    elif page_id:
        page(sc, page_id)
        if not _page_admin(sc, page_id, me_["id"]):
            raise PermissionError("Only the page's admins post as the page.")
        audience = "page"
    elif audience == "public" and me_["age_group"] in ("child", "teen"):
        audience = "friends"
    if shared:
        original = visible_post(sc, me_, shared)
        if original["audience"] not in ("public", "page") and not (
            original["group_id"] and _group_public(sc, original["group_id"])
        ):
            raise PermissionError("Only public posts can be shared.")
        shared = original["shared_post_id"] or original["id"]
    rate(sc, me_, "post")
    post_id = new_id()
    moment = stamp()
    clean = filtered(sc, text)
    tags = sorted({t.lower() for t in TAG.findall(clean)})
    sc.execute(
        """INSERT INTO so_posts (id, author_id, audience, group_id, page_id, text, photos, shared_post_id, tags, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post_id,
            me_["id"],
            audience,
            group_id or None,
            page_id or None,
            clean,
            store.dumps(photos),
            shared or None,
            " ".join(tags),
            moment,
            moment,
        ),
    )
    for tag in tags:
        sc.execute(
            "INSERT OR IGNORE INTO so_post_tags (tag, post_id, created_at) VALUES (?, ?, ?)",
            (tag, post_id, moment),
        )
    post = _post_row(sc, post_id)
    _mention(sc, me_, post, clean, "post")
    if shared:
        original = _post_row(sc, shared)
        notify(
            sc,
            original["author_id"],
            "share",
            me_["id"],
            "post",
            post_id,
            f"{me_['display_name']} shared your post.",
        )
    sc.commit()
    return post_view(sc, me_, post)


def _group_public(sc: sqlite3.Connection, group_id: str) -> bool:
    found = row(sc, "SELECT visibility FROM so_groups WHERE id = ?", (group_id,))
    return bool(found and found["visibility"] == "public")


def _mention(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    post: dict[str, Any],
    text: str,
    where: str,
) -> None:
    for handle in sorted(set(MENTION.findall(text.lower()))):
        found = _account(
            row(sc, "SELECT * FROM so_accounts WHERE handle = ?", (handle,))
        )
        if found and found["id"] != me_["id"] and can_see_post(sc, found, post):
            notify(
                sc,
                found["id"],
                "mention",
                me_["id"],
                "post",
                post["id"],
                f"{me_['display_name']} mentioned you in a {where}.",
            )


def _active(me_: dict[str, Any]) -> None:
    if me_.get("suspended_at"):
        raise PermissionError("Your Social account is suspended.")


def edit_post(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    post = _post_row(sc, post_id)
    if post["author_id"] != me_["id"] or post["deleted_at"]:
        raise LookupError("That post was not found.")
    moment = stamp()
    values: dict[str, Any] = {"updated_at": moment, "edited_at": moment}
    if "text" in raw:
        values["text"] = filtered(
            sc, kit.text(raw["text"], "the text", max_len=2000, multiline=True)
        )
        tags = sorted({t.lower() for t in TAG.findall(values["text"])})
        values["tags"] = " ".join(tags)
        sc.execute("DELETE FROM so_post_tags WHERE post_id = ?", (post["id"],))
        for tag in tags:
            sc.execute(
                "INSERT OR IGNORE INTO so_post_tags (tag, post_id, created_at) VALUES (?, ?, ?)",
                (tag, post["id"], post["created_at"]),
            )
    if "audience" in raw and post["audience"] in AUDIENCES:
        wanted = kit.choice(raw["audience"], "who can see it", AUDIENCES)
        if wanted == "public" and me_["age_group"] in ("child", "teen"):
            wanted = "friends"
        values["audience"] = wanted
    _update(sc, "so_posts", post["id"], values)
    sc.commit()
    return post_view(sc, me_, _post_row(sc, post["id"]))


def delete_post(sc: sqlite3.Connection, me_: dict[str, Any], post_id: str) -> None:
    post = _post_row(sc, post_id)
    page_admin = bool(post["page_id"]) and _page_admin(sc, post["page_id"], me_["id"])
    group_admin = bool(post["group_id"]) and _group_admin(
        sc, post["group_id"], me_["id"]
    )
    if post["author_id"] != me_["id"] and not page_admin and not group_admin:
        raise LookupError("That post was not found.")
    moment = stamp()
    sc.execute(
        "UPDATE so_posts SET deleted_at = ?, updated_at = ? WHERE id = ?",
        (moment, moment, post["id"]),
    )
    sc.commit()


def react(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, emoji: str | None
) -> dict[str, Any]:
    post = visible_post(sc, me_, post_id)
    current = row(
        sc,
        "SELECT emoji FROM so_reactions WHERE post_id = ? AND account_id = ?",
        (post["id"], me_["id"]),
    )
    if emoji is None or (current and current["emoji"] == emoji):
        sc.execute(
            "DELETE FROM so_reactions WHERE post_id = ? AND account_id = ?",
            (post["id"], me_["id"]),
        )
    else:
        if emoji not in REACTIONS:
            raise ValueError("Choose one of the reactions.")
        sc.execute(
            """INSERT INTO so_reactions (post_id, account_id, emoji, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(post_id, account_id) DO UPDATE SET emoji = excluded.emoji""",
            (post["id"], me_["id"], emoji, stamp()),
        )
        if not current:
            notify(
                sc,
                post["author_id"],
                "reaction",
                me_["id"],
                "post",
                post["id"],
                f"{me_['display_name']} reacted {emoji} to your post.",
            )
    sc.execute("UPDATE so_posts SET updated_at = ? WHERE id = ?", (stamp(), post["id"]))
    sc.commit()
    return post_view(sc, me_, _post_row(sc, post["id"]))


def comment(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    post = visible_post(sc, me_, post_id)
    text = filtered(
        sc,
        kit.text(
            raw.get("text"), "a comment", max_len=1000, required=True, multiline=True
        ),
    )
    rate(sc, me_, "comment")
    comment_id = new_id()
    moment = stamp()
    sc.execute(
        "INSERT INTO so_comments (id, post_id, author_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (comment_id, post["id"], me_["id"], text, moment),
    )
    sc.execute("UPDATE so_posts SET updated_at = ? WHERE id = ?", (moment, post["id"]))
    notify(
        sc,
        post["author_id"],
        "comment",
        me_["id"],
        "post",
        post["id"],
        f"{me_['display_name']} commented on your post.",
    )
    _mention(sc, me_, post, text, "comment")
    sc.commit()
    return {
        "comment": {
            "id": comment_id,
            "author": mini(me_),
            "text": text,
            "created_at": moment,
            "mine": True,
        }
    }


def comments(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str
) -> list[dict[str, Any]]:
    post = visible_post(sc, me_, post_id)
    hidden = blocked_ids(sc, me_["id"])
    out = []
    for item in rows(
        sc,
        "SELECT * FROM so_comments WHERE post_id = ? AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at",
        (post["id"],),
    ):
        if item["author_id"] in hidden:
            continue
        try:
            author = account(sc, item["author_id"])
        except LookupError:
            continue
        if author.get("suspended_at"):
            continue
        out.append(
            {
                "id": item["id"],
                "author": mini(author),
                "text": item["text"],
                "created_at": item["created_at"],
                "mine": item["author_id"] == me_["id"],
                "can_delete": item["author_id"] == me_["id"]
                or post["author_id"] == me_["id"],
            }
        )
    return out


def delete_comment(
    sc: sqlite3.Connection, me_: dict[str, Any], comment_id: str
) -> None:
    found = row(
        sc,
        "SELECT * FROM so_comments WHERE id = ?",
        (store.check_id(comment_id, "comment"),),
    )
    if not found or found["deleted_at"]:
        raise LookupError("That comment was not found.")
    post = _post_row(sc, found["post_id"])
    if found["author_id"] != me_["id"] and post["author_id"] != me_["id"]:
        raise LookupError("That comment was not found.")
    moment = stamp()
    sc.execute(
        "UPDATE so_comments SET deleted_at = ? WHERE id = ?", (moment, found["id"])
    )
    sc.execute("UPDATE so_posts SET updated_at = ? WHERE id = ?", (moment, post["id"]))
    sc.commit()


def post_view(
    sc: sqlite3.Connection,
    viewer: dict[str, Any],
    post: dict[str, Any],
    *,
    nested: bool = False,
) -> dict[str, Any]:
    author = account(sc, post["author_id"])
    reactions: dict[str, int] = {}
    mine = None
    for item in rows(
        sc,
        "SELECT account_id, emoji FROM so_reactions WHERE post_id = ?",
        (post["id"],),
    ):
        reactions[item["emoji"]] = reactions.get(item["emoji"], 0) + 1
        if item["account_id"] == viewer["id"]:
            mine = item["emoji"]
    view: dict[str, Any] = {
        "id": post["id"],
        "author": mini(author),
        "audience": post["audience"],
        "text": post["text"],
        "photos": store.loads(post["photos"], []),
        "tags": [t for t in post["tags"].split() if t],
        "created_at": post["created_at"],
        "updated_at": post["updated_at"],
        "edited": bool(post.get("edited_at")),
        "reactions": reactions,
        "my_reaction": mine,
        "comments": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_comments WHERE post_id = ? AND deleted_at IS NULL AND hidden_at IS NULL",
            (post["id"],),
        ),
        "mine": post["author_id"] == viewer["id"],
        "can_share": post["audience"] in ("public", "page"),
    }
    if post["group_id"]:
        found = row(
            sc, "SELECT id, name FROM so_groups WHERE id = ?", (post["group_id"],)
        )
        view["group"] = found
    if post["page_id"]:
        found = row(
            sc, "SELECT id, name FROM so_pages WHERE id = ?", (post["page_id"],)
        )
        view["page"] = found
    if post["shared_post_id"] and not nested:
        try:
            original = _post_row(sc, post["shared_post_id"])
            view["shared"] = (
                post_view(sc, viewer, original, nested=True)
                if can_see_post(sc, viewer, original)
                else {"gone": True}
            )
        except LookupError:
            view["shared"] = {"gone": True}
    return view


def feed(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    *,
    since: str = "",
    limit: int = 30,
    before: str = "",
) -> dict[str, Any]:
    """Newest first: your friends and you, people and pages you follow, and your groups."""
    authors = sorted(friend_ids(sc, me_["id"]) | {me_["id"]})
    follows = rows(
        sc,
        "SELECT target_type, target_id FROM so_follows WHERE follower_id = ?",
        (me_["id"],),
    )
    followed_people = [f["target_id"] for f in follows if f["target_type"] == "account"]
    followed_pages = [f["target_id"] for f in follows if f["target_type"] == "page"]
    groups = [
        g["group_id"]
        for g in rows(
            sc,
            "SELECT group_id FROM so_group_members WHERE account_id = ? AND status = 'member'",
            (me_["id"],),
        )
    ]

    def placeholders(values: list[str]) -> str:
        return ",".join("?" for _ in values) or "''"

    clauses = [
        f"author_id IN ({placeholders(authors)})",
        f"(author_id IN ({placeholders(followed_people)}) AND audience = 'public')",
        f"page_id IN ({placeholders(followed_pages)})",
        f"group_id IN ({placeholders(groups)})",
    ]
    params: list[Any] = [*authors, *followed_people, *followed_pages, *groups]
    where = f"({' OR '.join(clauses)})"
    if since:
        where += " AND updated_at > ?"
        params.append(since)
    else:
        where += " AND deleted_at IS NULL AND hidden_at IS NULL"
        if before:
            where += " AND created_at < ?"
            params.append(before)
    found = rows(
        sc,
        f"SELECT * FROM so_posts WHERE {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608 - placeholders only
        (*params, max(1, min(limit, 100))),
    )
    posts, removed = [], []
    for post in found:
        if can_see_post(sc, me_, post):
            posts.append(post_view(sc, me_, post))
        elif since:
            removed.append(post["id"])
    return {"posts": posts, "removed": removed, "now": stamp()}


def tag_posts(
    sc: sqlite3.Connection, me_: dict[str, Any], tag: str
) -> list[dict[str, Any]]:
    clean = kit.text(tag, "a tag", max_len=40).lstrip("#").lower()
    out = []
    for item in rows(
        sc,
        "SELECT p.* FROM so_post_tags t JOIN so_posts p ON p.id = t.post_id WHERE t.tag = ? ORDER BY p.created_at DESC LIMIT 100",
        (clean,),
    ):
        if can_see_post(sc, me_, item):
            out.append(post_view(sc, me_, item))
        if len(out) >= 40:
            break
    return out


def person_posts(
    sc: sqlite3.Connection, me_: dict[str, Any], target: dict[str, Any], limit: int = 30
) -> list[dict[str, Any]]:
    out = []
    for item in rows(
        sc,
        "SELECT * FROM so_posts WHERE author_id = ? AND group_id IS NULL AND page_id IS NULL AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at DESC LIMIT ?",
        (target["id"], limit * 2),
    ):
        if can_see_post(sc, me_, item):
            out.append(post_view(sc, me_, item))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# stories (24 hours)
# ---------------------------------------------------------------------------


def create_story(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    text = filtered(
        sc, kit.text(raw.get("text"), "the story", max_len=200, multiline=True)
    )
    photos = _photos([raw["photo"]] if raw.get("photo") else [], limit=1)
    if not text and not photos:
        raise ValueError("Write something or add a photo.")
    rate(sc, me_, "story")
    story_id = new_id()
    sc.execute(
        "INSERT INTO so_stories (id, author_id, text, photo, color, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            story_id,
            me_["id"],
            text,
            photos[0] if photos else "",
            kit.color(raw.get("color")),
            stamp(),
            store.later(STORY_HOURS),
        ),
    )
    sc.commit()
    return {"id": story_id}


def stories(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    authors = sorted(friend_ids(sc, me_["id"]) | {me_["id"]})
    marks = ",".join("?" for _ in authors)
    found = rows(
        sc,
        f"SELECT * FROM so_stories WHERE author_id IN ({marks}) AND expires_at > ? AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at",  # noqa: S608
        (*authors, stamp()),
    )
    seen = {
        r["story_id"]
        for r in rows(
            sc, "SELECT story_id FROM so_story_views WHERE viewer_id = ?", (me_["id"],)
        )
    }
    grouped: dict[str, dict[str, Any]] = {}
    for item in found:
        author = account(sc, item["author_id"])
        if author.get("suspended_at"):
            continue
        entry = grouped.setdefault(
            author["id"],
            {
                "author": mini(author),
                "stories": [],
                "unseen": 0,
                "mine": author["id"] == me_["id"],
            },
        )
        is_seen = item["id"] in seen or author["id"] == me_["id"]
        entry["stories"].append(
            {
                "id": item["id"],
                "text": item["text"],
                "photo": item["photo"],
                "color": item["color"],
                "created_at": item["created_at"],
                "seen": is_seen,
                "views": kit.count(
                    sc,
                    "SELECT COUNT(*) FROM so_story_views WHERE story_id = ?",
                    (item["id"],),
                )
                if author["id"] == me_["id"]
                else None,
            }
        )
        if not is_seen:
            entry["unseen"] += 1
    return sorted(grouped.values(), key=lambda e: (not e["mine"], e["unseen"] == 0))


def see_story(sc: sqlite3.Connection, me_: dict[str, Any], story_id: str) -> None:
    found = row(
        sc,
        "SELECT * FROM so_stories WHERE id = ?",
        (store.check_id(story_id, "story"),),
    )
    if (
        not found
        or found["deleted_at"]
        or found["hidden_at"]
        or found["expires_at"] <= stamp()
        or not (
            found["author_id"] == me_["id"]
            or are_friends(sc, me_["id"], found["author_id"])
        )
    ):
        raise LookupError("That story is no longer available.")
    sc.execute(
        "INSERT OR IGNORE INTO so_story_views (story_id, viewer_id, at) VALUES (?, ?, ?)",
        (found["id"], me_["id"], stamp()),
    )
    sc.commit()


def delete_story(sc: sqlite3.Connection, me_: dict[str, Any], story_id: str) -> None:
    found = row(
        sc,
        "SELECT * FROM so_stories WHERE id = ?",
        (store.check_id(story_id, "story"),),
    )
    if not found or found["author_id"] != me_["id"]:
        raise LookupError("That story was not found.")
    sc.execute(
        "UPDATE so_stories SET deleted_at = ? WHERE id = ?", (stamp(), found["id"])
    )
    sc.commit()


# ---------------------------------------------------------------------------
# chats
# ---------------------------------------------------------------------------


def can_message(
    sc: sqlite3.Connection, me_: dict[str, Any], other: dict[str, Any]
) -> bool:
    if me_["id"] == other["id"] or other.get("suspended_at") or me_.get("suspended_at"):
        return False
    if blocked(sc, me_["id"], other["id"]):
        return False
    if are_friends(sc, me_["id"], other["id"]):
        return True
    if other["age_group"] in ("child", "teen") or me_["age_group"] == "child":
        return False
    return bool(other["open_messages"])


def may_send_direct(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    other: dict[str, Any],
    chat: dict[str, Any],
) -> bool:
    """Whether a message may go into this one-to-one chat now. The rule is the one for starting a chat (an
    unfriended stranger cannot keep writing to a teen through an old chat), plus a chat an adult started
    about a Marketplace listing, which stays open between the two adults while that listing is still for sale
    to the buyer (and a little while after it sold, to arrange the pick-up). Once it is removed, hidden, no
    longer visible to them or long sold, the ordinary rule applies again."""
    if can_message(sc, me_, other):
        return True
    if (
        chat.get("context") == "market"
        and _listing_still_open(sc, chat.get("listing_id") or "", me_, other)
        and not blocked(sc, me_["id"], other["id"])
        and not other.get("suspended_at")
        and not me_.get("suspended_at")
    ):
        return me_["age_group"] not in ("child", "teen") and other["age_group"] not in (
            "child",
            "teen",
        )
    return False


MARKET_CHAT_DAYS_AFTER_SALE = 14


def _listing_still_open(
    sc: sqlite3.Connection, listing_id: str, a: dict[str, Any], b: dict[str, Any]
) -> bool:
    """The Marketplace listing a chat between ``a`` and ``b`` is about is still one the buyer may ask about."""
    if not listing_id:
        return False
    listing = row(sc, "SELECT * FROM so_listings WHERE id = ?", (listing_id,))
    if not listing or listing["deleted_at"] or listing["hidden_at"]:
        return False
    if listing["seller_id"] not in (a["id"], b["id"]):
        return False
    buyer = b if listing["seller_id"] == a["id"] else a
    if listing["audience"] == "friends" and not are_friends(
        sc, buyer["id"], listing["seller_id"]
    ):
        return False
    if listing["status"] == "sold":
        sold = store.parse(listing["sold_at"] or listing["updated_at"])
        return sold > store.utcnow() - timedelta(days=MARKET_CHAT_DAYS_AFTER_SALE)
    return True


def _member(
    sc: sqlite3.Connection, chat_id: str, account_id: str, *, active: bool = True
) -> dict[str, Any] | None:
    found = row(
        sc,
        "SELECT * FROM so_chat_members WHERE chat_id = ? AND account_id = ?",
        (chat_id, account_id),
    )
    if not found or (active and found["left_at"]):
        return None
    return found


def chat_row(
    sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str
) -> dict[str, Any]:
    found = row(
        sc, "SELECT * FROM so_chats WHERE id = ?", (store.check_id(chat_id, "chat"),)
    )
    if not found or not _member(sc, found["id"], me_["id"]):
        raise LookupError("That chat was not found.")
    return found


def open_direct(
    sc: sqlite3.Connection, me_: dict[str, Any], other_id: str
) -> dict[str, Any]:
    other = visible_account(sc, me_, other_id)
    x, y = _pair(me_["id"], other["id"])
    key = f"{x}:{y}"
    existing = row(sc, "SELECT * FROM so_chats WHERE direct_key = ?", (key,))
    if existing:
        if blocked(sc, me_["id"], other["id"]):
            raise PermissionError("You cannot message this person.")
        sc.execute(
            "UPDATE so_chat_members SET left_at = NULL WHERE chat_id = ? AND account_id = ?",
            (existing["id"], me_["id"]),
        )
        sc.commit()
        return chat_summary(sc, me_, existing)
    if not can_message(sc, me_, other):
        raise PermissionError(
            "You can message friends, and people who accept messages from everyone."
        )
    chat_id = new_id()
    moment = stamp()
    sc.execute(
        "INSERT INTO so_chats (id, kind, title, direct_key, created_by, created_at, updated_at) VALUES (?, 'direct', '', ?, ?, ?, ?)",
        (chat_id, key, me_["id"], moment, moment),
    )
    for member in (me_["id"], other["id"]):
        sc.execute(
            "INSERT INTO so_chat_members (chat_id, account_id, role, joined_at, last_read_at) VALUES (?, ?, 'member', ?, ?)",
            (chat_id, member, moment, moment if member == me_["id"] else ""),
        )
    sc.commit()
    return chat_summary(
        sc, me_, row(sc, "SELECT * FROM so_chats WHERE id = ?", (chat_id,)) or {}
    )


def create_group_chat(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    title = kit.text(raw.get("title"), "a name for the chat", max_len=60, required=True)
    ids = [str(i) for i in (raw.get("member_ids") or []) if str(i) != me_["id"]]
    if not ids:
        raise ValueError("Add at least one person.")
    if len(ids) > 49:
        raise ValueError("A group chat has at most 50 people.")
    members = []
    for member_id in dict.fromkeys(ids):
        other = visible_account(sc, me_, member_id)
        if not can_message(sc, me_, other):
            raise PermissionError(
                f"You can add friends only ({other['display_name']} is not one)."
            )
        members.append(other)
    chat_id = new_id()
    moment = stamp()
    sc.execute(
        "INSERT INTO so_chats (id, kind, title, created_by, created_at, updated_at) VALUES (?, 'group', ?, ?, ?, ?)",
        (chat_id, title, me_["id"], moment, moment),
    )
    sc.execute(
        "INSERT INTO so_chat_members (chat_id, account_id, role, joined_at, last_read_at) VALUES (?, ?, 'admin', ?, ?)",
        (chat_id, me_["id"], moment, moment),
    )
    for other in members:
        sc.execute(
            "INSERT INTO so_chat_members (chat_id, account_id, role, joined_at) VALUES (?, ?, 'member', ?)",
            (chat_id, other["id"], moment),
        )
        notify(
            sc,
            other["id"],
            "chat_added",
            me_["id"],
            "chat",
            chat_id,
            f"{me_['display_name']} added you to {title}.",
        )
    sc.commit()
    return chat_summary(
        sc, me_, row(sc, "SELECT * FROM so_chats WHERE id = ?", (chat_id,)) or {}
    )


def _unread(sc: sqlite3.Connection, chat_id: str, member: dict[str, Any]) -> int:
    return kit.count(
        sc,
        "SELECT COUNT(*) FROM so_messages WHERE chat_id = ? AND author_id != ? AND created_at > ? AND deleted_at IS NULL AND hidden_at IS NULL",
        (chat_id, member["account_id"], member["last_read_at"] or ""),
    )


def chat_summary(
    sc: sqlite3.Connection, me_: dict[str, Any], chat: dict[str, Any]
) -> dict[str, Any]:
    member = _member(sc, chat["id"], me_["id"], active=False) or {
        "account_id": me_["id"],
        "last_read_at": "",
        "muted": 0,
        "role": "member",
    }
    others = []
    for item in rows(
        sc,
        "SELECT account_id, role FROM so_chat_members WHERE chat_id = ? AND left_at IS NULL",
        (chat["id"],),
    ):
        if item["account_id"] == me_["id"]:
            continue
        try:
            other = account(sc, item["account_id"])
        except LookupError:
            continue
        others.append({**mini(other), "role": item["role"]})
    last = row(
        sc,
        "SELECT * FROM so_messages WHERE chat_id = ? AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (chat["id"],),
    )
    title = (
        chat["title"]
        if chat["kind"] == "group"
        else (others[0]["name"] if others else "Chat")
    )
    blocked_direct = chat["kind"] == "direct" and any(
        blocked(sc, me_["id"], o["id"]) for o in others
    )
    can_send = not me_.get("suspended_at") and not blocked_direct
    if can_send and chat["kind"] == "direct":
        can_send = all(
            may_send_direct(sc, me_, account(sc, o["id"]), chat) for o in others
        )
    return {
        "id": chat["id"],
        "kind": chat["kind"],
        "title": title,
        "avatar": others[0]["avatar"] if chat["kind"] == "direct" and others else "👥",
        "members": others,
        "admin": member.get("role") == "admin",
        "muted": bool(member.get("muted")),
        "unread": _unread(sc, chat["id"], member),
        "updated_at": chat["updated_at"],
        "blocked": blocked_direct,
        "can_send": bool(can_send),
        "listing_id": chat.get("listing_id") or "",
        "last": None
        if not last
        else {
            "text": ""
            if last["deleted_at"]
            else (last["text"] or ("📷" if last["photo"] else "")),
            "deleted": bool(last["deleted_at"]),
            "mine": last["author_id"] == me_["id"],
            "at": last["created_at"],
        },
    }


def chats(sc: sqlite3.Connection, me_: dict[str, Any]) -> dict[str, Any]:
    found = rows(
        sc,
        """SELECT c.* FROM so_chats c JOIN so_chat_members m ON m.chat_id = c.id
        WHERE m.account_id = ? AND m.left_at IS NULL ORDER BY c.updated_at DESC LIMIT 200""",
        (me_["id"],),
    )
    out = [chat_summary(sc, me_, chat) for chat in found]
    return {"chats": out, "unread": sum(c["unread"] for c in out if not c["muted"])}


def messages(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    chat_id: str,
    *,
    since: str = "",
    limit: int = 80,
) -> dict[str, Any]:
    chat = chat_row(sc, me_, chat_id)
    members = rows(
        sc,
        "SELECT account_id, last_read_at FROM so_chat_members WHERE chat_id = ? AND left_at IS NULL",
        (chat["id"],),
    )
    hidden = blocked_ids(sc, me_["id"])
    if since:
        found = rows(
            sc,
            "SELECT * FROM so_messages WHERE chat_id = ? AND updated_at > ? ORDER BY created_at LIMIT 500",
            (chat["id"], since),
        )
    else:
        found = list(
            reversed(
                rows(
                    sc,
                    "SELECT * FROM so_messages WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
                    (chat["id"], max(1, min(limit, 200))),
                )
            )
        )
    names: dict[str, dict[str, Any]] = {}
    out = []
    for item in found:
        if item["author_id"] in hidden or item["hidden_at"]:
            if since:
                out.append({"id": item["id"], "removed": True})
            continue
        if item["author_id"] not in names:
            try:
                names[item["author_id"]] = mini(account(sc, item["author_id"]))
            except LookupError:
                names[item["author_id"]] = {
                    "id": item["author_id"],
                    "name": "Someone",
                    "handle": "",
                    "avatar": "🙂",
                }
        mine = item["author_id"] == me_["id"]
        seen_by = [
            m["account_id"]
            for m in members
            if m["account_id"] != item["author_id"]
            and (m["last_read_at"] or "") >= item["created_at"]
        ]
        out.append(
            {
                "id": item["id"],
                "author": names[item["author_id"]],
                "text": "" if item["deleted_at"] else item["text"],
                "photo": "" if item["deleted_at"] else item["photo"],
                "deleted": bool(item["deleted_at"]),
                "created_at": item["created_at"],
                "mine": mine,
                "seen": bool(seen_by) if mine else None,
                "can_delete": mine
                and not item["deleted_at"]
                and store.parse(item["created_at"])
                > store.utcnow() - timedelta(hours=DELETE_WINDOW_HOURS),
            }
        )
    # how far the others have read: the browser marks "Seen" from it without fetching the whole chat again
    read_upto = max(
        (
            m["last_read_at"] or ""
            for m in members
            if m["account_id"] != me_["id"] and m["account_id"] not in hidden
        ),
        default="",
    )
    return {
        "chat": chat_summary(sc, me_, chat),
        "messages": out,
        "read_upto": read_upto,
        "now": stamp(),
    }


def send(
    sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    chat = chat_row(sc, me_, chat_id)
    if chat["kind"] == "direct":
        others = [
            m["account_id"]
            for m in rows(
                sc,
                "SELECT account_id FROM so_chat_members WHERE chat_id = ?",
                (chat["id"],),
            )
            if m["account_id"] != me_["id"]
        ]
        for other_id in others:
            if blocked(sc, me_["id"], other_id):
                raise PermissionError("You cannot message this person.")
            try:
                other = account(sc, other_id)
            except LookupError:
                continue
            if other.get("suspended_at"):
                raise PermissionError("This person cannot get messages right now.")
            if not may_send_direct(sc, me_, other, chat):
                raise PermissionError(
                    "You can message friends, and people who accept messages from everyone."
                )
    text = filtered(
        sc, kit.text(raw.get("text"), "a message", max_len=4000, multiline=True)
    )
    photos = _photos([raw["photo"]] if raw.get("photo") else [], limit=1)
    if not text and not photos:
        raise ValueError("Write a message.")
    rate(sc, me_, "message")
    message_id = new_id()
    moment = stamp()
    sc.execute(
        "INSERT INTO so_messages (id, chat_id, author_id, text, photo, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            chat["id"],
            me_["id"],
            text,
            photos[0] if photos else "",
            moment,
            moment,
        ),
    )
    sc.execute("UPDATE so_chats SET updated_at = ? WHERE id = ?", (moment, chat["id"]))
    sc.execute(
        "UPDATE so_chat_members SET last_read_at = ? WHERE chat_id = ? AND account_id = ?",
        (moment, chat["id"], me_["id"]),
    )
    # someone who left a direct chat gets it back when a new message arrives
    if chat["kind"] == "direct":
        sc.execute(
            "UPDATE so_chat_members SET left_at = NULL WHERE chat_id = ?", (chat["id"],)
        )
    sc.commit()
    return {
        "message": {
            "id": message_id,
            "text": text,
            "photo": photos[0] if photos else "",
            "created_at": moment,
            "mine": True,
        }
    }


def delete_message(
    sc: sqlite3.Connection, me_: dict[str, Any], message_id: str
) -> None:
    found = row(
        sc,
        "SELECT * FROM so_messages WHERE id = ?",
        (store.check_id(message_id, "message"),),
    )
    if (
        not found
        or found["author_id"] != me_["id"]
        or not _member(sc, found["chat_id"], me_["id"])
    ):
        raise LookupError("That message was not found.")
    if store.parse(found["created_at"]) <= store.utcnow() - timedelta(
        hours=DELETE_WINDOW_HOURS
    ):
        raise PermissionError(
            "A message can be deleted for everyone within an hour of sending it."
        )
    moment = stamp()
    sc.execute(
        "UPDATE so_messages SET deleted_at = ?, updated_at = ?, text = '', photo = '' WHERE id = ?",
        (moment, moment, found["id"]),
    )
    sc.commit()


def mark_read(sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str) -> None:
    chat = chat_row(sc, me_, chat_id)
    sc.execute(
        "UPDATE so_chat_members SET last_read_at = ? WHERE chat_id = ? AND account_id = ?",
        (stamp(), chat["id"], me_["id"]),
    )
    sc.commit()


def mute(sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str, on: bool) -> None:
    chat = chat_row(sc, me_, chat_id)
    sc.execute(
        "UPDATE so_chat_members SET muted = ? WHERE chat_id = ? AND account_id = ?",
        (1 if on else 0, chat["id"], me_["id"]),
    )
    sc.commit()


def leave(sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str) -> None:
    chat = chat_row(sc, me_, chat_id)
    sc.execute(
        "UPDATE so_chat_members SET left_at = ? WHERE chat_id = ? AND account_id = ?",
        (stamp(), chat["id"], me_["id"]),
    )
    if chat["kind"] == "group":
        admins = kit.count(
            sc,
            "SELECT COUNT(*) FROM so_chat_members WHERE chat_id = ? AND role = 'admin' AND left_at IS NULL",
            (chat["id"],),
        )
        if not admins:
            nxt = row(
                sc,
                "SELECT account_id FROM so_chat_members WHERE chat_id = ? AND left_at IS NULL ORDER BY joined_at LIMIT 1",
                (chat["id"],),
            )
            if nxt:
                sc.execute(
                    "UPDATE so_chat_members SET role = 'admin' WHERE chat_id = ? AND account_id = ?",
                    (chat["id"], nxt["account_id"]),
                )
    sc.commit()


def add_member(
    sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str, other_id: str
) -> dict[str, Any]:
    chat = chat_row(sc, me_, chat_id)
    member = _member(sc, chat["id"], me_["id"]) or {}
    if chat["kind"] != "group" or member.get("role") != "admin":
        raise PermissionError("Only the chat's admins add people.")
    other = visible_account(sc, me_, other_id)
    if not can_message(sc, me_, other):
        raise PermissionError("You can add friends only.")
    sc.execute(
        """INSERT INTO so_chat_members (chat_id, account_id, role, joined_at) VALUES (?, ?, 'member', ?)
        ON CONFLICT(chat_id, account_id) DO UPDATE SET left_at = NULL""",
        (chat["id"], other["id"], stamp()),
    )
    notify(
        sc,
        other["id"],
        "chat_added",
        me_["id"],
        "chat",
        chat["id"],
        f"{me_['display_name']} added you to {chat['title']}.",
    )
    sc.commit()
    return chat_summary(sc, me_, chat)


def remove_member(
    sc: sqlite3.Connection, me_: dict[str, Any], chat_id: str, other_id: str
) -> dict[str, Any]:
    chat = chat_row(sc, me_, chat_id)
    member = _member(sc, chat["id"], me_["id"]) or {}
    if chat["kind"] != "group" or member.get("role") != "admin":
        raise PermissionError("Only the chat's admins remove people.")
    store.check_id(other_id, "person")
    sc.execute(
        "UPDATE so_chat_members SET left_at = ? WHERE chat_id = ? AND account_id = ?",
        (stamp(), chat["id"], other_id),
    )
    sc.commit()
    return chat_summary(sc, me_, chat)


def unread_messages(sc: sqlite3.Connection, me_: dict[str, Any]) -> int:
    total = 0
    for member in rows(
        sc,
        "SELECT * FROM so_chat_members WHERE account_id = ? AND left_at IS NULL AND muted = 0",
        (me_["id"],),
    ):
        total += _unread(sc, member["chat_id"], member)
    return total


# ---------------------------------------------------------------------------
# groups and pages
# ---------------------------------------------------------------------------


def group_row(sc: sqlite3.Connection, group_id: str) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_groups WHERE id = ? AND archived_at IS NULL",
        (store.check_id(group_id, "group"),),
    )
    if not found:
        raise LookupError("That group was not found.")
    return found


def _group_admin(sc: sqlite3.Connection, group_id: str, account_id: str) -> bool:
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_group_members WHERE group_id = ? AND account_id = ? AND role = 'admin' AND status = 'member'",
            (group_id, account_id),
        )
    )


def create_group(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    if me_["age_group"] == "child":
        raise PermissionError("Ask an adult to make a group.")
    rate(sc, me_, "group")
    group_id = new_id()
    moment = stamp()
    sc.execute(
        "INSERT INTO so_groups (id, name, about, visibility, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            group_id,
            kit.text(raw.get("name"), "a group name", max_len=80, required=True),
            kit.text(
                raw.get("about"),
                "what the group is about",
                max_len=1000,
                multiline=True,
            ),
            kit.choice(
                raw.get("visibility"),
                "who can see the group",
                VISIBILITIES,
                default="public",
            ),
            me_["id"],
            moment,
        ),
    )
    sc.execute(
        "INSERT INTO so_group_members (group_id, account_id, role, status, joined_at) VALUES (?, ?, 'admin', 'member', ?)",
        (group_id, me_["id"], moment),
    )
    sc.commit()
    return group_view(sc, me_, group_row(sc, group_id))


def group_view(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    group: dict[str, Any],
    *,
    with_posts: bool = False,
) -> dict[str, Any]:
    mine = row(
        sc,
        "SELECT role, status FROM so_group_members WHERE group_id = ? AND account_id = ?",
        (group["id"], me_["id"]),
    )
    view: dict[str, Any] = {
        "id": group["id"],
        "name": group["name"],
        "about": group["about"],
        "visibility": group["visibility"],
        "members": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_group_members WHERE group_id = ? AND status = 'member'",
            (group["id"],),
        ),
        "status": mine["status"] if mine else "none",
        "admin": bool(mine and mine["role"] == "admin" and mine["status"] == "member"),
    }
    member = bool(mine and mine["status"] == "member")
    if with_posts and (group["visibility"] == "public" or member):
        view["posts"] = [
            post_view(sc, me_, item)
            for item in rows(
                sc,
                "SELECT * FROM so_posts WHERE group_id = ? AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 50",
                (group["id"],),
            )
            if can_see_post(sc, me_, item)
        ]
    if view["admin"]:
        view["requests"] = [
            mini(account(sc, r["account_id"]))
            for r in rows(
                sc,
                "SELECT account_id FROM so_group_members WHERE group_id = ? AND status = 'pending'",
                (group["id"],),
            )
        ]
    return view


def groups(sc: sqlite3.Connection, me_: dict[str, Any], q: str = "") -> dict[str, Any]:
    mine = [
        group_view(sc, me_, g)
        for g in rows(
            sc,
            """SELECT g.* FROM so_groups g JOIN so_group_members m ON m.group_id = g.id
            WHERE m.account_id = ? AND g.archived_at IS NULL ORDER BY g.name""",
            (me_["id"],),
        )
    ]
    like = f"%{kit.text(q, 'a search', max_len=60).lower()}%"
    known = {g["id"] for g in mine}
    discover = [
        group_view(sc, me_, g)
        for g in rows(
            sc,
            "SELECT * FROM so_groups WHERE archived_at IS NULL AND visibility = 'public' AND lower(name) LIKE ? ORDER BY created_at DESC LIMIT 30",
            (like,),
        )
        if g["id"] not in known
    ]
    return {"mine": mine, "discover": discover}


def join_group(
    sc: sqlite3.Connection, me_: dict[str, Any], group_id: str
) -> dict[str, Any]:
    group = group_row(sc, group_id)
    if me_["age_group"] == "child" and group["visibility"] == "public":
        raise PermissionError(
            "Children can join private groups an admin lets them into."
        )
    status = "member" if group["visibility"] == "public" else "pending"
    sc.execute(
        """INSERT INTO so_group_members (group_id, account_id, role, status, joined_at) VALUES (?, ?, 'member', ?, ?)
        ON CONFLICT(group_id, account_id) DO NOTHING""",
        (group["id"], me_["id"], status, stamp()),
    )
    if status == "pending":
        for admin in rows(
            sc,
            "SELECT account_id FROM so_group_members WHERE group_id = ? AND role = 'admin'",
            (group["id"],),
        ):
            notify(
                sc,
                admin["account_id"],
                "group_request",
                me_["id"],
                "group",
                group["id"],
                f"{me_['display_name']} asked to join {group['name']}.",
            )
    sc.commit()
    return group_view(sc, me_, group)


def leave_group(
    sc: sqlite3.Connection, me_: dict[str, Any], group_id: str
) -> dict[str, Any]:
    group = group_row(sc, group_id)
    sc.execute(
        "DELETE FROM so_group_members WHERE group_id = ? AND account_id = ?",
        (group["id"], me_["id"]),
    )
    if not kit.count(
        sc,
        "SELECT COUNT(*) FROM so_group_members WHERE group_id = ? AND role = 'admin'",
        (group["id"],),
    ):
        nxt = row(
            sc,
            "SELECT account_id FROM so_group_members WHERE group_id = ? AND status = 'member' ORDER BY joined_at LIMIT 1",
            (group["id"],),
        )
        if nxt:
            sc.execute(
                "UPDATE so_group_members SET role = 'admin' WHERE group_id = ? AND account_id = ?",
                (group["id"], nxt["account_id"]),
            )
    sc.commit()
    return group_view(sc, me_, group)


def approve_member(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    group_id: str,
    other_id: str,
    approve: bool,
) -> dict[str, Any]:
    group = group_row(sc, group_id)
    if not _group_admin(sc, group["id"], me_["id"]):
        raise PermissionError("Only the group's admins do that.")
    store.check_id(other_id, "person")
    if approve:
        sc.execute(
            "UPDATE so_group_members SET status = 'member' WHERE group_id = ? AND account_id = ? AND status = 'pending'",
            (group["id"], other_id),
        )
        notify(
            sc,
            other_id,
            "group_joined",
            me_["id"],
            "group",
            group["id"],
            f"You are now a member of {group['name']}.",
        )
    else:
        sc.execute(
            "DELETE FROM so_group_members WHERE group_id = ? AND account_id = ? AND role != 'admin'",
            (group["id"], other_id),
        )
    sc.commit()
    return group_view(sc, me_, group)


def update_group(
    sc: sqlite3.Connection, me_: dict[str, Any], group_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    group = group_row(sc, group_id)
    if not _group_admin(sc, group["id"], me_["id"]):
        raise PermissionError("Only the group's admins change it.")
    values: dict[str, Any] = {}
    if "name" in raw:
        values["name"] = kit.text(
            raw["name"], "a group name", max_len=80, required=True
        )
    if "about" in raw:
        values["about"] = kit.text(
            raw["about"], "what the group is about", max_len=1000, multiline=True
        )
    if "visibility" in raw:
        values["visibility"] = kit.choice(
            raw["visibility"], "who can see the group", VISIBILITIES
        )
    _update(sc, "so_groups", group["id"], values)
    sc.commit()
    return group_view(sc, me_, group_row(sc, group["id"]))


def delete_group(sc: sqlite3.Connection, me_: dict[str, Any], group_id: str) -> None:
    """An admin closes the group: it and its posts disappear for everyone."""
    group = group_row(sc, group_id)
    if not _group_admin(sc, group["id"], me_["id"]):
        raise PermissionError("Only the group's admins delete it.")
    moment = stamp()
    sc.execute(
        "UPDATE so_groups SET archived_at = ? WHERE id = ?", (moment, group["id"])
    )
    sc.execute(
        "UPDATE so_posts SET updated_at = ? WHERE group_id = ? AND deleted_at IS NULL",
        (moment, group["id"]),
    )
    sc.commit()


def update_page(
    sc: sqlite3.Connection, me_: dict[str, Any], page_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    found = page(sc, page_id)
    if not _page_admin(sc, found["id"], me_["id"]):
        raise PermissionError("Only the page's admins change it.")
    values: dict[str, Any] = {}
    if "name" in raw:
        values["name"] = kit.text(raw["name"], "a page name", max_len=80, required=True)
    if "about" in raw:
        values["about"] = kit.text(
            raw["about"], "what the page is about", max_len=1000, multiline=True
        )
    if "category" in raw:
        values["category"] = kit.text(raw["category"], "a category", max_len=40)
    if "link" in raw:
        link = kit.url(raw["link"], "the link")
        if link and not link.lower().startswith("https://"):
            raise ValueError("The link must start with https://.")
        values["link"] = link
    _update(sc, "so_pages", found["id"], values)
    sc.commit()
    return page_view(sc, me_, page(sc, found["id"]))


def delete_page(sc: sqlite3.Connection, me_: dict[str, Any], page_id: str) -> None:
    found = page(sc, page_id)
    if not _page_admin(sc, found["id"], me_["id"]):
        raise PermissionError("Only the page's admins delete it.")
    moment = stamp()
    sc.execute(
        "UPDATE so_pages SET archived_at = ? WHERE id = ?", (moment, found["id"])
    )
    sc.execute(
        "DELETE FROM so_follows WHERE target_type = 'page' AND target_id = ?",
        (found["id"],),
    )
    sc.execute(
        "UPDATE so_posts SET updated_at = ? WHERE page_id = ? AND deleted_at IS NULL",
        (moment, found["id"]),
    )
    sc.commit()


def page(sc: sqlite3.Connection, page_id: str) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_pages WHERE id = ? AND archived_at IS NULL",
        (store.check_id(page_id, "page"),),
    )
    if not found:
        raise LookupError("That page was not found.")
    return found


def create_page(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    if me_["age_group"] in ("child", "teen"):
        raise PermissionError("Pages are for adults.")
    link = kit.url(raw.get("link"), "the link")
    if link and not link.lower().startswith("https://"):
        raise ValueError("The link must start with https://.")
    rate(sc, me_, "page")
    page_id = new_id()
    sc.execute(
        "INSERT INTO so_pages (id, name, about, category, link, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            page_id,
            kit.text(raw.get("name"), "a page name", max_len=80, required=True),
            kit.text(
                raw.get("about"), "what the page is about", max_len=1000, multiline=True
            ),
            kit.text(raw.get("category"), "a category", max_len=40),
            link,
            me_["id"],
            stamp(),
        ),
    )
    sc.execute(
        "INSERT INTO so_page_admins (page_id, account_id) VALUES (?, ?)",
        (page_id, me_["id"]),
    )
    sc.commit()
    return page_view(sc, me_, page(sc, page_id))


def page_view(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    found: dict[str, Any],
    *,
    with_posts: bool = False,
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": found["id"],
        "name": found["name"],
        "about": found["about"],
        "category": found["category"],
        "link": found["link"],
        "followers": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_follows WHERE target_type = 'page' AND target_id = ?",
            (found["id"],),
        ),
        "following": bool(
            row(
                sc,
                "SELECT 1 AS x FROM so_follows WHERE follower_id = ? AND target_type = 'page' AND target_id = ?",
                (me_["id"], found["id"]),
            )
        ),
        "admin": _page_admin(sc, found["id"], me_["id"]),
    }
    if with_posts:
        view["posts"] = [
            post_view(sc, me_, item)
            for item in rows(
                sc,
                "SELECT * FROM so_posts WHERE page_id = ? AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 50",
                (found["id"],),
            )
            if can_see_post(sc, me_, item)
        ]
    return view


def pages(sc: sqlite3.Connection, me_: dict[str, Any], q: str = "") -> dict[str, Any]:
    followed = [
        page_view(sc, me_, p)
        for p in rows(
            sc,
            """SELECT p.* FROM so_pages p WHERE p.archived_at IS NULL AND (p.id IN (SELECT target_id FROM so_follows WHERE follower_id = ? AND target_type = 'page')
            OR p.id IN (SELECT page_id FROM so_page_admins WHERE account_id = ?)) ORDER BY p.name""",
            (me_["id"], me_["id"]),
        )
    ]
    like = f"%{kit.text(q, 'a search', max_len=60).lower()}%"
    known = {p["id"] for p in followed}
    discover = [
        page_view(sc, me_, p)
        for p in rows(
            sc,
            "SELECT * FROM so_pages WHERE archived_at IS NULL AND lower(name) LIKE ? ORDER BY created_at DESC LIMIT 30",
            (like,),
        )
        if p["id"] not in known
    ]
    return {"mine": followed, "discover": discover}


# ---------------------------------------------------------------------------
# reports and moderation
# ---------------------------------------------------------------------------


def report(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    target_type = kit.choice(
        raw.get("target_type"), "what you are reporting", REPORT_TARGETS
    )
    target_id = store.check_id(str(raw.get("target_id") or ""), target_type)
    reason = (
        kit.text(raw.get("reason"), "a reason", max_len=500, multiline=True)
        or "No reason given"
    )
    if target_type == "post":
        visible_post(sc, me_, target_id)
    elif target_type == "comment":
        found = row(sc, "SELECT post_id FROM so_comments WHERE id = ?", (target_id,))
        if not found:
            raise LookupError("That comment was not found.")
        visible_post(sc, me_, found["post_id"])
    elif target_type == "message":
        found = row(sc, "SELECT chat_id FROM so_messages WHERE id = ?", (target_id,))
        if not found or not _member(sc, found["chat_id"], me_["id"], active=False):
            raise LookupError("That message was not found.")
    elif target_type == "story":
        found = row(sc, "SELECT author_id FROM so_stories WHERE id = ?", (target_id,))
        if not found or not (
            found["author_id"] == me_["id"]
            or are_friends(sc, me_["id"], found["author_id"])
        ):
            raise LookupError("That story was not found.")
    elif target_type == "listing":
        from aira.suite.social import market

        market.visible(sc, me_, target_id)
    else:
        visible_account(sc, me_, target_id)
    if row(
        sc,
        "SELECT 1 AS x FROM so_reports WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND status = 'open'",
        (me_["id"], target_type, target_id),
    ):
        return {"reported": True}
    rate(sc, me_, "report")
    sc.execute(
        "INSERT INTO so_reports (id, reporter_id, target_type, target_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), me_["id"], target_type, target_id, reason, stamp()),
    )
    sc.commit()
    return {"reported": True}


def _target(
    sc: sqlite3.Connection, target_type: str, target_id: str
) -> dict[str, Any] | None:
    table = {
        "post": "so_posts",
        "comment": "so_comments",
        "message": "so_messages",
        "story": "so_stories",
        "account": "so_accounts",
        "listing": "so_listings",
    }[target_type]
    return row(sc, f"SELECT * FROM {table} WHERE id = ?", (target_id,))  # noqa: S608 - fixed table names


def _author_of(target_type: str, target: dict[str, Any]) -> str:
    return str(
        target.get("author_id")
        or target.get("seller_id")
        or (target.get("id") if target_type == "account" else "")
        or ""
    )


def _home_of(sc: sqlite3.Connection, account_id: str) -> str:
    found = row(sc, "SELECT home FROM so_accounts WHERE id = ?", (account_id,))
    return found["home"] if found else ""


def _in_scope(
    sc: sqlite3.Connection, report_row: dict[str, Any], home: str | None
) -> bool:
    """A household owner moderates reports their household filed, or about their household's people;
    a platform moderator (``home`` None) moderates every report."""
    if home is None:
        return True
    target = _target(sc, report_row["target_type"], report_row["target_id"]) or {}
    return home in (
        _home_of(sc, report_row["reporter_id"]),
        _home_of(sc, _author_of(report_row["target_type"], target)),
    )


def reports(
    sc: sqlite3.Connection, *, status: str = "open", home: str | None = None
) -> list[dict[str, Any]]:
    """The moderation queue, with a preview of each reported thing (for a moderator only)."""
    out = []
    for item in rows(
        sc,
        "SELECT * FROM so_reports WHERE status = ? ORDER BY created_at DESC LIMIT 500",
        (status,),
    ):
        if not _in_scope(sc, item, home):
            continue
        target = _target(sc, item["target_type"], item["target_id"]) or {}
        author_id = _author_of(item["target_type"], target)
        author = None
        if author_id:
            try:
                author = mini(account(sc, author_id))
            except LookupError:
                author = None
        reporter = None
        try:
            reporter = mini(account(sc, item["reporter_id"]))
        except LookupError:
            pass
        out.append(
            {
                **item,
                "reporter": reporter,
                "author": author,
                "preview": (
                    target.get("text")
                    or target.get("title")
                    or target.get("display_name")
                    or ""
                )[:300],
                "hidden": bool(target.get("hidden_at") or target.get("suspended_at")),
                "can_suspend": home is None or _home_of(sc, author_id) == home,
            }
        )
        if len(out) >= 200:
            break
    return out


def moderate(
    sc: sqlite3.Connection,
    moderator: str,
    report_id: str,
    action: str,
    *,
    home: str | None = None,
) -> dict[str, Any]:
    """``hide`` the reported thing, ``suspend`` its author, or ``dismiss`` the report. A household owner
    (``home``) acts on reports in their scope and suspends only their own household's people."""
    found = row(
        sc,
        "SELECT * FROM so_reports WHERE id = ?",
        (store.check_id(report_id, "report"),),
    )
    if not found or not _in_scope(sc, found, home):
        raise LookupError("That report was not found.")
    action = kit.choice(action, "the action", ("hide", "suspend", "dismiss"))
    moment = stamp()
    target = _target(sc, found["target_type"], found["target_id"]) or {}
    if (
        (action == "suspend" or found["target_type"] == "account")
        and action != "dismiss"
        and home is not None
    ):
        if _home_of(sc, _author_of(found["target_type"], target)) != home:
            raise PermissionError(
                "You can hide what was reported; only Monosage's moderators suspend someone outside your household."
            )
    if action == "hide" and found["target_type"] != "account":
        table = {
            "post": "so_posts",
            "comment": "so_comments",
            "message": "so_messages",
            "story": "so_stories",
            "listing": "so_listings",
        }[found["target_type"]]
        extra = (
            ", updated_at = ?"
            if found["target_type"] in ("post", "message", "listing")
            else ""
        )
        params = (
            (moment, moment, found["target_id"])
            if extra
            else (moment, found["target_id"])
        )
        sc.execute(f"UPDATE {table} SET hidden_at = ?{extra} WHERE id = ?", params)  # noqa: S608 - fixed names
    elif action == "suspend" or (
        action == "hide" and found["target_type"] == "account"
    ):
        author_id = _author_of(found["target_type"], target)
        if author_id:
            set_suspended(sc, author_id, True)
    sc.execute(
        "UPDATE so_reports SET status = ?, action = ?, resolved_at = ?, resolved_by = ? WHERE target_type = ? AND target_id = ? AND status = 'open'",
        (
            "dismissed" if action == "dismiss" else "resolved",
            action,
            moment,
            moderator[:120],
            found["target_type"],
            found["target_id"],
        ),
    )
    sc.commit()
    return {"action": action}


def set_suspended(sc: sqlite3.Connection, account_id: str, on: bool) -> None:
    sc.execute(
        "UPDATE so_accounts SET suspended_at = ? WHERE id = ?",
        (stamp() if on else None, account_id),
    )
    sc.commit()


# ---------------------------------------------------------------------------
# a person's data: export and erasure (an organization deleted, a member removed)
# ---------------------------------------------------------------------------


def accounts_for(
    sc: sqlite3.Connection, *, home: str = "", person_keys: Iterable[str] = ()
) -> list[str]:
    """Account ids of a household or organization (``home``) and/or of the given person keys."""
    found: list[str] = []
    if home:
        found += [
            r["id"]
            for r in rows(sc, "SELECT id FROM so_accounts WHERE home = ?", (home,))
        ]
    for key in person_keys:
        hit = row(sc, "SELECT id FROM so_accounts WHERE person_key = ?", (key,))
        if hit:
            found.append(hit["id"])
    return list(dict.fromkeys(found))


def export_account(sc: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    """Everything this person wrote or set (never other people's words)."""
    found = row(sc, "SELECT * FROM so_accounts WHERE id = ?", (account_id,))
    if not found:
        raise LookupError("That person was not found.")
    found.pop("person_key", None)
    return {
        "account": found,
        "posts": rows(
            sc,
            "SELECT id, text, photos, audience, created_at, updated_at FROM so_posts WHERE author_id = ? ORDER BY created_at",
            (account_id,),
        ),
        "comments": rows(
            sc,
            "SELECT id, post_id, text, created_at FROM so_comments WHERE author_id = ? ORDER BY created_at",
            (account_id,),
        ),
        "messages": rows(
            sc,
            "SELECT id, chat_id, text, photo, created_at FROM so_messages WHERE author_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (account_id,),
        ),
        "friends": [
            r["other"]
            for r in rows(
                sc,
                "SELECT CASE WHEN a_id = ? THEN b_id ELSE a_id END AS other FROM so_friends WHERE a_id = ? OR b_id = ?",
                (account_id, account_id, account_id),
            )
        ],
        "stories": rows(
            sc,
            "SELECT id, text, photo, created_at, expires_at FROM so_stories WHERE author_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (account_id,),
        ),
        "listings": rows(
            sc,
            """SELECT id, title, about, price_cents, currency, category, condition, place, photos, audience, status,
            buyer, sold_cents, sold_at, created_at FROM so_listings WHERE seller_id = ? AND deleted_at IS NULL ORDER BY created_at""",
            (account_id,),
        ),
    }


def erase_accounts(sc: sqlite3.Connection, account_ids: Iterable[str]) -> int:
    """Remove these people from Social: their profile, posts, comments, stories, messages, reactions,
    friendships, memberships and notifications. Groups, pages and chats they made stay for the others."""
    ids = [str(a) for a in account_ids if a]
    for account_id in ids:
        posts = [
            r["id"]
            for r in rows(
                sc, "SELECT id FROM so_posts WHERE author_id = ?", (account_id,)
            )
        ]
        for post_id in posts:
            for table in ("so_post_tags", "so_reactions", "so_comments"):
                sc.execute(f"DELETE FROM {table} WHERE post_id = ?", (post_id,))  # noqa: S608 - fixed names
        stories = [
            r["id"]
            for r in rows(
                sc, "SELECT id FROM so_stories WHERE author_id = ?", (account_id,)
            )
        ]
        for story_id in stories:
            sc.execute("DELETE FROM so_story_views WHERE story_id = ?", (story_id,))
        for statement in (
            "DELETE FROM so_posts WHERE author_id = ?",
            "DELETE FROM so_comments WHERE author_id = ?",
            "DELETE FROM so_reactions WHERE account_id = ?",
            "DELETE FROM so_stories WHERE author_id = ?",
            "DELETE FROM so_story_views WHERE viewer_id = ?",
            "DELETE FROM so_messages WHERE author_id = ?",
            "DELETE FROM so_chat_members WHERE account_id = ?",
            "DELETE FROM so_group_members WHERE account_id = ?",
            "DELETE FROM so_page_admins WHERE account_id = ?",
            "DELETE FROM so_notifications WHERE account_id = ? OR actor_id = ?",
            "DELETE FROM so_friends WHERE a_id = ? OR b_id = ?",
            "DELETE FROM so_follows WHERE follower_id = ? OR (target_type = 'account' AND target_id = ?)",
            "DELETE FROM so_blocks WHERE blocker_id = ? OR blocked_id = ?",
            "DELETE FROM so_reports WHERE reporter_id = ? OR (target_type = 'account' AND target_id = ?)",
            "DELETE FROM so_rate WHERE account_id = ?",
            "DELETE FROM so_listing_saves WHERE account_id = ? OR listing_id IN (SELECT id FROM so_listings WHERE seller_id = ?)",
            "DELETE FROM so_listings WHERE seller_id = ?",
            "DELETE FROM so_guardian WHERE child_id = ? OR other_id = ?",
            "DELETE FROM so_accounts WHERE id = ?",
        ):
            sc.execute(statement, (account_id,) * statement.count("?"))
    sc.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


def today_cards(
    sc: sqlite3.Connection, profile: dict[str, Any]
) -> list[dict[str, Any]]:
    acct = account_by_key(sc, person_key(profile))
    if not acct or acct.get("suspended_at"):
        return []
    lines = []
    for chat in chats(sc, acct)["chats"]:
        if chat["unread"] and not chat["muted"]:
            lines.append(
                {
                    "text": f"{chat['title']}: {chat['unread']} new",
                    "sub": "Open Social to read and reply.",
                }
            )
        if len(lines) >= 3:
            break
    for asking in guardian_requests(sc, acct)[:3]:
        lines.append(
            {
                "text": f"{asking['child']['name']} wants to be friends with {asking['other']['name']}",
                "sub": f"@{asking['other']['handle']} · say yes or no",
                "action": life.action(
                    "Say yes",
                    f"/api/social/guardian/{asking['child']['id']}/{asking['other']['id']}",
                    body={"approve": True},
                ),
            }
        )
    for incoming in friends_overview(sc, acct)["incoming"][:3]:
        lines.append(
            {
                "text": f"{incoming['name']} wants to be friends",
                "sub": f"@{incoming['handle']}",
                "action": life.action(
                    "Accept", f"/api/social/friends/{incoming['id']}"
                ),
            }
        )
    return [
        life.card(
            "social",
            "From your friends",
            lines=lines,
            empty="No new messages or requests.",
        )
    ]


def pulse(
    sc: sqlite3.Connection, profile: dict[str, Any], since: str
) -> dict[str, Any]:
    acct = account_by_key(sc, person_key(profile))
    if not acct:
        return {"badge": 0, "alerts": []}
    badge = unread_messages(sc, acct) + unread_notifications(sc, acct)
    alerts = []
    floor = store.later(
        -0.5
    )  # alerts for messages of the last half hour that are still unread
    try:
        cutoff = max(floor, store.since_value(since) or floor)
    except ValueError:
        cutoff = floor
    found = rows(
        sc,
        """SELECT m.* FROM so_messages m JOIN so_chat_members c ON c.chat_id = m.chat_id
        WHERE c.account_id = ? AND c.left_at IS NULL AND c.muted = 0 AND m.author_id != ? AND m.created_at > ?
        AND m.created_at > c.last_read_at AND m.deleted_at IS NULL AND m.hidden_at IS NULL ORDER BY m.created_at LIMIT 5""",
        (acct["id"], acct["id"], cutoff),
    )
    for item in found:
        if blocked(sc, acct["id"], item["author_id"]):
            continue
        try:
            author = account(sc, item["author_id"])
        except LookupError:
            continue
        alerts.append(
            {
                "id": f"social:msg:{item['id']}",
                "title": f"New message from {author['display_name']}",
                "body": (item["text"] or "📷")[:140],
                "speak": True,
                "actions": [
                    life.action(
                        "Mark as read", f"/api/social/chats/{item['chat_id']}/read"
                    )
                ],
            }
        )
    return {"badge": min(badge, 99), "alerts": alerts}


# ---------------------------------------------------------------------------
# the public profile page (a link an adult with a public profile shares outside Aira)
# ---------------------------------------------------------------------------


def public_profile(sc: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    try:
        acct = account(sc, account_id)
    except LookupError:
        return {"available": False, "title": "This profile is no longer shared."}
    if (
        acct.get("suspended_at")
        or acct["age_group"] in ("child", "teen")
        or acct["visibility"] != "public"
    ):
        return {"available": False, "title": "This profile is private."}
    posts = []
    for item in rows(
        sc,
        """SELECT * FROM so_posts WHERE author_id = ? AND audience = 'public' AND group_id IS NULL AND page_id IS NULL
        AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 20""",
        (acct["id"],),
    ):
        posts.append(
            {
                "id": item["id"],
                "text": item["text"],
                "photos": store.loads(item["photos"], []),
                "created_at": item["created_at"],
                "reactions": kit.count(
                    sc,
                    "SELECT COUNT(*) FROM so_reactions WHERE post_id = ?",
                    (item["id"],),
                ),
            }
        )
    return {
        "available": True,
        "title": acct["display_name"],
        "person": {
            "name": acct["display_name"],
            "handle": acct["handle"],
            "avatar": acct["avatar"],
            "bio": acct["bio"],
            "headline": acct["headline"],
            "open_to_work": acct["open_to_work"],
            "skills": acct["skills"],
            "experience": acct["experience"],
        },
        "posts": posts,
    }
