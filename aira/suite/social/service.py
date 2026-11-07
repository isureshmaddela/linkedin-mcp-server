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
from aira.suite.social import moderation, photos, store
from aira.suite.social.store import new_id, row, rows, stamp

REACTIONS = ("❤️", "👍", "😂", "😮", "😢", "🎉")
STORY_REACTIONS = ("❤️", "😂", "😮", "😢", "👏", "🔥")
# close_friends: only the people on the author's close friends list (who are also their friends)
AUDIENCES = ("public", "friends", "close_friends", "only_me")
# A feeling or activity beside a post: the browser shows the emoji and the words.
FEELINGS = {
    "happy": "😊",
    "grateful": "🙏",
    "excited": "🤩",
    "loved": "🥰",
    "proud": "😌",
    "relaxed": "😎",
    "tired": "😴",
    "sad": "😢",
    "celebrating": "🎉",
    "eating": "🍽️",
    "travelling": "✈️",
    "watching": "🎬",
    "reading": "📖",
    "listening": "🎧",
    "playing": "🎮",
    "working": "💼",
    "exercising": "🏃",
    "cooking": "🍳",
}
# Photo filters: the browser applies a CSS filter by this name when it shows the photos (the files stay as sent).
FILTERS = ("", "warm", "cool", "mono", "vivid", "fade", "vintage", "dramatic")
POLL_DAYS = (1, 3, 7)
MAX_PINNED = 3
MAX_LINKS = 5
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
    "photo": 120,
    "story_reply": 120,
}
# The moderators' own word list starts as the swear words; moderation.py keeps the categorised term lists.
DEFAULT_BLOCKLIST = moderation.PROFANITY
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
    life.require_app(
        connection, profile, "social"
    )  # a guardian may turn Social off for a child or a teen
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
    found["links"] = store.loads(found.get("links"), [])
    found["cover"] = found.get("cover") or ""
    found["open_messages"] = bool(found.get("open_messages"))
    found["open_to_work"] = bool(found.get("open_to_work"))
    return found


def account_out(acct: dict[str, Any]) -> dict[str, Any]:
    """The person's own account as the browser sees it: uploaded pictures as addresses it can load."""
    out = {key: value for key, value in acct.items() if key != "person_key"}
    out["avatar"] = photos.display(acct.get("avatar"))
    out["cover"] = photos.display(acct.get("cover"))
    return out


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


UPLOADS_ONLY = (
    "Upload the photo from your device. Links to pictures on other websites are not used: they could tell that "
    "website who looks at them."
)


def _avatar(
    value: Any, fallback: str, *, sc: sqlite3.Connection | None = None, owner: str = ""
) -> str:
    """An emoji, or a photo this person uploaded (``media:<id>``); never a link to a picture elsewhere."""
    text = str(value or "").strip()
    if not text:
        return fallback
    if photos.is_ref(text) and sc is not None and owner:
        return photos.owned(sc, text, owner)
    if photos.external(text) or photos.is_ref(text):
        raise ValueError(UPLOADS_ONLY)
    if len(text) > 8:
        raise ValueError("Use an emoji or a photo from your device.")
    return text


def _picture(value: Any, sc: sqlite3.Connection, owner: str, label: str) -> str:
    """A cover, group or page picture: empty, or a photo this person uploaded (``media:<id>``)."""
    text = str(value or "").strip()
    if not text:
        return ""
    if photos.is_ref(text):
        return photos.owned(sc, text, owner)
    raise ValueError(UPLOADS_ONLY)


def _links(value: Any) -> str:
    """Links in the bio: at most five, https only, each with an optional short label."""
    items = value if isinstance(value, list) else []
    if len(items) > MAX_LINKS:
        raise ValueError(f"At most {MAX_LINKS} links.")
    clean = []
    for item in items:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            raise ValueError("Each link is an address and a short label.")
        link = kit.url(item.get("url"), "the link", required=True)
        if not link.lower().startswith("https://"):
            raise ValueError("Links must start with https://.")
        label = kit.text(item.get("label"), "the link's label", max_len=40)
        clean.append({"url": link, "label": label})
    return store.dumps(clean)


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
        values["avatar"] = _avatar(
            raw["avatar"], acct["avatar"], sc=sc, owner=acct["id"]
        )
    if "cover" in raw:
        values["cover"] = _picture(raw["cover"], sc, acct["id"], "the cover photo link")
    if "links" in raw:
        values["links"] = _links(raw["links"])
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
        # a friend, a request between them, someone from the child's own household or an approved contact
        return (
            bool(friendship(sc, viewer["id"], target["id"]))
            or _same_household(viewer, target)
            or _approved(sc, target["id"], viewer["id"])
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
        "avatar": photos.display(acct["avatar"]),
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
        "following_count": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_follows WHERE follower_id = ? AND target_type = 'account'",
            (target["id"],),
        ),
        "can_message": rel == "self" or can_message(sc, viewer, target),
        "young": target["age_group"] in ("child", "teen"),
        "cover": photos.display(target.get("cover")),
        "full": full,
        # the viewer put this friend on their close friends list (only the viewer knows)
        "close_friend": rel == "friends"
        and is_close_friend(sc, viewer["id"], target["id"]),
    }
    if full:
        view.update(
            {
                "bio": target["bio"],
                "headline": target["headline"],
                "open_to_work": target["open_to_work"],
                "skills": target["skills"],
                "experience": target["experience"],
                "links": target.get("links") or [],
                "posts_count": kit.count(
                    sc,
                    """SELECT COUNT(*) FROM so_posts WHERE author_id = ? AND group_id IS NULL AND page_id IS NULL
                    AND deleted_at IS NULL AND hidden_at IS NULL"""
                    + (
                        ""
                        if rel == "self"
                        else " AND audience = 'public'"
                        if rel != "friends"
                        else " AND audience IN ('public', 'friends')"
                    ),
                    (target["id"],),
                ),
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
        "SELECT id, name, about, visibility, photo FROM so_groups WHERE archived_at IS NULL AND visibility = 'public' AND lower(name) LIKE ? ORDER BY name LIMIT 20",
        (like,),
    )
    pages = rows(
        sc,
        "SELECT id, name, about, category, photo FROM so_pages WHERE archived_at IS NULL AND lower(name) LIKE ? ORDER BY name LIMIT 20",
        (like,),
    )
    for item in [*groups, *pages]:
        item["photo"] = photos.display(item.get("photo"), sc=sc)
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
    # A child's new friend from outside the household, and a teen's new adult friend who is not on their approved
    # contacts, wait for a guardian's yes.
    needs_yes = (minor(me_) and not _same_household(me_, other)) or (
        me_["age_group"] == "teen"
        and not young(other)
        and not contact_ok(sc, me_, other)
    )
    if needs_yes and not approved:
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
    if minor(other) and not minor(me_) and not contact_ok(sc, other, me_):
        # a stranger cannot send a child a request: only their household, or someone a guardian approved
        raise PermissionError("Only family can send a friend request to a child.")
    if (
        other["age_group"] == "teen"
        and not young(me_)
        and not contact_ok(sc, other, me_)
    ):
        # nor can an adult stranger send one to a teen
        raise PermissionError(
            "Only family, and people their parent approved, can send a friend request to a teen."
        )
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


def contact_ok(
    sc: sqlite3.Connection, young_acct: dict[str, Any], other: dict[str, Any]
) -> bool:
    """Whether ``other`` may befriend or message this child or teen: someone of their household, another child or
    teen, or an account on the approved contacts a guardian keeps for them. Anyone may contact a grown-up."""
    if not young(young_acct):
        return True
    if _same_household(young_acct, other) or young(other):
        return True
    return _approved(sc, young_acct["id"], other["id"])


def _approved(sc: sqlite3.Connection, young_id: str, other_id: str) -> bool:
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_approved_contacts WHERE young_id = ? AND contact_id = ?",
            (young_id, other_id),
        )
    )


def approved_contacts(
    sc: sqlite3.Connection, me_: dict[str, Any], young_id: str
) -> dict[str, Any]:
    """The approved contacts of a child or teen, for them and their guardians."""
    person = account(sc, young_id)
    if not young(person) or not (
        person["id"] == me_["id"] or is_guardian(sc, me_, person)
    ):
        raise LookupError("That person was not found.")
    out = []
    for item in rows(
        sc,
        "SELECT * FROM so_approved_contacts WHERE young_id = ? ORDER BY created_at",
        (person["id"],),
    ):
        try:
            out.append(
                {**mini(account(sc, item["contact_id"])), "since": item["created_at"]}
            )
        except LookupError:
            continue
    return {
        "person": mini(person),
        "contacts": out,
        "can_change": is_guardian(sc, me_, person),
    }


def set_approved_contact(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    young_id: str,
    raw: dict[str, Any],
    on: bool,
) -> dict[str, Any]:
    """A guardian adds (by @name or id) or removes an approved contact of their child or teen."""
    person = account(sc, young_id)
    if not young(person) or not is_guardian(sc, me_, person):
        raise PermissionError(
            "Only a parent or guardian can change who may contact them."
        )
    wanted = (
        str(raw.get("contact_id") or raw.get("handle") or "")
        .strip()
        .lstrip("@")
        .lower()
    )
    found = (
        row(sc, "SELECT * FROM so_accounts WHERE id = ?", (wanted,))
        if store.HEX.fullmatch(wanted)
        else row(sc, "SELECT * FROM so_accounts WHERE handle = ?", (wanted,))
    )
    if not found or found["id"] == person["id"]:
        raise LookupError("That person was not found.")
    if on:
        if blocked(sc, person["id"], found["id"]) or found.get("suspended_at"):
            raise PermissionError("This person cannot be an approved contact.")
        sc.execute(
            "INSERT OR IGNORE INTO so_approved_contacts (young_id, contact_id, approved_by, created_at) VALUES (?, ?, ?, ?)",
            (person["id"], found["id"], me_["id"], stamp()),
        )
    else:
        sc.execute(
            "DELETE FROM so_approved_contacts WHERE young_id = ? AND contact_id = ?",
            (person["id"], found["id"]),
        )
    sc.commit()
    return approved_contacts(sc, me_, person["id"])


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
        young(child)
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
        WHERE c.home = ? AND c.age_group IN ('child', 'teen') ORDER BY g.created_at""",
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
        if not young(other) and not _same_household(child, other):
            # a grown-up the guardian said yes to is one of the child's approved contacts from now on
            sc.execute(
                "INSERT OR IGNORE INTO so_approved_contacts (young_id, contact_id, approved_by, created_at) VALUES (?, ?, ?, ?)",
                (child["id"], other["id"], me_["id"], stamp()),
            )
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
        # the household's children and teens whose approved contacts this grown-up keeps
        "young_in_household": [
            mini(_account(r) or {})
            for r in rows(
                sc,
                "SELECT * FROM so_accounts WHERE home = ? AND id != ? AND age_group IN ('child', 'teen') ORDER BY display_name",
                (me_.get("home") or "", me_["id"]),
            )
        ]
        if me_["age_group"] in ("adult", "senior") and me_.get("home")
        else [],
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
    sc.execute(
        "DELETE FROM so_close_friends WHERE (owner_id = ? AND friend_id = ?) OR (owner_id = ? AND friend_id = ?)",
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
# close friends, and who follows whom
# ---------------------------------------------------------------------------


def is_close_friend(sc: sqlite3.Connection, owner_id: str, friend_id: str) -> bool:
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_close_friends WHERE owner_id = ? AND friend_id = ?",
            (owner_id, friend_id),
        )
    )


def in_close_circle(sc: sqlite3.Connection, viewer_id: str, author_id: str) -> bool:
    """The author put the viewer on their close friends list, and they are still friends."""
    return is_close_friend(sc, author_id, viewer_id) and are_friends(
        sc, viewer_id, author_id
    )


def close_friends(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rows(
        sc,
        "SELECT friend_id FROM so_close_friends WHERE owner_id = ? ORDER BY created_at",
        (me_["id"],),
    ):
        try:
            other = account(sc, item["friend_id"])
        except LookupError:
            continue
        if other.get("suspended_at") or not are_friends(sc, me_["id"], other["id"]):
            continue
        out.append(mini(other))
    return out


def set_close_friend(
    sc: sqlite3.Connection, me_: dict[str, Any], other_id: str, on: bool
) -> dict[str, Any]:
    other = visible_account(sc, me_, other_id)
    if on:
        if not are_friends(sc, me_["id"], other["id"]):
            raise PermissionError(
                "Only your friends can be on your close friends list."
            )
        if (
            kit.count(
                sc,
                "SELECT COUNT(*) FROM so_close_friends WHERE owner_id = ?",
                (me_["id"],),
            )
            >= 300
        ):
            raise ValueError("Your close friends list is full (300 people).")
        sc.execute(
            "INSERT OR IGNORE INTO so_close_friends (owner_id, friend_id, created_at) VALUES (?, ?, ?)",
            (me_["id"], other["id"], stamp()),
        )
    else:
        sc.execute(
            "DELETE FROM so_close_friends WHERE owner_id = ? AND friend_id = ?",
            (me_["id"], other["id"]),
        )
    sc.commit()
    return {"close_friend": on}


def follow_lists(
    sc: sqlite3.Connection, me_: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """Who follows this person and whom they follow, for someone who may see their whole profile. People the
    viewer may not see (blocked, suspended, a child who is a stranger) are left out."""
    rel = relation(sc, me_, target)
    if not (rel in ("self", "friends") or target["visibility"] == "public"):
        raise PermissionError(
            "This profile is private: become friends to see who they follow."
        )

    def people(sql: str) -> list[dict[str, Any]]:
        out = []
        for item in rows(sc, sql, (target["id"],)):
            try:
                other = account(sc, item["other"])
            except LookupError:
                continue
            if not can_see_account(sc, me_, other):
                continue
            out.append({**mini(other), "relation": relation(sc, me_, other)})
        return out

    return {
        "followers": people(
            "SELECT follower_id AS other FROM so_follows WHERE target_type = 'account' AND target_id = ? ORDER BY created_at DESC LIMIT 300"
        ),
        "following": people(
            "SELECT target_id AS other FROM so_follows WHERE target_type = 'account' AND follower_id = ? ORDER BY created_at DESC LIMIT 300"
        ),
        "pages": [
            {"id": p["id"], "name": p["name"], "photo": photos.display(p["photo"])}
            for p in rows(
                sc,
                """SELECT p.id, p.name, p.photo FROM so_follows f JOIN so_pages p ON p.id = f.target_id
                WHERE f.follower_id = ? AND f.target_type = 'page' AND p.archived_at IS NULL ORDER BY p.name LIMIT 100""",
                (target["id"],),
            )
        ],
    }


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
    """The moderators' own words, starred out like swearing (the term lists themselves are in moderation.py)."""
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
    """Swear words and the moderators' own words starred out (moderation.mask)."""
    return moderation.mask(text, extra_words=blocklist(sc))


def young(acct: dict[str, Any] | None) -> bool:
    return bool(acct) and acct.get("age_group") in ("child", "teen")


def screen(
    sc: sqlite3.Connection, text: str, *, involves_young: bool
) -> moderation.Verdict:
    """The automatic check of one piece of writing: masked, or held for review when a child or a teen is
    involved and it has a flagged term, contact details or a move to another app (moderation.check)."""
    return moderation.check(text, young=involves_young, extra_words=blocklist(sc))


def _hold(
    sc: sqlite3.Connection,
    target_type: str,
    target_id: str,
    author: dict[str, Any],
    verdict: moderation.Verdict,
    *,
    young_ids: Iterable[str] = (),
) -> None:
    """Keep this (already hidden) writing back for a moderator: it is in the report queue as an automatic report,
    its author sees it waiting, nobody else sees it. The young people's guardians are told."""
    report_id = new_id()
    moment = stamp()
    people = []
    for young_id in dict.fromkeys(young_ids):
        try:
            people.append(account(sc, young_id))
        except LookupError:
            continue
    # the households that moderate it: the author's and each young person's
    homes = sorted(
        {h for h in [author.get("home"), *[p.get("home") for p in people]] if h}
    )
    sc.execute(
        "INSERT INTO so_reports (id, reporter_id, target_type, target_id, reason, created_at) VALUES (?, '', ?, ?, ?, ?)",
        (report_id, target_type, target_id, verdict.reason, moment),
    )
    sc.execute(
        """INSERT OR REPLACE INTO so_held (target_type, target_id, author_id, report_id, reasons, homes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            target_type,
            target_id,
            author["id"],
            report_id,
            ",".join(verdict.categories),
            "\n".join(homes),
            moment,
        ),
    )
    told: set[str] = set()
    for person in people:
        for grown_up in guardians(sc, person):
            if grown_up["id"] in told or grown_up["id"] == author["id"]:
                continue
            told.add(grown_up["id"])
            notify(
                sc,
                grown_up["id"],
                "safety_hold",
                "",
                "account",
                person["id"],
                f"Something written to or by {person['display_name']} was held for a safety check.",
            )


def held(
    sc: sqlite3.Connection, target_type: str, target_id: str
) -> dict[str, Any] | None:
    return row(
        sc,
        "SELECT * FROM so_held WHERE target_type = ? AND target_id = ?",
        (target_type, target_id),
    )


def _photos(
    value: Any,
    limit: int = 6,
    *,
    sc: sqlite3.Connection | None = None,
    owner: str = "",
) -> list[str]:
    """Photos this person uploaded (``media:<id>``). A link to a picture elsewhere is refused (UPLOADS_ONLY)."""
    items = (
        value if isinstance(value, list) else [p for p in str(value or "").split() if p]
    )
    if len(items) > limit:
        raise ValueError(f"At most {limit} photos.")
    out = []
    for item in items:
        if not photos.is_ref(item) or sc is None or not owner:
            raise ValueError(UPLOADS_ONLY)
        out.append(photos.owned(sc, item, owner))
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
    finish_polls(sc)
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


def read_notification(
    sc: sqlite3.Connection, me_: dict[str, Any], notification_id: str
) -> int:
    """Mark one notification read (the person opened it)."""
    found = row(
        sc,
        "SELECT id FROM so_notifications WHERE id = ? AND account_id = ?",
        (store.check_id(notification_id, "notification"), me_["id"]),
    )
    if not found:
        raise LookupError("That notification was not found.")
    sc.execute(
        "UPDATE so_notifications SET read_at = COALESCE(read_at, ?) WHERE id = ?",
        (stamp(), found["id"]),
    )
    sc.commit()
    return unread_notifications(sc, me_)


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
    if post["audience"] == "close_friends":
        return in_close_circle(sc, viewer["id"], author["id"])
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


def _alts(value: Any, count: int) -> list[str]:
    """A short description of each photo for people who cannot see it (one per photo, may be empty)."""
    items = value if isinstance(value, list) else []
    if len(items) > count:
        items = items[:count]
    out = [kit.text(item, "a photo description", max_len=200) for item in items]
    return out + [""] * (count - len(out))


def _poll(raw: Any) -> dict[str, Any] | None:
    """``{"options": [...], "days": 1|3|7}``: two to four different answers, each up to 80 characters."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("A poll is a list of answers.")
    options = [
        kit.text(item, "an answer", max_len=80)
        for item in (raw.get("options") or [])
        if str(item or "").strip()
    ]
    if len(options) < 2 or len(options) > 4:
        raise ValueError("A poll has two to four answers.")
    if len({item.lower() for item in options}) != len(options):
        raise ValueError("Each answer in a poll must be different.")
    try:
        days = int(raw.get("days") or 1)
    except (TypeError, ValueError):
        raise ValueError("A poll runs for 1, 3 or 7 days.") from None
    if days not in POLL_DAYS:
        raise ValueError("A poll runs for 1, 3 or 7 days.")
    return {"options": options, "days": days}


def create_post(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    text = kit.text(raw.get("text"), "something to share", max_len=2000, multiline=True)
    pictures = _photos(raw.get("photos"), sc=sc, owner=me_["id"])
    shared = str(raw.get("shared_post_id") or "")
    poll = _poll(raw.get("poll"))
    if not text and not pictures and not shared and not poll:
        raise ValueError("Write something or add a photo.")
    if poll and not text:
        raise ValueError("Write the question for your poll.")
    group_id = str(raw.get("group_id") or "")
    page_id = str(raw.get("page_id") or "")
    audience = kit.choice(
        raw.get("audience"), "who can see it", AUDIENCES, default="friends"
    )
    feeling = kit.choice(raw.get("feeling"), "the feeling", tuple(FEELINGS), default="")
    place = kit.text(raw.get("place"), "the place", max_len=80)
    photo_filter = kit.choice(
        raw.get("filter"), "the photo filter", FILTERS, default=""
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
    # a child's or a teen's post with a flagged term or contact details waits for a moderator
    verdict = screen(sc, f"{text}\n{place}", involves_young=young(me_))
    clean = filtered(sc, text)
    tags = sorted({t.lower() for t in TAG.findall(clean)})
    sc.execute(
        """INSERT INTO so_posts (id, author_id, audience, group_id, page_id, text, photos, shared_post_id, tags, created_at,
        updated_at, feeling, place, filter, alts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post_id,
            me_["id"],
            audience,
            group_id or None,
            page_id or None,
            clean,
            store.dumps(pictures),
            shared or None,
            " ".join(tags),
            moment,
            moment,
            feeling,
            filtered(sc, place),
            photo_filter if pictures else "",
            store.dumps(_alts(raw.get("alts"), len(pictures))),
        ),
    )
    if poll:
        sc.execute(
            "INSERT INTO so_polls (post_id, options, closes_at) VALUES (?, ?, ?)",
            (
                post_id,
                store.dumps([filtered(sc, item) for item in poll["options"]]),
                store.later(24 * poll["days"]),
            ),
        )
    for tag in tags:
        sc.execute(
            "INSERT OR IGNORE INTO so_post_tags (tag, post_id, created_at) VALUES (?, ?, ?)",
            (tag, post_id, moment),
        )
    if verdict.action == "hold":
        sc.execute("UPDATE so_posts SET hidden_at = ? WHERE id = ?", (moment, post_id))
        _hold(sc, "post", post_id, me_, verdict, young_ids=[me_["id"]])
        sc.commit()
        return {
            **post_view(sc, me_, _post_row(sc, post_id)),
            "held": True,
            "notice": "Your post is waiting for a safety check before others can see it.",
        }
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
    if "feeling" in raw:
        values["feeling"] = kit.choice(
            raw["feeling"], "the feeling", tuple(FEELINGS), default=""
        )
    if "place" in raw:
        values["place"] = filtered(sc, kit.text(raw["place"], "the place", max_len=80))
    if "filter" in raw:
        values["filter"] = kit.choice(
            raw["filter"], "the photo filter", FILTERS, default=""
        )
    if "alts" in raw:
        values["alts"] = store.dumps(
            _alts(raw["alts"], len(store.loads(post["photos"], [])))
        )
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
        "UPDATE so_posts SET deleted_at = ?, updated_at = ?, pinned_at = NULL WHERE id = ?",
        (moment, moment, post["id"]),
    )
    sc.commit()


def pin_post(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, on: bool
) -> dict[str, Any]:
    """Keep up to three of your own posts at the top of your profile."""
    post = _post_row(sc, post_id)
    if post["author_id"] != me_["id"] or post["deleted_at"] or post["hidden_at"]:
        raise LookupError("That post was not found.")
    if post["group_id"] or post["page_id"]:
        raise ValueError("Only posts on your own profile can be pinned.")
    moment = stamp()
    if on and not post["pinned_at"]:
        pinned = kit.count(
            sc,
            "SELECT COUNT(*) FROM so_posts WHERE author_id = ? AND pinned_at IS NOT NULL AND deleted_at IS NULL",
            (me_["id"],),
        )
        if pinned >= MAX_PINNED:
            raise ValueError(f"You can pin up to {MAX_PINNED} posts. Unpin one first.")
    sc.execute(
        "UPDATE so_posts SET pinned_at = ?, updated_at = ? WHERE id = ?",
        (moment if on else None, moment, post["id"]),
    )
    sc.commit()
    return post_view(sc, me_, _post_row(sc, post["id"]))


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


# ---------------------------------------------------------------------------
# polls
# ---------------------------------------------------------------------------


def _poll_row(sc: sqlite3.Connection, post_id: str) -> dict[str, Any] | None:
    return row(sc, "SELECT * FROM so_polls WHERE post_id = ?", (post_id,))


def poll_view(
    sc: sqlite3.Connection, viewer: dict[str, Any], post: dict[str, Any]
) -> dict[str, Any] | None:
    found = _poll_row(sc, post["id"])
    if not found:
        return None
    options = store.loads(found["options"], [])
    counts = [0] * len(options)
    mine = None
    for vote in rows(
        sc,
        "SELECT account_id, choice FROM so_poll_votes WHERE post_id = ?",
        (post["id"],),
    ):
        if 0 <= vote["choice"] < len(counts):
            counts[vote["choice"]] += 1
        if vote["account_id"] == viewer["id"]:
            mine = vote["choice"]
    closed = found["closes_at"] <= stamp()
    # the results show once you voted, when the poll is over, and always to its author
    shown = closed or mine is not None or post["author_id"] == viewer["id"]
    total = sum(counts)
    return {
        "options": [
            {
                "text": text,
                "votes": counts[index] if shown else None,
                "percent": round(100 * counts[index] / total)
                if shown and total
                else (0 if shown else None),
            }
            for index, text in enumerate(options)
        ],
        "total": total,
        "my_vote": mine,
        "closed": closed,
        "closes_at": found["closes_at"],
        "results": shown,
    }


def vote(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, choice: Any
) -> dict[str, Any]:
    """One vote per person, kept once given."""
    _active(me_)
    post = visible_post(sc, me_, post_id)
    found = _poll_row(sc, post["id"])
    if not found:
        raise LookupError("That poll was not found.")
    if found["closes_at"] <= stamp():
        raise PermissionError("This poll is over.")
    options = store.loads(found["options"], [])
    try:
        index = int(choice)
    except (TypeError, ValueError):
        raise ValueError("Choose one of the answers.") from None
    if not 0 <= index < len(options):
        raise ValueError("Choose one of the answers.")
    if row(
        sc,
        "SELECT 1 AS x FROM so_poll_votes WHERE post_id = ? AND account_id = ?",
        (post["id"], me_["id"]),
    ):
        raise ValueError("You already voted in this poll.")
    moment = stamp()
    sc.execute(
        "INSERT INTO so_poll_votes (post_id, account_id, choice, created_at) VALUES (?, ?, ?, ?)",
        (post["id"], me_["id"], index, moment),
    )
    sc.execute("UPDATE so_posts SET updated_at = ? WHERE id = ?", (moment, post["id"]))
    notify(
        sc,
        post["author_id"],
        "poll_vote",
        me_["id"],
        "post",
        post["id"],
        f"{me_['display_name']} voted in your poll.",
    )
    sc.commit()
    return post_view(sc, me_, _post_row(sc, post["id"]))


def finish_polls(sc: sqlite3.Connection) -> int:
    """Polls that are over tell their author and everyone who voted which answer won (once)."""
    done = 0
    for found in rows(
        sc,
        "SELECT * FROM so_polls WHERE closes_at <= ? AND notified_at IS NULL LIMIT 50",
        (stamp(),),
    ):
        post = row(sc, "SELECT * FROM so_posts WHERE id = ?", (found["post_id"],))
        sc.execute(
            "UPDATE so_polls SET notified_at = ? WHERE post_id = ?",
            (stamp(), found["post_id"]),
        )
        done += 1
        if not post or post["deleted_at"] or post["hidden_at"]:
            continue
        options = store.loads(found["options"], [])
        counts = [0] * len(options)
        voters = []
        for item in rows(
            sc,
            "SELECT account_id, choice FROM so_poll_votes WHERE post_id = ?",
            (post["id"],),
        ):
            if 0 <= item["choice"] < len(counts):
                counts[item["choice"]] += 1
            voters.append(item["account_id"])
        total = sum(counts)
        if total:
            best = max(range(len(counts)), key=lambda i: counts[i])
            text = f"The poll is over: “{options[best]}” got the most votes ({counts[best]} of {total})."
        else:
            text = "The poll is over. Nobody voted this time."
        for person in dict.fromkeys([post["author_id"], *voters]):
            sc.execute(
                """INSERT INTO so_notifications (id, account_id, kind, actor_id, ref_type, ref_id, text, created_at)
                VALUES (?, ?, 'poll_result', '', 'post', ?, ?, ?)""",
                (new_id(), person, post["id"], text[:300], stamp()),
            )
    if done:
        sc.commit()
    return done


# ---------------------------------------------------------------------------
# comments: one level of replies, and likes
# ---------------------------------------------------------------------------


def comment(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    post = visible_post(sc, me_, post_id)
    written = kit.text(
        raw.get("text"), "a comment", max_len=1000, required=True, multiline=True
    )
    post_author = row(
        sc, "SELECT * FROM so_accounts WHERE id = ?", (post["author_id"],)
    )
    young_ids = [a["id"] for a in (me_, post_author) if a and young(a)]
    verdict = screen(sc, written, involves_young=bool(young_ids))
    text = verdict.text
    parent_id = str(raw.get("parent_id") or "")
    parent = None
    if parent_id:
        parent = row(
            sc,
            "SELECT * FROM so_comments WHERE id = ? AND post_id = ?",
            (store.check_id(parent_id, "comment"), post["id"]),
        )
        if (
            not parent
            or parent["deleted_at"]
            or parent["hidden_at"]
            or blocked(sc, me_["id"], parent["author_id"])
        ):
            raise LookupError("That comment was not found.")
        if parent["parent_id"]:
            parent_id = parent[
                "parent_id"
            ]  # a reply to a reply joins the same thread (one level)
    rate(sc, me_, "comment")
    comment_id = new_id()
    moment = stamp()
    sc.execute(
        "INSERT INTO so_comments (id, post_id, author_id, text, created_at, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, post["id"], me_["id"], text, moment, parent_id),
    )
    if verdict.action == "hold":
        sc.execute(
            "UPDATE so_comments SET hidden_at = ? WHERE id = ?", (moment, comment_id)
        )
        _hold(sc, "comment", comment_id, me_, verdict, young_ids=young_ids)
        sc.commit()
        return {
            "comment": {
                "id": comment_id,
                "author": mini(me_),
                "text": text,
                "created_at": moment,
                "mine": True,
                "parent_id": parent_id,
                "likes": 0,
                "liked": False,
                "held": True,
            },
            "held": True,
            "notice": "Your comment is waiting for a safety check before others can see it.",
        }
    sc.execute("UPDATE so_posts SET updated_at = ? WHERE id = ?", (moment, post["id"]))
    if parent:
        notify(
            sc,
            parent["author_id"],
            "reply",
            me_["id"],
            "post",
            post["id"],
            f"{me_['display_name']} replied to your comment.",
        )
    if not parent or parent["author_id"] != post["author_id"]:
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
            "parent_id": parent_id,
            "likes": 0,
            "liked": False,
        }
    }


def comments(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str
) -> list[dict[str, Any]]:
    """Oldest first; a reply carries its ``parent_id`` (replies to a removed comment go with it)."""
    post = visible_post(sc, me_, post_id)
    hidden = blocked_ids(sc, me_["id"])
    out = []
    shown: set[str] = set()
    liked = {
        r["comment_id"]
        for r in rows(
            sc,
            """SELECT l.comment_id FROM so_comment_likes l JOIN so_comments c ON c.id = l.comment_id
            WHERE c.post_id = ? AND l.account_id = ?""",
            (post["id"], me_["id"]),
        )
    }
    for item in rows(
        sc,
        "SELECT * FROM so_comments WHERE post_id = ? AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at",
        (post["id"],),
    ):
        if item["author_id"] in hidden:
            continue
        if item["parent_id"] and item["parent_id"] not in shown:
            continue
        try:
            author = account(sc, item["author_id"])
        except LookupError:
            continue
        if author.get("suspended_at"):
            continue
        shown.add(item["id"])
        out.append(
            {
                "id": item["id"],
                "author": mini(author),
                "text": item["text"],
                "created_at": item["created_at"],
                "mine": item["author_id"] == me_["id"],
                "can_delete": item["author_id"] == me_["id"]
                or post["author_id"] == me_["id"],
                "parent_id": item["parent_id"] or "",
                "likes": kit.count(
                    sc,
                    "SELECT COUNT(*) FROM so_comment_likes WHERE comment_id = ?",
                    (item["id"],),
                ),
                "liked": item["id"] in liked,
            }
        )
    return out


def like_comment(
    sc: sqlite3.Connection, me_: dict[str, Any], comment_id: str, on: bool
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_comments WHERE id = ?",
        (store.check_id(comment_id, "comment"),),
    )
    if (
        not found
        or found["deleted_at"]
        or found["hidden_at"]
        or blocked(sc, me_["id"], found["author_id"])
    ):
        raise LookupError("That comment was not found.")
    post = visible_post(sc, me_, found["post_id"])
    had = bool(
        row(
            sc,
            "SELECT 1 AS x FROM so_comment_likes WHERE comment_id = ? AND account_id = ?",
            (found["id"], me_["id"]),
        )
    )
    if on and not had:
        sc.execute(
            "INSERT INTO so_comment_likes (comment_id, account_id, created_at) VALUES (?, ?, ?)",
            (found["id"], me_["id"], stamp()),
        )
        notify(
            sc,
            found["author_id"],
            "comment_like",
            me_["id"],
            "post",
            post["id"],
            f"{me_['display_name']} liked your comment.",
        )
    elif not on:
        sc.execute(
            "DELETE FROM so_comment_likes WHERE comment_id = ? AND account_id = ?",
            (found["id"], me_["id"]),
        )
    sc.execute("UPDATE so_posts SET updated_at = ? WHERE id = ?", (stamp(), post["id"]))
    sc.commit()
    return {
        "liked": on,
        "likes": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_comment_likes WHERE comment_id = ?",
            (found["id"],),
        ),
    }


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


# ---------------------------------------------------------------------------
# a post as the browser sees it
# ---------------------------------------------------------------------------


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
    saved = row(
        sc,
        "SELECT collection_id FROM so_saves WHERE account_id = ? AND post_id = ?",
        (viewer["id"], post["id"]),
    )
    pictures = store.loads(post["photos"], [])
    view: dict[str, Any] = {
        "id": post["id"],
        "author": mini(author),
        "audience": post["audience"],
        "text": post["text"],
        "photos": photos.display_list(pictures, sc=sc),
        "alts": _alts(store.loads(post.get("alts"), []), len(pictures)),
        "filter": post.get("filter") or "",
        "feeling": post.get("feeling") or "",
        "place": post.get("place") or "",
        "tags": [t for t in post["tags"].split() if t],
        "created_at": post["created_at"],
        "updated_at": post["updated_at"],
        "edited": bool(post.get("edited_at")),
        "pinned": bool(post.get("pinned_at")),
        "reactions": reactions,
        "my_reaction": mine,
        "comments": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_comments WHERE post_id = ? AND deleted_at IS NULL AND hidden_at IS NULL",
            (post["id"],),
        ),
        "mine": post["author_id"] == viewer["id"],
        "can_share": post["audience"] in ("public", "page"),
        "saved": saved is not None,
        "collection_id": saved["collection_id"] if saved else "",
    }
    poll = poll_view(sc, viewer, post)
    if poll:
        view["poll"] = poll
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
    """Their posts on their own profile, pinned ones first."""
    out = []
    for item in rows(
        sc,
        """SELECT * FROM so_posts WHERE author_id = ? AND group_id IS NULL AND page_id IS NULL AND deleted_at IS NULL
        AND hidden_at IS NULL ORDER BY (pinned_at IS NULL), pinned_at DESC, created_at DESC LIMIT ?""",
        (target["id"], limit * 2),
    ):
        if can_see_post(sc, me_, item):
            out.append(post_view(sc, me_, item))
        if len(out) >= limit:
            break
    return out


def _tile(
    sc: sqlite3.Connection, me_: dict[str, Any], post: dict[str, Any]
) -> dict[str, Any]:
    """A square in a photo grid (Explore, a profile's Photos and Tagged): the first photo or the first words."""
    pictures = store.loads(post["photos"], [])
    return {
        "id": post["id"],
        "photo": photos.display(pictures[0], sc=sc) if pictures else "",
        "alt": (_alts(store.loads(post.get("alts"), []), len(pictures)) or [""])[0]
        if pictures
        else "",
        "photos": len(pictures),
        "filter": post.get("filter") or "",
        "text": post["text"][:140],
        "author": mini(account(sc, post["author_id"])),
        "reactions": kit.count(
            sc, "SELECT COUNT(*) FROM so_reactions WHERE post_id = ?", (post["id"],)
        ),
        "comments": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_comments WHERE post_id = ? AND deleted_at IS NULL AND hidden_at IS NULL",
            (post["id"],),
        ),
        "poll": bool(_poll_row(sc, post["id"])),
        "created_at": post["created_at"],
    }


def person_grid(
    sc: sqlite3.Connection, me_: dict[str, Any], target: dict[str, Any], kind: str
) -> list[dict[str, Any]]:
    """``photos``: their posts with photos; ``tagged``: posts by others that mention them, both as grid squares."""
    kind = kit.choice(kind, "what to show", ("photos", "tagged"))
    if kind == "photos":
        found = rows(
            sc,
            """SELECT * FROM so_posts WHERE author_id = ? AND group_id IS NULL AND page_id IS NULL AND photos != '[]'
            AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 200""",
            (target["id"],),
        )
    else:
        found = rows(
            sc,
            """SELECT * FROM so_posts WHERE author_id != ? AND lower(text) LIKE ? AND deleted_at IS NULL
            AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 200""",
            (target["id"], f"%@{target['handle']}%"),
        )
    out = []
    for item in found:
        if kind == "tagged" and target["handle"] not in MENTION.findall(
            item["text"].lower()
        ):
            continue
        if can_see_post(sc, me_, item):
            out.append(_tile(sc, me_, item))
        if len(out) >= 60:
            break
    return out


def memories(
    sc: sqlite3.Connection, me_: dict[str, Any], day: str = ""
) -> list[dict[str, Any]]:
    """On this day: your own posts from earlier years on today's date (``day`` is MM-DD in your time zone)."""
    today = store.utcnow()
    wanted = str(day or "").strip() or today.strftime("%m-%d")
    if not re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", wanted):
        raise ValueError("The day must be like 10-29 (month and day).")
    out = []
    for item in rows(
        sc,
        """SELECT * FROM so_posts WHERE author_id = ? AND substr(created_at, 6, 5) = ? AND substr(created_at, 1, 4) < ?
        AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 20""",
        (me_["id"], wanted, today.strftime("%Y")),
    ):
        if can_see_post(sc, me_, item):
            view = post_view(sc, me_, item)
            view["years_ago"] = int(today.strftime("%Y")) - int(item["created_at"][:4])
            out.append(view)
    return out


# ---------------------------------------------------------------------------
# saved posts and collections
# ---------------------------------------------------------------------------


def _collection(
    sc: sqlite3.Connection, me_: dict[str, Any], collection_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_collections WHERE id = ? AND owner_id = ?",
        (store.check_id(collection_id, "collection"), me_["id"]),
    )
    if not found:
        raise LookupError("That collection was not found.")
    return found


def collections(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    """Your collections (and "All saved"), each with how many posts and the first photo as its cover."""

    def cover(where: str, params: tuple[Any, ...]) -> str:
        for item in rows(
            sc,
            f"""SELECT p.* FROM so_saves s JOIN so_posts p ON p.id = s.post_id WHERE s.account_id = ? {where}
            AND p.photos != '[]' ORDER BY s.created_at DESC LIMIT 10""",  # noqa: S608 - fixed clauses
            (me_["id"], *params),
        ):
            if can_see_post(sc, me_, item):
                return photos.display(store.loads(item["photos"], [""])[0], sc=sc)
        return ""

    out = [
        {
            "id": "",
            "name": "All saved",
            "count": kit.count(
                sc, "SELECT COUNT(*) FROM so_saves WHERE account_id = ?", (me_["id"],)
            ),
            "cover": cover("", ()),
        }
    ]
    for item in rows(
        sc,
        "SELECT * FROM so_collections WHERE owner_id = ? ORDER BY name",
        (me_["id"],),
    ):
        out.append(
            {
                "id": item["id"],
                "name": item["name"],
                "count": kit.count(
                    sc,
                    "SELECT COUNT(*) FROM so_saves WHERE account_id = ? AND collection_id = ?",
                    (me_["id"], item["id"]),
                ),
                "cover": cover("AND s.collection_id = ?", (item["id"],)),
            }
        )
    return out


def create_collection(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    name = kit.text(
        raw.get("name"), "a name for the collection", max_len=60, required=True
    )
    if name.lower() == "all saved" or row(
        sc,
        "SELECT 1 AS x FROM so_collections WHERE owner_id = ? AND lower(name) = ?",
        (me_["id"], name.lower()),
    ):
        raise ValueError(f"You already have a collection called {name}.")
    if (
        kit.count(
            sc, "SELECT COUNT(*) FROM so_collections WHERE owner_id = ?", (me_["id"],)
        )
        >= 100
    ):
        raise ValueError("You have 100 collections: delete one first.")
    collection_id = new_id()
    sc.execute(
        "INSERT INTO so_collections (id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
        (collection_id, me_["id"], name, stamp()),
    )
    sc.commit()
    return {"id": collection_id, "name": name, "count": 0, "cover": ""}


def rename_collection(
    sc: sqlite3.Connection, me_: dict[str, Any], collection_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    found = _collection(sc, me_, collection_id)
    name = kit.text(
        raw.get("name"), "a name for the collection", max_len=60, required=True
    )
    if row(
        sc,
        "SELECT 1 AS x FROM so_collections WHERE owner_id = ? AND lower(name) = ? AND id != ?",
        (me_["id"], name.lower(), found["id"]),
    ):
        raise ValueError(f"You already have a collection called {name}.")
    sc.execute("UPDATE so_collections SET name = ? WHERE id = ?", (name, found["id"]))
    sc.commit()
    return {"id": found["id"], "name": name}


def delete_collection(
    sc: sqlite3.Connection, me_: dict[str, Any], collection_id: str
) -> None:
    """The collection goes; its posts stay saved under All saved."""
    found = _collection(sc, me_, collection_id)
    sc.execute(
        "UPDATE so_saves SET collection_id = '' WHERE account_id = ? AND collection_id = ?",
        (me_["id"], found["id"]),
    )
    sc.execute("DELETE FROM so_collections WHERE id = ?", (found["id"],))
    sc.commit()


def save_post(
    sc: sqlite3.Connection, me_: dict[str, Any], post_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Save a post you can see (into a collection, or All saved); ``{"saved": false}`` takes it out."""
    post = visible_post(sc, me_, post_id)
    if raw.get("saved") is False or str(raw.get("saved")).lower() == "false":
        sc.execute(
            "DELETE FROM so_saves WHERE account_id = ? AND post_id = ?",
            (me_["id"], post["id"]),
        )
        sc.commit()
        return {"saved": False, "collection_id": ""}
    collection_id = str(raw.get("collection_id") or "")
    if collection_id:
        collection_id = _collection(sc, me_, collection_id)["id"]
    sc.execute(
        """INSERT INTO so_saves (account_id, post_id, collection_id, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(account_id, post_id) DO UPDATE SET collection_id = excluded.collection_id""",
        (me_["id"], post["id"], collection_id, stamp()),
    )
    sc.commit()
    return {"saved": True, "collection_id": collection_id}


def saved_posts(
    sc: sqlite3.Connection, me_: dict[str, Any], collection_id: str = ""
) -> list[dict[str, Any]]:
    """Saved posts you can still see, newest saved first (a post that was removed or hidden drops out)."""
    if collection_id:
        _collection(sc, me_, collection_id)
        found = rows(
            sc,
            """SELECT p.* FROM so_saves s JOIN so_posts p ON p.id = s.post_id WHERE s.account_id = ? AND s.collection_id = ?
            ORDER BY s.created_at DESC LIMIT 200""",
            (me_["id"], collection_id),
        )
    else:
        found = rows(
            sc,
            "SELECT p.* FROM so_saves s JOIN so_posts p ON p.id = s.post_id WHERE s.account_id = ? ORDER BY s.created_at DESC LIMIT 200",
            (me_["id"],),
        )
    return [post_view(sc, me_, item) for item in found if can_see_post(sc, me_, item)]


# ---------------------------------------------------------------------------
# Explore: what is popular now, people to follow, #tags, and search across it all
# ---------------------------------------------------------------------------

TRENDING_DAYS = 14


def _explorable(
    sc: sqlite3.Connection, me_: dict[str, Any], post: dict[str, Any]
) -> bool:
    """Public posts you may see; a child sees only posts from friends and their own household."""
    if not can_see_post(sc, me_, post):
        return False
    if minor(me_) and post["author_id"] != me_["id"]:
        author = (
            row(sc, "SELECT home FROM so_accounts WHERE id = ?", (post["author_id"],))
            or {}
        )
        return are_friends(sc, me_["id"], post["author_id"]) or (
            bool(me_.get("home")) and author.get("home") == me_["home"]
        )
    return True


def _public_candidates(
    sc: sqlite3.Connection, limit: int = 400
) -> list[dict[str, Any]]:
    return rows(
        sc,
        """SELECT p.*, (SELECT COUNT(*) FROM so_reactions r WHERE r.post_id = p.id)
              + 2 * (SELECT COUNT(*) FROM so_comments c WHERE c.post_id = p.id AND c.deleted_at IS NULL AND c.hidden_at IS NULL)
              + 3 * (SELECT COUNT(*) FROM so_posts s WHERE s.shared_post_id = p.id AND s.deleted_at IS NULL)
              + (SELECT COUNT(*) FROM so_poll_votes v WHERE v.post_id = p.id) AS score
        FROM so_posts p LEFT JOIN so_groups g ON g.id = p.group_id
        WHERE p.deleted_at IS NULL AND p.hidden_at IS NULL AND p.created_at > ?
        AND (p.audience IN ('public', 'page') OR (p.audience = 'group' AND g.visibility = 'public'))
        ORDER BY score DESC, p.created_at DESC LIMIT ?""",
        (store.later(-24 * TRENDING_DAYS), limit),
    )


def explore(sc: sqlite3.Connection, me_: dict[str, Any]) -> dict[str, Any]:
    trending = []
    tags: dict[str, int] = {}
    for item in _public_candidates(sc):
        if not _explorable(sc, me_, item):
            continue
        for tag in item["tags"].split():
            tags[tag] = tags.get(tag, 0) + 1
        if len(trending) < 30:
            trending.append(_tile(sc, me_, item))
    people = suggestions(sc, me_, limit=12)
    if me_["age_group"] not in ("child", "teen"):
        known = (
            {p["id"] for p in people}
            | friend_ids(sc, me_["id"])
            | blocked_ids(sc, me_["id"])
            | {me_["id"]}
        )
        followed = {
            r["target_id"]
            for r in rows(
                sc,
                "SELECT target_id FROM so_follows WHERE follower_id = ? AND target_type = 'account'",
                (me_["id"],),
            )
        }
        for item in rows(
            sc,
            """SELECT a.*, (SELECT COUNT(*) FROM so_follows f WHERE f.target_type = 'account' AND f.target_id = a.id) AS fans
            FROM so_accounts a WHERE a.visibility = 'public' AND a.suspended_at IS NULL AND a.age_group IN ('adult', 'senior')
            ORDER BY fans DESC, a.created_at DESC LIMIT 60""",
        ):
            if len(people) >= 12:
                break
            if item["id"] in known or item["id"] in followed:
                continue
            candidate = _account(item) or {}
            people.append(
                {**mini(candidate), "followers": item["fans"], "public": True}
            )
    for person in people:
        person.setdefault("relation", "none")
    popular = sorted(tags.items(), key=lambda pair: (-pair[1], pair[0]))[:16]
    return {
        "trending": trending,
        "people": people,
        "tags": [{"tag": tag, "posts": n} for tag, n in popular],
    }


def explore_search(
    sc: sqlite3.Connection, me_: dict[str, Any], q: str
) -> dict[str, Any]:
    """People, posts, groups, pages and #tags matching the words, each only what this person may see."""
    query = kit.text(q, "a search", max_len=60)
    empty = {"people": [], "groups": [], "pages": [], "posts": [], "tags": []}
    if len(query.lstrip("#@")) < 2:
        return empty
    if query.startswith("#"):
        word = query[1:].lower()
        found = {
            r["tag"]: r["n"]
            for r in rows(
                sc,
                "SELECT tag, COUNT(*) AS n FROM so_post_tags WHERE tag LIKE ? GROUP BY tag ORDER BY n DESC LIMIT 20",
                (f"{word}%",),
            )
        }
        return {
            **empty,
            "tags": [{"tag": t, "posts": n} for t, n in found.items()],
            "posts": [
                _tile(sc, me_, p)
                for p in [
                    post
                    for post in rows(
                        sc,
                        "SELECT p.* FROM so_post_tags t JOIN so_posts p ON p.id = t.post_id WHERE t.tag = ? ORDER BY p.created_at DESC LIMIT 100",
                        (word,),
                    )
                    if _explorable(sc, me_, post)
                ][:30]
            ],
        }
    base = search(sc, me_, query)
    words = query.lstrip("@").lower()
    posts = []
    for item in rows(
        sc,
        """SELECT * FROM so_posts WHERE deleted_at IS NULL AND hidden_at IS NULL AND (lower(text) LIKE ? OR lower(place) LIKE ?)
        ORDER BY created_at DESC LIMIT 300""",
        (f"%{words}%", f"%{words}%"),
    ):
        if _explorable(sc, me_, item):
            posts.append(_tile(sc, me_, item))
        if len(posts) >= 30:
            break
    tags = [
        {"tag": r["tag"], "posts": r["n"]}
        for r in rows(
            sc,
            "SELECT tag, COUNT(*) AS n FROM so_post_tags WHERE tag LIKE ? GROUP BY tag ORDER BY n DESC LIMIT 10",
            (f"{words.replace(' ', '')}%",),
        )
    ]
    return {**base, "posts": posts, "tags": tags}


# ---------------------------------------------------------------------------
# stories (24 hours), their replies and reactions, and highlights that keep them
# ---------------------------------------------------------------------------

STORY_AUDIENCES = ("friends", "close_friends")


def create_story(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    text = filtered(
        sc, kit.text(raw.get("text"), "the story", max_len=200, multiline=True)
    )
    pictures = _photos(
        [raw["photo"]] if raw.get("photo") else [], limit=1, sc=sc, owner=me_["id"]
    )
    if not text and not pictures:
        raise ValueError("Write something or add a photo.")
    audience = kit.choice(
        raw.get("audience"), "who can see it", STORY_AUDIENCES, default="friends"
    )
    rate(sc, me_, "story")
    story_id = new_id()
    sc.execute(
        "INSERT INTO so_stories (id, author_id, text, photo, color, created_at, expires_at, audience) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            story_id,
            me_["id"],
            text,
            pictures[0] if pictures else "",
            kit.color(raw.get("color")),
            stamp(),
            store.later(STORY_HOURS),
            audience,
        ),
    )
    sc.commit()
    return {"id": story_id}


def can_see_story(
    sc: sqlite3.Connection, viewer: dict[str, Any], story: dict[str, Any]
) -> bool:
    """A story is for the author's friends (or only their close friends); never after it was removed."""
    if story.get("deleted_at") or story.get("hidden_at"):
        return False
    if story["author_id"] == viewer["id"]:
        return True
    if blocked(sc, viewer["id"], story["author_id"]):
        return False
    try:
        author = account(sc, story["author_id"])
    except LookupError:
        return False
    if author.get("suspended_at"):
        return False
    if (story.get("audience") or "friends") == "close_friends":
        return in_close_circle(sc, viewer["id"], story["author_id"])
    return are_friends(sc, viewer["id"], story["author_id"])


def _story_view(
    sc: sqlite3.Connection, me_: dict[str, Any], item: dict[str, Any], seen: bool
) -> dict[str, Any]:
    own = item["author_id"] == me_["id"]
    reaction = row(
        sc,
        "SELECT emoji FROM so_story_reactions WHERE story_id = ? AND account_id = ?",
        (item["id"], me_["id"]),
    )
    return {
        "id": item["id"],
        "text": item["text"],
        "photo": photos.display(item["photo"], sc=sc),
        "color": item["color"],
        "audience": item.get("audience") or "friends",
        "created_at": item["created_at"],
        "expires_at": item["expires_at"],
        "seen": seen,
        "views": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_story_views WHERE story_id = ?",
            (item["id"],),
        )
        if own
        else None,
        "reactions": kit.count(
            sc,
            "SELECT COUNT(*) FROM so_story_reactions WHERE story_id = ?",
            (item["id"],),
        )
        if own
        else None,
        "my_reaction": reaction["emoji"] if reaction else None,
    }


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
        if author.get("suspended_at") or not can_see_story(sc, me_, item):
            continue
        entry = grouped.setdefault(
            author["id"],
            {
                "author": mini(author),
                "stories": [],
                "unseen": 0,
                "mine": author["id"] == me_["id"],
                "close": False,
            },
        )
        is_seen = item["id"] in seen or author["id"] == me_["id"]
        entry["stories"].append(_story_view(sc, me_, item, is_seen))
        if (item.get("audience") or "friends") == "close_friends":
            entry["close"] = True
        if not is_seen:
            entry["unseen"] += 1
    return sorted(grouped.values(), key=lambda e: (not e["mine"], e["unseen"] == 0))


def _story_row(sc: sqlite3.Connection, story_id: str) -> dict[str, Any] | None:
    return row(
        sc,
        "SELECT * FROM so_stories WHERE id = ?",
        (store.check_id(story_id, "story"),),
    )


def visible_story(
    sc: sqlite3.Connection, me_: dict[str, Any], story_id: str, *, live: bool = True
) -> dict[str, Any]:
    """A story this person may see; ``live`` also asks that its day is not over (a highlight keeps it)."""
    found = _story_row(sc, story_id)
    if (
        not found
        or not can_see_story(sc, me_, found)
        or (
            live
            and found["expires_at"] <= stamp()
            and not _in_highlight(sc, found["id"])
        )
    ):
        raise LookupError("That story is no longer available.")
    return found


def _in_highlight(sc: sqlite3.Connection, story_id: str) -> bool:
    return bool(
        row(sc, "SELECT 1 AS x FROM so_highlight_items WHERE story_id = ?", (story_id,))
    )


def see_story(sc: sqlite3.Connection, me_: dict[str, Any], story_id: str) -> None:
    found = visible_story(sc, me_, story_id)
    sc.execute(
        "INSERT OR IGNORE INTO so_story_views (story_id, viewer_id, at) VALUES (?, ?, ?)",
        (found["id"], me_["id"], stamp()),
    )
    sc.commit()


def story_viewers(
    sc: sqlite3.Connection, me_: dict[str, Any], story_id: str
) -> list[dict[str, Any]]:
    """Who saw your story, and how they reacted (only for its author)."""
    found = _story_row(sc, story_id)
    if not found or found["author_id"] != me_["id"] or found["deleted_at"]:
        raise LookupError("That story was not found.")
    hidden = blocked_ids(sc, me_["id"])
    out = []
    for item in rows(
        sc,
        """SELECT v.viewer_id, v.at, r.emoji FROM so_story_views v LEFT JOIN so_story_reactions r
        ON r.story_id = v.story_id AND r.account_id = v.viewer_id WHERE v.story_id = ? ORDER BY v.at DESC""",
        (found["id"],),
    ):
        if item["viewer_id"] in hidden:
            continue
        try:
            other = account(sc, item["viewer_id"])
        except LookupError:
            continue
        out.append({**mini(other), "at": item["at"], "reaction": item["emoji"]})
    return out


def react_story(
    sc: sqlite3.Connection, me_: dict[str, Any], story_id: str, emoji: Any
) -> dict[str, Any]:
    found = visible_story(sc, me_, story_id)
    if found["author_id"] == me_["id"]:
        raise ValueError("React to your friends' stories.")
    if emoji in (None, ""):
        sc.execute(
            "DELETE FROM so_story_reactions WHERE story_id = ? AND account_id = ?",
            (found["id"], me_["id"]),
        )
        sc.commit()
        return {"my_reaction": None}
    if emoji not in STORY_REACTIONS:
        raise ValueError("Choose one of the reactions.")
    had = row(
        sc,
        "SELECT emoji FROM so_story_reactions WHERE story_id = ? AND account_id = ?",
        (found["id"], me_["id"]),
    )
    sc.execute(
        """INSERT INTO so_story_reactions (story_id, account_id, emoji, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(story_id, account_id) DO UPDATE SET emoji = excluded.emoji""",
        (found["id"], me_["id"], emoji, stamp()),
    )
    if not had:
        notify(
            sc,
            found["author_id"],
            "story_reaction",
            me_["id"],
            "story",
            found["id"],
            f"{me_['display_name']} reacted {emoji} to your story.",
        )
    sc.commit()
    return {"my_reaction": emoji}


def reply_story(
    sc: sqlite3.Connection, me_: dict[str, Any], story_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """A reply to a story is a private message to its author (the usual rules for who may message whom apply)."""
    _active(me_)
    found = visible_story(sc, me_, story_id)
    if found["author_id"] == me_["id"]:
        raise ValueError("Reply to your friends' stories.")
    text = kit.text(
        raw.get("text"), "your reply", max_len=1000, required=True, multiline=True
    )
    author = account(sc, found["author_id"])
    if not can_message(sc, me_, author):
        raise PermissionError("You can reply to your friends' stories.")
    rate(sc, me_, "story_reply")
    chat = open_direct(sc, me_, author["id"])
    about = found["text"][:60] or "📷"
    sent = send(sc, me_, chat["id"], {"text": f"↪ Story: “{about}”\n{text}"})
    notify(
        sc,
        author["id"],
        "story_reply",
        me_["id"],
        "chat",
        chat["id"],
        f"{me_['display_name']} replied to your story.",
    )
    sc.commit()
    return {"chat": chat, "message": sent["message"]}


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
    sc.execute("DELETE FROM so_highlight_items WHERE story_id = ?", (found["id"],))
    sc.commit()


def my_stories(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    """Your stories of the last year (also the ones whose day is over), to choose for a highlight."""
    return [
        _story_view(sc, me_, item, True)
        for item in rows(
            sc,
            """SELECT * FROM so_stories WHERE author_id = ? AND deleted_at IS NULL AND hidden_at IS NULL AND created_at > ?
            ORDER BY created_at DESC LIMIT 200""",
            (me_["id"], store.later(-24 * 366)),
        )
    ]


def _highlight(
    sc: sqlite3.Connection, me_: dict[str, Any], highlight_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_highlights WHERE id = ? AND owner_id = ?",
        (store.check_id(highlight_id, "highlight"), me_["id"]),
    )
    if not found:
        raise LookupError("That highlight was not found.")
    return found


def _highlight_stories(
    sc: sqlite3.Connection, me_: dict[str, Any], story_ids: Any
) -> list[str]:
    if not isinstance(story_ids, list) or not story_ids:
        raise ValueError("Choose at least one of your stories.")
    if len(story_ids) > 100:
        raise ValueError("A highlight keeps up to 100 stories.")
    out = []
    for story_id in dict.fromkeys(str(s) for s in story_ids):
        found = _story_row(sc, story_id)
        if (
            not found
            or found["author_id"] != me_["id"]
            or found["deleted_at"]
            or found["hidden_at"]
        ):
            raise LookupError("That story was not found.")
        out.append(found["id"])
    return out


def save_highlight(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    raw: dict[str, Any],
    highlight_id: str = "",
) -> dict[str, Any]:
    """Make a highlight (a title and some of your stories) or change one; its stories stay after their day."""
    _active(me_)
    title = kit.text(
        raw.get("title"), "a title for the highlight", max_len=30, required=True
    )
    story_ids = _highlight_stories(sc, me_, raw.get("story_ids"))
    moment = stamp()
    if highlight_id:
        found = _highlight(sc, me_, highlight_id)
        sc.execute(
            "UPDATE so_highlights SET title = ? WHERE id = ?", (title, found["id"])
        )
        sc.execute(
            "DELETE FROM so_highlight_items WHERE highlight_id = ?", (found["id"],)
        )
        highlight_id = found["id"]
    else:
        if (
            kit.count(
                sc,
                "SELECT COUNT(*) FROM so_highlights WHERE owner_id = ?",
                (me_["id"],),
            )
            >= 50
        ):
            raise ValueError("You have 50 highlights: delete one first.")
        highlight_id = new_id()
        sc.execute(
            "INSERT INTO so_highlights (id, owner_id, title, created_at) VALUES (?, ?, ?, ?)",
            (highlight_id, me_["id"], title, moment),
        )
    for story_id in story_ids:
        sc.execute(
            "INSERT OR IGNORE INTO so_highlight_items (highlight_id, story_id, added_at) VALUES (?, ?, ?)",
            (highlight_id, story_id, moment),
        )
    sc.commit()
    return {"id": highlight_id, "title": title}


def delete_highlight(
    sc: sqlite3.Connection, me_: dict[str, Any], highlight_id: str
) -> None:
    found = _highlight(sc, me_, highlight_id)
    sc.execute("DELETE FROM so_highlight_items WHERE highlight_id = ?", (found["id"],))
    sc.execute("DELETE FROM so_highlights WHERE id = ?", (found["id"],))
    sc.commit()


def highlights(
    sc: sqlite3.Connection, viewer: dict[str, Any], owner: dict[str, Any]
) -> list[dict[str, Any]]:
    """The owner's highlights with the stories in them this viewer may see (friends; close friends' ones only for
    them). A highlight with nothing the viewer may see is left out."""
    out = []
    for item in rows(
        sc,
        "SELECT * FROM so_highlights WHERE owner_id = ? ORDER BY created_at",
        (owner["id"],),
    ):
        shown = []
        for story in rows(
            sc,
            """SELECT s.* FROM so_highlight_items i JOIN so_stories s ON s.id = i.story_id
            WHERE i.highlight_id = ? ORDER BY s.created_at""",
            (item["id"],),
        ):
            if can_see_story(sc, viewer, story):
                shown.append(_story_view(sc, viewer, story, True))
        if shown:
            cover = next((s["photo"] for s in shown if s["photo"]), "")
            out.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "cover": cover,
                    "color": shown[0]["color"],
                    "stories": shown,
                    "mine": owner["id"] == viewer["id"],
                }
            )
    return out


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
    # An adult outside the household writes to a child or a teen only from their approved contacts, even when the
    # two became friends earlier; teens write to each other.
    if not (contact_ok(sc, other, me_) and contact_ok(sc, me_, other)):
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
    longer visible to them or long sold, the ordinary rule applies again. A Dating match's chat has its own rule
    (open while the match lasts, :func:`aira.suite.social.dating.chat_open`)."""
    if chat.get("context") == "dating":
        from aira.suite.social import dating

        return dating.chat_open(sc, me_, other, chat)
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
        WHERE m.account_id = ? AND m.left_at IS NULL AND c.context != 'dating' ORDER BY c.updated_at DESC LIMIT 200""",
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
        waiting = bool(
            item["hidden_at"]
            and item["author_id"] == me_["id"]
            and held(sc, "message", item["id"])
        )  # held for a safety check: its author sees it waiting, nobody else sees it
        if item["author_id"] in hidden or (item["hidden_at"] and not waiting):
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
                "photo": ""
                if item["deleted_at"]
                else photos.display(item["photo"], sc=sc),
                "deleted": bool(item["deleted_at"]),
                "created_at": item["created_at"],
                "mine": mine,
                "seen": bool(seen_by) if mine else None,
                "held": waiting,
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
    written = kit.text(raw.get("text"), "a message", max_len=4000, multiline=True)
    pictures = _photos(
        [raw["photo"]] if raw.get("photo") else [], limit=1, sc=sc, owner=me_["id"]
    )
    if not written and not pictures:
        raise ValueError("Write a message.")
    # A chat with a child or a teen in it: flagged words, contact details or a move to another app wait for a
    # moderator instead of being delivered.
    young_ids = [
        m["account_id"]
        for m in rows(
            sc,
            """SELECT c.account_id FROM so_chat_members c JOIN so_accounts a ON a.id = c.account_id
            WHERE c.chat_id = ? AND a.age_group IN ('child', 'teen')""",
            (chat["id"],),
        )
    ]
    verdict = screen(sc, written, involves_young=bool(young_ids) or young(me_))
    text = verdict.text
    rate(sc, me_, "message")
    message_id = new_id()
    moment = stamp()
    on_hold = verdict.action == "hold"
    sc.execute(
        "INSERT INTO so_messages (id, chat_id, author_id, text, photo, created_at, updated_at, hidden_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            chat["id"],
            me_["id"],
            text,
            pictures[0] if pictures else "",
            moment,
            moment,
            moment if on_hold else None,
        ),
    )
    if on_hold:
        _hold(sc, "message", message_id, me_, verdict, young_ids=young_ids)
        sc.execute(
            "UPDATE so_chat_members SET last_read_at = ? WHERE chat_id = ? AND account_id = ?",
            (moment, chat["id"], me_["id"]),
        )
        sc.commit()
        support = (
            moderation.crisis_reply(young(me_))
            if "self_harm" in verdict.categories
            else ""
        )
        return {
            "message": {
                "id": message_id,
                "text": text,
                "photo": photos.display(pictures[0], sc=sc) if pictures else "",
                "created_at": moment,
                "mine": True,
                "held": True,
            },
            "held": True,
            "notice": "This message is waiting for a safety check before it is delivered.",
            "support": support,
        }
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
            "photo": photos.display(pictures[0], sc=sc) if pictures else "",
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
        # Dating's chats count in Dating's own badge, never on Social's
        "SELECT * FROM so_chat_members WHERE account_id = ? AND left_at IS NULL AND muted = 0 AND chat_id NOT IN (SELECT id FROM so_chats WHERE context = 'dating')",
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
        "INSERT INTO so_groups (id, name, about, visibility, created_by, created_at, photo) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
            _picture(raw.get("photo"), sc, me_["id"], "the group's picture link"),
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
        "photo": photos.display(group.get("photo"), sc=sc),
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
    if "photo" in raw:
        values["photo"] = _picture(
            raw["photo"], sc, me_["id"], "the group's picture link"
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
    if "photo" in raw:
        values["photo"] = _picture(
            raw["photo"], sc, me_["id"], "the page's picture link"
        )
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
        "INSERT INTO so_pages (id, name, about, category, link, created_by, created_at, photo) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
            _picture(raw.get("photo"), sc, me_["id"], "the page's picture link"),
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
        "photo": photos.display(found.get("photo"), sc=sc),
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
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    raw: dict[str, Any],
    *,
    dating_profiles: bool = False,
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
        found = row(sc, "SELECT * FROM so_stories WHERE id = ?", (target_id,))
        if not found or not can_see_story(sc, me_, found):
            raise LookupError("That story was not found.")
    elif target_type == "listing":
        from aira.suite.social import market

        market.visible(sc, me_, target_id)
    else:
        visible_account(sc, me_, target_id)
    existing = row(
        sc,
        "SELECT id FROM so_reports WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND status = 'open'",
        (me_["id"], target_type, target_id),
    )
    if existing:
        return {"reported": True, "report_id": existing["id"]}
    rate(sc, me_, "report")
    report_id = new_id()
    sc.execute(
        "INSERT INTO so_reports (id, reporter_id, target_type, target_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (report_id, me_["id"], target_type, target_id, reason, stamp()),
    )
    # The evidence is copied now: the chat may be deleted or a match ended later (moderators only read it).
    if target_type == "message":
        found = row(sc, "SELECT chat_id FROM so_messages WHERE id = ?", (target_id,))
        if found:
            from aira.suite.social import dating

            add_evidence(
                sc,
                report_id,
                "chat",
                {
                    "chat_id": found["chat_id"],
                    "transcript": dating.transcript(sc, found["chat_id"])[-200:],
                },
            )
    elif target_type == "account":
        from aira.suite.social import dating

        between = dating.evidence_between(
            sc, me_["id"], target_id, profiles=dating_profiles
        )
        if between:
            add_evidence(sc, report_id, "dating", between)
    sc.commit()
    return {"reported": True, "report_id": report_id}


def add_evidence(
    sc: sqlite3.Connection, report_id: str, kind: str, data: dict[str, Any]
) -> None:
    sc.execute(
        "INSERT INTO so_evidence (id, report_id, kind, data, created_at, purge_after) VALUES (?, ?, ?, ?, ?, ?)",
        (
            new_id(),
            report_id,
            kind,
            store.dumps(data),
            stamp(),
            store.later(24 * store.RETENTION_DAYS),
        ),
    )


def evidence(
    sc: sqlite3.Connection, report_id: str, *, dating_too: bool = True
) -> list[dict[str, Any]]:
    """What was copied with a report (for a moderator). Dating evidence is left out unless ``dating_too``."""
    out = []
    for item in rows(
        sc,
        "SELECT * FROM so_evidence WHERE report_id = ? ORDER BY created_at",
        (report_id,),
    ):
        if item["kind"] == "dating" and not dating_too:
            out.append(
                {"kind": "dating", "withheld": True, "created_at": item["created_at"]}
            )
            continue
        out.append(
            {
                "kind": item["kind"],
                "data": store.loads(item["data"], {}),
                "created_at": item["created_at"],
                # the person was erased: this is kept only until the report is closed
                "legal_hold": bool(item.get("legal_hold")),
            }
        )
    return out


def purge_kept(sc: sqlite3.Connection) -> dict[str, int]:
    """Social's background task: ended Dating chats past their retention, and evidence of closed reports past it.

    What a legal hold kept of an erased person (:func:`erase_account`) goes as soon as no open report needs it:
    the evidence, the ended chats, the reports about them and the photos the evidence showed."""
    moment = stamp()
    ended = sc.execute(
        """DELETE FROM so_dating_ended WHERE (purge_after < ? AND legal_hold = '') OR (legal_hold != '' AND NOT EXISTS
        (SELECT 1 FROM so_reports r WHERE r.status = 'open' AND r.target_type = 'account'
        AND ((r.reporter_id = so_dating_ended.a_id AND r.target_id = so_dating_ended.b_id)
        OR (r.reporter_id = so_dating_ended.b_id AND r.target_id = so_dating_ended.a_id))))""",
        (moment,),
    ).rowcount
    kept = sc.execute(
        """DELETE FROM so_evidence WHERE (purge_after < ? OR legal_hold != '') AND report_id NOT IN
        (SELECT id FROM so_reports WHERE status = 'open')""",
        (moment,),
    ).rowcount
    # closed reports by or about someone erased while they were open, and the photos only their evidence showed
    sc.execute(
        """DELETE FROM so_reports WHERE status != 'open' AND (
        (reporter_id != '' AND reporter_id NOT IN (SELECT id FROM so_accounts))
        OR (target_type = 'account' AND target_id NOT IN (SELECT id FROM so_accounts)))"""
    )
    held = _held_media(sc)
    for item in rows(
        sc, "SELECT id FROM so_media WHERE owner_id NOT IN (SELECT id FROM so_accounts)"
    ):
        if item["id"] not in held:
            photos.delete(sc, item["id"])
    sc.commit()
    return {"ended_chats": max(ended, 0), "evidence": max(kept, 0)}


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
    if not report_row["reporter_id"]:
        kept = held(sc, report_row["target_type"], report_row["target_id"])
        if kept and home in str(kept["homes"] or "").split("\n"):
            return True  # held by the automatic check: the young person's household moderates it too
    target = _target(sc, report_row["target_type"], report_row["target_id"]) or {}
    return home in (
        _home_of(sc, report_row["reporter_id"]),
        _home_of(sc, _author_of(report_row["target_type"], target)),
    )


def reports(
    sc: sqlite3.Connection,
    *,
    status: str = "open",
    home: str | None = None,
    viewer_id: str = "",
) -> list[dict[str, Any]]:
    """The moderation queue, with a preview of each reported thing and the evidence copied when it was filed (for
    a moderator only). Writing the automatic check held back is here too (``held``, no reporter). A household
    owner (``home``) sees Dating evidence only of reports they filed themselves (``viewer_id``): the rest stays
    with the platform's moderators."""
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
                "automatic": not item["reporter_id"],
                "held": bool(held(sc, item["target_type"], item["target_id"])),
                "evidence": evidence(
                    sc,
                    item["id"],
                    dating_too=home is None
                    or bool(viewer_id and item["reporter_id"] == viewer_id),
                ),
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
    if action == "release":
        action = "dismiss"  # writing held by the automatic check: deliver it after all
    action = kit.choice(action, "the action", ("hide", "suspend", "dismiss"))
    moment = stamp()
    target = _target(sc, found["target_type"], found["target_id"]) or {}
    kept = held(sc, found["target_type"], found["target_id"])
    if kept:
        sc.execute(
            "DELETE FROM so_held WHERE target_type = ? AND target_id = ?",
            (found["target_type"], found["target_id"]),
        )
        if action == "dismiss":
            table = {
                "message": "so_messages",
                "post": "so_posts",
                "comment": "so_comments",
            }.get(found["target_type"])
            if table:
                sc.execute(
                    f"UPDATE {table} SET hidden_at = NULL WHERE id = ?",  # noqa: S608 - fixed names
                    (found["target_id"],),
                )
                if found["target_type"] in ("message", "post"):
                    sc.execute(
                        f"UPDATE {table} SET updated_at = ? WHERE id = ?",  # noqa: S608 - fixed names
                        (moment, found["target_id"]),
                    )
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
            # Dating's chats are the person's own (dating.export), never in an organization's export of a member
            """SELECT id, chat_id, text, photo, created_at FROM so_messages WHERE author_id = ? AND deleted_at IS NULL
            AND chat_id NOT IN (SELECT id FROM so_chats WHERE context = 'dating') ORDER BY created_at""",
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
        "polls": rows(
            sc,
            "SELECT p.post_id, p.options, p.closes_at FROM so_polls p JOIN so_posts s ON s.id = p.post_id WHERE s.author_id = ?",
            (account_id,),
        ),
        "poll_votes": rows(
            sc,
            "SELECT post_id, choice, created_at FROM so_poll_votes WHERE account_id = ?",
            (account_id,),
        ),
        "saved": rows(
            sc,
            "SELECT post_id, collection_id, created_at FROM so_saves WHERE account_id = ?",
            (account_id,),
        ),
        "collections": rows(
            sc,
            "SELECT id, name, created_at FROM so_collections WHERE owner_id = ?",
            (account_id,),
        ),
        "close_friends": [
            r["friend_id"]
            for r in rows(
                sc,
                "SELECT friend_id FROM so_close_friends WHERE owner_id = ?",
                (account_id,),
            )
        ],
        "highlights": [
            {
                **item,
                "story_ids": [
                    r["story_id"]
                    for r in rows(
                        sc,
                        "SELECT story_id FROM so_highlight_items WHERE highlight_id = ?",
                        (item["id"],),
                    )
                ],
            }
            for item in rows(
                sc,
                "SELECT id, title, created_at FROM so_highlights WHERE owner_id = ?",
                (account_id,),
            )
        ],
        "photos": rows(
            sc,
            "SELECT id, mime, size, width, height, created_at FROM so_media WHERE owner_id = ? ORDER BY created_at",
            (account_id,),
        ),
    }


def erase_accounts(sc: sqlite3.Connection, account_ids: Iterable[str]) -> int:
    """Remove these people from Social: their profile, posts, comments, stories, messages, reactions,
    friendships, memberships and notifications. Groups, pages and chats they made stay for the others. Evidence of
    an open report about them, or that they filed, stays under a legal hold (:func:`erase_account`)."""
    ids = [str(a) for a in account_ids if a]
    for account_id in ids:
        erase_account(sc, account_id)
    return len(ids)


LEGAL_HOLD = "erased_person"  # so_evidence / so_dating_ended.legal_hold: kept for an open report, purged when it closes


def _open_reports_of(sc: sqlite3.Connection, account_id: str) -> list[str]:
    """Open reports this person filed, or about them, or about a message they wrote."""
    return [
        r["id"]
        for r in rows(
            sc,
            """SELECT id FROM so_reports WHERE status = 'open' AND (reporter_id = ?
            OR (target_type = 'account' AND target_id = ?)
            OR (target_type = 'message' AND target_id IN (SELECT id FROM so_messages WHERE author_id = ?))
            OR id IN (SELECT report_id FROM so_evidence WHERE kind IN ('dating', 'chat') AND data LIKE ?))""",
            (account_id, account_id, account_id, f'%"{account_id}"%'),
        )
    ]


def _open_between(sc: sqlite3.Connection, a_id: str, b_id: str) -> bool:
    return bool(
        row(
            sc,
            """SELECT 1 AS x FROM so_reports WHERE status = 'open' AND target_type = 'account'
            AND ((reporter_id = ? AND target_id = ?) OR (reporter_id = ? AND target_id = ?)) LIMIT 1""",
            (a_id, b_id, b_id, a_id),
        )
    )


def erase_account(sc: sqlite3.Connection, account_id: str) -> dict[str, int]:
    """Erase one person from Social and Dating; what went, by table.

    **Legal hold.** A moderation report that is still open keeps its evidence: the report itself, the chat and the
    dating profiles copied with it (``so_evidence``), the ended matches' chats between the two (``so_dating_ended``)
    and the photos those show. They are marked ``legal_hold``, counted under ``held:<table>``, and Social's
    background task (:func:`purge_kept`) deletes them once the report is closed. Closed reports by or about the
    person go now."""
    from aira.suite.social import dating

    counts: dict[str, int] = {}

    def add(table: str, number: int) -> None:
        if number and number > 0:
            counts[table] = counts.get(table, 0) + number

    if not row(sc, "SELECT id FROM so_accounts WHERE id = ?", (account_id,)):
        return counts
    dating.ensure(sc)
    held_reports = _open_reports_of(sc, account_id)
    for table, where, params in (
        ("so_dating_profiles", "account_id = ?", (account_id,)),
        ("so_dating_matches", "a_id = ? OR b_id = ?", (account_id, account_id)),
        ("so_dating_swipes", "actor_id = ? OR target_id = ?", (account_id, account_id)),
        ("so_dating_daily", "account_id = ?", (account_id,)),
    ):
        found = row(sc, f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)  # noqa: S608 - fixed names
        add(table, int(found["n"]) if found else 0)
    dating.erase(
        sc, [account_id]
    )  # their dating profile, likes and matches (each match's chat is copied first)
    if held_reports:
        marks = ", ".join("?" for _ in held_reports)
        add(
            "held:so_evidence",
            sc.execute(
                f"UPDATE so_evidence SET legal_hold = ? WHERE report_id IN ({marks})",  # noqa: S608
                (LEGAL_HOLD, *held_reports),
            ).rowcount,
        )
        add("held:so_reports", len(held_reports))
    for ended in rows(
        sc,
        "SELECT id, a_id, b_id FROM so_dating_ended WHERE a_id = ? OR b_id = ?",
        (account_id, account_id),
    ):
        if _open_between(sc, ended["a_id"], ended["b_id"]):
            sc.execute(
                "UPDATE so_dating_ended SET legal_hold = ? WHERE id = ?",
                (LEGAL_HOLD, ended["id"]),
            )
            add("held:so_dating_ended", 1)
        else:
            sc.execute("DELETE FROM so_dating_ended WHERE id = ?", (ended["id"],))
            add("so_dating_ended", 1)
    posts = [
        r["id"]
        for r in rows(sc, "SELECT id FROM so_posts WHERE author_id = ?", (account_id,))
    ]
    for post_id in posts:
        for table in (
            "so_post_tags",
            "so_reactions",
            "so_comments",
            "so_polls",
            "so_poll_votes",
            "so_saves",
        ):
            sc.execute(f"DELETE FROM {table} WHERE post_id = ?", (post_id,))  # noqa: S608 - fixed names
    stories = [
        r["id"]
        for r in rows(
            sc, "SELECT id FROM so_stories WHERE author_id = ?", (account_id,)
        )
    ]
    for story_id in stories:
        sc.execute("DELETE FROM so_story_views WHERE story_id = ?", (story_id,))
        sc.execute("DELETE FROM so_story_reactions WHERE story_id = ?", (story_id,))
        sc.execute("DELETE FROM so_highlight_items WHERE story_id = ?", (story_id,))
    # the photo files they uploaded, from the disk too, except those held evidence shows
    held = _held_media(sc)
    for item in rows(sc, "SELECT id FROM so_media WHERE owner_id = ?", (account_id,)):
        if item["id"] in held:
            add("held:so_media", 1)
            continue
        photos.delete(sc, item["id"])
        add("so_media", 1)
    for statement in (
        "DELETE FROM so_comment_likes WHERE account_id = ? OR comment_id IN (SELECT id FROM so_comments WHERE author_id = ?)",
        "DELETE FROM so_poll_votes WHERE account_id = ?",
        "DELETE FROM so_saves WHERE account_id = ?",
        "DELETE FROM so_collections WHERE owner_id = ?",
        "DELETE FROM so_close_friends WHERE owner_id = ? OR friend_id = ?",
        "DELETE FROM so_highlight_items WHERE highlight_id IN (SELECT id FROM so_highlights WHERE owner_id = ?)",
        "DELETE FROM so_highlights WHERE owner_id = ?",
        "DELETE FROM so_story_reactions WHERE account_id = ?",
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
        # an open report stays for its moderator (legal hold); a closed one by or about them goes
        "DELETE FROM so_reports WHERE status != 'open' AND (reporter_id = ? OR (target_type = 'account' AND target_id = ?))",
        "DELETE FROM so_rate WHERE account_id = ?",
        "DELETE FROM so_listing_saves WHERE account_id = ? OR listing_id IN (SELECT id FROM so_listings WHERE seller_id = ?)",
        "DELETE FROM so_listings WHERE seller_id = ?",
        "DELETE FROM so_guardian WHERE child_id = ? OR other_id = ?",
        "DELETE FROM so_approved_contacts WHERE young_id = ? OR contact_id = ?",
        "DELETE FROM so_held WHERE author_id = ?",
        "DELETE FROM so_accounts WHERE id = ?",
    ):
        table = statement.split()[2]
        add(table, sc.execute(statement, (account_id,) * statement.count("?")).rowcount)
    add(
        "so_evidence",
        sc.execute(
            "DELETE FROM so_evidence WHERE report_id NOT IN (SELECT id FROM so_reports)"
        ).rowcount,
    )
    sc.commit()
    return counts


def _held_media(sc: sqlite3.Connection) -> set[str]:
    """Photos that held evidence shows (``media:<id>`` in its copied profiles and chats)."""
    found: set[str] = set()
    for text in [
        *(
            r["data"]
            for r in rows(sc, "SELECT data FROM so_evidence WHERE legal_hold != ''")
        ),
        *(
            f"{r['transcript']} {r['profiles']}"
            for r in rows(
                sc,
                "SELECT transcript, profiles FROM so_dating_ended WHERE legal_hold != ''",
            )
        ),
    ]:
        found.update(re.findall(r"media:([0-9a-f]{32})", str(text or "")))
    return found


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
        AND m.created_at > c.last_read_at AND m.deleted_at IS NULL AND m.hidden_at IS NULL
        AND m.chat_id NOT IN (SELECT id FROM so_chats WHERE context = 'dating') ORDER BY m.created_at LIMIT 5""",
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


def public_profile(
    sc: sqlite3.Connection, account_id: str, token: str = ""
) -> dict[str, Any]:
    """The shared profile page; uploaded photos load through the link's own address (``token``)."""
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
                "photos": photos.display_list(
                    store.loads(item["photos"], []), token=token
                ),
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
            "avatar": photos.display(acct["avatar"], token=token),
            "cover": photos.display(acct.get("cover"), token=token),
            "links": acct.get("links") or [],
            "bio": acct["bio"],
            "headline": acct["headline"],
            "open_to_work": acct["open_to_work"],
            "skills": acct["skills"],
            "experience": acct["experience"],
        },
        "posts": posts,
    }


def public_profile_photo(
    sc: sqlite3.Connection, account_id: str, photo_id: str
) -> bool:
    """Whether the shared profile page shows this uploaded photo (its picture, cover or a public post's photo)."""
    page = public_profile(sc, account_id)
    if not page.get("available"):
        return False
    acct = account(sc, account_id)
    ref = f"media:{photo_id}"
    if ref in (acct["avatar"], acct.get("cover")):
        return True
    return bool(
        row(
            sc,
            """SELECT 1 AS x FROM so_posts WHERE author_id = ? AND audience = 'public' AND group_id IS NULL AND page_id IS NULL
            AND deleted_at IS NULL AND hidden_at IS NULL AND photos LIKE ?""",
            (acct["id"], f'%"{ref}"%'),
        )
    )
